"""16장 표: 장비/포트/이웃/FDB/ARP/Route/PoE 조회 및 Role 수동 변경 API."""
from __future__ import annotations

import ipaddress
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.classification.device_role import DEVICE_ROLES
from app.classification.reclassify import reclassify_all_devices
from app.config_history import record_config_change
from app.db import db_session_dependency
from app.diagnostics import get_source_ip, get_source_mac, parse_route_hops, run_diagnostic
from app.models import (
    ArpEntry,
    DeviceInterface,
    LldpNeighbor,
    MacFdb,
    NetworkDevice,
    PoePort,
    RouteEntry,
)
from app.schemas import (
    ArpOut,
    DeviceDetailOut,
    DeviceDiagnosticOut,
    DiagnosticHopOut,
    DeviceOut,
    FdbOut,
    InterfaceOut,
    L2PathEvidenceOut,
    L2SwitchEvidenceOut,
    NeighborOut,
    PoeOut,
    ReclassifyOut,
    RoleUpdateIn,
    RouteOut,
)
from app.topology.engine import recompute_topology_and_roles

router = APIRouter(tags=["devices"])


def _get_device_or_404(session: Session, device_id: int) -> NetworkDevice:
    device = session.get(NetworkDevice, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail=f"장비를 찾을 수 없습니다: {device_id}")
    return device


@router.get("/devices", response_model=list[DeviceOut])
def list_devices(
    device_type: Optional[str] = None,
    device_role: Optional[str] = None,
    status: Optional[str] = None,
    q: Optional[str] = None,
    session: Session = Depends(db_session_dependency),
):
    stmt = select(NetworkDevice)
    if device_type:
        stmt = stmt.where(NetworkDevice.device_type == device_type)
    if device_role:
        stmt = stmt.where(NetworkDevice.device_role == device_role)
    if status:
        stmt = stmt.where(NetworkDevice.status == status)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(NetworkDevice.hostname.ilike(like), NetworkDevice.management_ip.ilike(like)))
    stmt = stmt.order_by(NetworkDevice.id)
    return session.scalars(stmt).all()


@router.post("/devices/reclassify", response_model=ReclassifyOut)
def reclassify_devices(session: Session = Depends(db_session_dependency)):
    """[KOS20260921] 분류 규칙을 고쳐도 이미 저장된 장비는 다음 Discovery
    재실행 전까지 예전 결과를 그대로 보여준다. 재탐색 없이 저장된 근거만으로
    즉시 재분류한다(reclassify.py 모듈 docstring 참고 - PC/카메라의 무자격
    프로브 근거는 저장되지 않으므로 완전한 재현은 아니며, 그런 장비는 여전히
    재탐색이 필요할 수 있다)."""
    changed = reclassify_all_devices(session)
    session.commit()
    recompute_topology_and_roles(session)
    return ReclassifyOut(changed=changed)


@router.get("/devices/{device_id}", response_model=DeviceDetailOut)
def get_device(device_id: int, session: Session = Depends(db_session_dependency)):
    return _get_device_or_404(session, device_id)


@router.post("/devices/{device_id}/diagnostics/{kind}", response_model=DeviceDiagnosticOut)
def diagnose_device(device_id: int, kind: str, protocol: str | None = None, session: Session = Depends(db_session_dependency)):
    if kind not in {"ping", "traceroute"}:
        raise HTTPException(status_code=404, detail=f"지원하지 않는 진단: {kind}")
    device = _get_device_or_404(session, device_id)
    if not device.management_ip:
        raise HTTPException(status_code=400, detail="관리 IP가 없어 진단할 수 없습니다.")
    try:
        target = str(ipaddress.ip_address(device.management_ip))
    except ValueError:
        raise HTTPException(status_code=400, detail="유효한 관리 IP가 없어 진단할 수 없습니다.") from None
    effective_protocol = (protocol or ("ICMP" if kind == "ping" else "UDP")).upper()
    if effective_protocol not in {"TCP", "UDP", "ICMP"} or (kind == "ping" and effective_protocol != "ICMP"):
        raise HTTPException(status_code=400, detail="지원하지 않는 진단 프로토콜입니다.")
    command, success, output = run_diagnostic(target, kind, effective_protocol)
    route_hops = parse_route_hops(command, output) if kind == "traceroute" else []
    hop_ips = {ip for _, ip in route_hops if ip}
    # [KOS20260922] 경로 추적은 홉의 IP만 알려주므로 관리 IP가 일치하는
    # 인벤토리 장비에 한해 이름을 붙인다. 응답 없는 홉의 장비는 추정하지 않는다.
    matching_devices = session.scalars(select(NetworkDevice).where(NetworkDevice.management_ip.in_(hop_ips))).all() if hop_ips else []
    devices_by_ip = {device.management_ip: device for device in matching_devices}
    hops = [
        DiagnosticHopOut(
            hop=number,
            ip=ip,
            device_id=devices_by_ip[ip].id if ip in devices_by_ip else None,
            hostname=devices_by_ip[ip].hostname if ip in devices_by_ip else None,
            device_role=devices_by_ip[ip].device_role if ip in devices_by_ip else None,
        )
        for number, ip in route_hops
    ]
    return DeviceDiagnosticOut(
        target=target,
        source_ip=get_source_ip(target) if kind == "traceroute" else None,
        command=command,
        protocol=effective_protocol,
        success=success,
        output=output,
        hops=hops,
    )


@router.get("/devices/{device_id}/diagnostics/l2-path", response_model=L2PathEvidenceOut)
def get_l2_path_evidence(device_id: int, session: Session = Depends(db_session_dependency)):
    device = _get_device_or_404(session, device_id)
    if not device.management_ip:
        raise HTTPException(status_code=400, detail="관리 IP가 없어 L2 경로를 확인할 수 없습니다.")
    try:
        target_ip = str(ipaddress.ip_address(device.management_ip))
    except ValueError:
        raise HTTPException(status_code=400, detail="유효한 관리 IP가 없어 L2 경로를 확인할 수 없습니다.") from None

    source_ip = get_source_ip(target_ip)
    source_mac = get_source_mac(target_ip)
    target_mac = device.primary_mac.lower() if device.primary_mac else None
    result = L2PathEvidenceOut(source_ip=source_ip, source_mac=source_mac, target_ip=target_ip, target_mac=target_mac)
    if not source_mac or not target_mac:
        return result

    stmt = (
        select(MacFdb, NetworkDevice, DeviceInterface)
        .join(NetworkDevice, MacFdb.device_id == NetworkDevice.id)
        .outerjoin(DeviceInterface, MacFdb.interface_id == DeviceInterface.id)
        .where(func.lower(MacFdb.mac).in_([source_mac, target_mac]))
    )
    groups: dict[tuple[int, int | None], dict] = {}
    for fdb, switch, interface in session.execute(stmt):
        key = (switch.id, fdb.vlan)
        group = groups.setdefault(key, {"switch": switch, "source": [], "target": []})
        side = "source" if fdb.mac.lower() == source_mac else "target"
        group[side].append((interface, fdb.last_seen_at))

    for (_, vlan), group in sorted(groups.items(), key=lambda item: (item[0][0], item[0][1] or -1)):
        source_rows = group["source"]
        target_rows = group["target"]
        source_ids = {interface.id for interface, _ in source_rows if interface}
        target_ids = {interface.id for interface, _ in target_rows if interface}
        # [KOS20260922] 같은 포트에서 두 MAC이 보이면 그 스위치를 경유했다고
        # 단정할 수 없다. 다른 포트여도 FDB만으로 전체 순서나 현재 경로는 확정할 수 없다.
        if not source_rows or not target_rows:
            relation = "ONE_SIDE"
        elif not source_ids or not target_ids:
            relation = "UNKNOWN_PORT"
        elif source_ids & target_ids:
            relation = "SAME_PORT"
        else:
            relation = "DIFFERENT_PORTS"
        result.switches.append(
            L2SwitchEvidenceOut(
                switch_id=group["switch"].id,
                hostname=group["switch"].hostname,
                sys_name=group["switch"].model if group["switch"].snmp_enabled else None,
                management_ip=group["switch"].management_ip,
                sys_descr=group["switch"].sys_descr,
                device_type=group["switch"].device_type,
                vlan=vlan,
                source_ports=sorted({interface.name or f"#{interface.if_index}" for interface, _ in source_rows if interface}),
                target_ports=sorted({interface.name or f"#{interface.if_index}" for interface, _ in target_rows if interface}),
                source_seen_at=max((seen_at for _, seen_at in source_rows), default=None),
                target_seen_at=max((seen_at for _, seen_at in target_rows), default=None),
                relation=relation,
            )
        )
    return result


@router.get("/devices/{device_id}/interfaces", response_model=list[InterfaceOut])
def get_device_interfaces(device_id: int, session: Session = Depends(db_session_dependency)):
    _get_device_or_404(session, device_id)
    stmt = select(DeviceInterface).where(DeviceInterface.device_id == device_id).order_by(DeviceInterface.if_index)
    return session.scalars(stmt).all()


@router.get("/devices/{device_id}/neighbors", response_model=list[NeighborOut])
def get_device_neighbors(device_id: int, session: Session = Depends(db_session_dependency)):
    _get_device_or_404(session, device_id)
    stmt = select(LldpNeighbor).where(LldpNeighbor.local_device_id == device_id)
    return session.scalars(stmt).all()


@router.get("/devices/{device_id}/mac-table", response_model=list[FdbOut])
def get_device_mac_table(device_id: int, session: Session = Depends(db_session_dependency)):
    _get_device_or_404(session, device_id)
    stmt = select(MacFdb).where(MacFdb.device_id == device_id)
    return session.scalars(stmt).all()


@router.get("/devices/{device_id}/arp", response_model=list[ArpOut])
def get_device_arp(device_id: int, session: Session = Depends(db_session_dependency)):
    _get_device_or_404(session, device_id)
    stmt = select(ArpEntry).where(ArpEntry.device_id == device_id)
    return session.scalars(stmt).all()


@router.get("/devices/{device_id}/routes", response_model=list[RouteOut])
def get_device_routes(device_id: int, session: Session = Depends(db_session_dependency)):
    _get_device_or_404(session, device_id)
    stmt = select(RouteEntry).where(RouteEntry.device_id == device_id)
    return session.scalars(stmt).all()


@router.get("/devices/{device_id}/poe", response_model=list[PoeOut])
def get_device_poe(device_id: int, session: Session = Depends(db_session_dependency)):
    _get_device_or_404(session, device_id)
    stmt = select(PoePort).where(PoePort.device_id == device_id)
    return session.scalars(stmt).all()


@router.patch("/devices/{device_id}/role", response_model=DeviceOut)
def update_device_role(device_id: int, payload: RoleUpdateIn, session: Session = Depends(db_session_dependency)):
    """9장 마지막 bullet / 2.1절: 수동 지정 Role은 자동 재계산이 덮어쓰지 않는다."""
    device = _get_device_or_404(session, device_id)
    if payload.device_role not in DEVICE_ROLES:
        raise HTTPException(status_code=400, detail=f"알 수 없는 device_role: {payload.device_role}")
    if payload.device_role != device.device_role:
        record_config_change(
            session,
            device_id=device.id,
            field_name="device_role",
            old_value=device.device_role,
            new_value=payload.device_role,
            source="MANUAL",
            performed_by=payload.performed_by,
        )
    device.device_role = payload.device_role
    device.role_source = "MANUAL"
    session.commit()
    session.refresh(device)
    return device
