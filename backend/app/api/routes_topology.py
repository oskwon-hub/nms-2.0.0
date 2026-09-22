"""16장: GET /api/topology - Graph Nodes/Links."""
from __future__ import annotations

import ipaddress
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import db_session_dependency
from app.credentials import resolve_ssh_credentials
from app.diagnostics import run_remote_ping
from app.models import DeviceInterface, NetworkDevice, NetworkLink
from app.schemas import LinkPingIn, LinkPingOut, ManualTopologyLinkIn, TopologyLinkOut, TopologyNodeOut, TopologyOut
from app.topology.engine import create_manual_link

router = APIRouter(tags=["topology"])


@router.post("/topology/links", response_model=TopologyLinkOut, status_code=201)
def create_topology_link(payload: ManualTopologyLinkIn, session: Session = Depends(db_session_dependency)):
    if payload.src_device_id == payload.dst_device_id:
        raise HTTPException(status_code=400, detail="같은 장비끼리는 링크를 추가할 수 없습니다.")
    for device_id in (payload.src_device_id, payload.dst_device_id):
        if session.get(NetworkDevice, device_id) is None:
            raise HTTPException(status_code=404, detail=f"장비를 찾을 수 없습니다: {device_id}")
    label = payload.label.strip()
    if not label:
        raise HTTPException(status_code=400, detail="링크 라벨을 입력해 주세요.")

    link = create_manual_link(session, payload.src_device_id, payload.dst_device_id)
    link.label = label
    session.commit()
    session.refresh(link)
    src_depth = session.get(NetworkDevice, link.src_device_id).discovery_depth
    dst_depth = session.get(NetworkDevice, link.dst_device_id).discovery_depth
    return TopologyLinkOut.model_validate(link).model_copy(
        update={"link_role": "PEER" if src_depth == dst_depth else "HIERARCHICAL"}
    )


@router.delete("/topology/links/{link_id}", status_code=204)
def delete_topology_link(link_id: int, session: Session = Depends(db_session_dependency)):
    link = session.get(NetworkLink, link_id)
    if link is None:
        raise HTTPException(status_code=404, detail=f"링크를 찾을 수 없습니다: {link_id}")
    session.delete(link)
    session.commit()


@router.post("/topology/links/{link_id}/ping", response_model=LinkPingOut)
def ping_topology_link(
    link_id: int,
    payload: LinkPingIn | None = None,
    session: Session = Depends(db_session_dependency),
):
    link = session.get(NetworkLink, link_id)
    if link is None:
        raise HTTPException(status_code=404, detail=f"링크를 찾을 수 없습니다: {link_id}")
    direction = (payload.direction if payload is not None else "FORWARD").upper()
    if direction not in {"FORWARD", "REVERSE"}:
        raise HTTPException(status_code=400, detail="Ping 방향은 FORWARD 또는 REVERSE여야 합니다.")
    source_id, target_id = (
        (link.dst_device_id, link.src_device_id) if direction == "REVERSE"
        else (link.src_device_id, link.dst_device_id)
    )
    source = session.get(NetworkDevice, source_id)
    target = session.get(NetworkDevice, target_id)
    if source is None or target is None:
        raise HTTPException(status_code=404, detail="링크의 From 또는 To 장비를 찾을 수 없습니다.")
    if not source.management_ip or not target.management_ip:
        raise HTTPException(status_code=400, detail="From 또는 To 장비의 관리 IP가 없습니다.")
    try:
        source_ip = str(ipaddress.ip_address(source.management_ip))
        target_ip = str(ipaddress.ip_address(target.management_ip))
    except ValueError:
        raise HTTPException(status_code=400, detail="From 또는 To 장비의 관리 IP가 유효하지 않습니다.") from None

    if payload is not None and payload.password is not None:
        if payload.protocol is None or payload.username is None or payload.port is None:
            raise HTTPException(status_code=400, detail="직접 입력한 인증정보에는 프로토콜, ID, Password, 포트가 모두 필요합니다.")
        protocol = payload.protocol.upper()
        if protocol not in {"SSH", "TELNET"}:
            raise HTTPException(status_code=400, detail="접속 프로토콜은 SSH 또는 TELNET이어야 합니다.")
        username = payload.username.strip()
        if not username:
            raise HTTPException(status_code=400, detail="CLI 사용자명을 입력해 주세요.")
        credentials = [(username, payload.password, payload.port, protocol)]
    else:
        credentials = resolve_ssh_credentials(session, source)
        # Password를 비워 장비별 저장 Credential을 사용할 때도 화면에서 고른
        # 프로토콜/포트는 적용한다. NSH이지만 Telnet이 닫힌 현장 예외를 SSH로
        # 전환할 수 있고, 저장된 두 Prefix 암호 후보는 그대로 순서대로 시도한다.
        if payload is not None and payload.protocol is not None:
            protocol = payload.protocol.upper()
            if protocol not in {"SSH", "TELNET"}:
                raise HTTPException(status_code=400, detail="접속 프로토콜은 SSH 또는 TELNET이어야 합니다.")
            if payload.port is None:
                raise HTTPException(status_code=400, detail="접속 포트를 입력해 주세요.")
            override_username = payload.username.strip() if payload.username else None
            credentials = [
                (override_username or username, password, payload.port, protocol)
                for username, password, _port, _protocol in credentials
            ]
    if not credentials:
        return LinkPingOut(
            link_id=link.id,
            from_device_id=source.id,
            from_ip=source_ip,
            to_device_id=target.id,
            to_ip=target_ip,
            supported=False,
            success=False,
            protocol=None,
            output="From 장비의 Credential Profile에 CLI 사용자명과 암호가 설정되지 않았습니다.",
        )

    result = None
    for username, password, port, protocol in credentials:
        result = run_remote_ping(
            source_ip,
            target_ip,
            username,
            password,
            port,
            " ".join(filter(None, (source.sys_descr, source.model, source.device_type))),
            protocol,
        )
        # 인증/채널 오류일 때만 다음 기본 Credential 후보를 시도한다. 정상 실행된
        # Ping의 대상 미응답은 Credential 문제가 아니므로 그대로 결과를 반환한다.
        if not result[2].startswith(("SSH 접속/실행 실패", "TELNET 접속/실행 실패")):
            break
    assert result is not None
    command, success, output, loss, rtt_min, rtt_avg, rtt_max = result
    return LinkPingOut(
        link_id=link.id,
        from_device_id=source.id,
        from_ip=source_ip,
        to_device_id=target.id,
        to_ip=target_ip,
        supported=True,
        success=success,
        protocol=protocol,
        command=command,
        packet_loss_percent=loss,
        rtt_min_ms=rtt_min,
        rtt_avg_ms=rtt_avg,
        rtt_max_ms=rtt_max,
        output=output,
    )


@router.get("/topology", response_model=TopologyOut)
def get_topology(session: Session = Depends(db_session_dependency)):
    devices = session.scalars(select(NetworkDevice)).all()
    links = session.scalars(select(NetworkLink)).all()
    depth_by_id = {d.id: d.discovery_depth for d in devices}
    # [KOS20260921] Topology 화면에서 Link 레이블을 "근거(Source)+Confidence" 대신
    # "포트no -> 포트no"로 보여 달라는 요청 - src/dst_interface_id는 있지만 이름이
    # 없으므로, 링크에 걸린 인터페이스만 한 번에 조회해 이름을 채워 넣는다.
    referenced_iface_ids = {
        iid for link in links for iid in (link.src_interface_id, link.dst_interface_id) if iid is not None
    }
    iface_name_by_id: dict[int, str] = {}
    # [KOS20260922] STP로 Blocking 처리된 링크를 Topology에서 구분해 보여주기
    # 위해 포트 이름과 함께 dot1dStpPortState도 같이 채운다.
    iface_stp_state_by_id: dict[int, Optional[str]] = {}
    if referenced_iface_ids:
        for iface in session.scalars(select(DeviceInterface).where(DeviceInterface.id.in_(referenced_iface_ids))):
            iface_name_by_id[iface.id] = iface.name or f"if{iface.if_index}"
            iface_stp_state_by_id[iface.id] = iface.stp_state

    links_out = []
    for link in links:
        src_depth = depth_by_id.get(link.src_device_id)
        dst_depth = depth_by_id.get(link.dst_device_id)
        # [KOS20260921] 두 장비의 depth가 다르면 계층 간 연결(한쪽에서는 Uplink,
        # 반대쪽에서는 Downlink)이고, 같으면 Peer/이중화 연결이다.
        link_role = "PEER" if src_depth is None or dst_depth is None or src_depth == dst_depth else "HIERARCHICAL"
        link_out = TopologyLinkOut.model_validate(link)
        links_out.append(
            link_out.model_copy(
                update={
                    "link_role": link_role,
                    "src_port": iface_name_by_id.get(link.src_interface_id) if link.src_interface_id else None,
                    "dst_port": iface_name_by_id.get(link.dst_interface_id) if link.dst_interface_id else None,
                    "src_stp_state": iface_stp_state_by_id.get(link.src_interface_id) if link.src_interface_id else None,
                    "dst_stp_state": iface_stp_state_by_id.get(link.dst_interface_id) if link.dst_interface_id else None,
                }
            )
        )

    return TopologyOut(
        # [KOS20260922] hostname에는 DNS 등의 보조 이름도 들어갈 수 있다. 현재
        # Discovery는 SNMP sysName을 model에 보관하므로 라벨용으로 분리해 보낸다.
        nodes=[TopologyNodeOut.model_validate(d).model_copy(update={"sys_name": d.model if d.snmp_enabled else None}) for d in devices],
        links=links_out,
    )
