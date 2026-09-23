"""두 Endpoint MAC을 스위치 FDB에서 대조해 L2 경유 후보를 만든다."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import DeviceInterface, MacFdb, NetworkDevice
from app.schemas import L2PathEvidenceOut, L2SwitchEvidenceOut


def build_l2_path_evidence(
    session: Session,
    source_ip: str | None,
    source_mac: str | None,
    target_ip: str,
    target_mac: str | None,
) -> L2PathEvidenceOut:
    source_mac = source_mac.lower() if source_mac else None
    target_mac = target_mac.lower() if target_mac else None
    result = L2PathEvidenceOut(
        source_ip=source_ip,
        source_mac=source_mac,
        target_ip=target_ip,
        target_mac=target_mac,
    )
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
