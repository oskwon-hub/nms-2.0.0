"""12장: 토폴로지 및 Link Evidence 설계.

모든 물리/논리 연결은 network_link로 저장하고 network_link_evidence에 근거를
누적한다. parent_device_id 하나로 원본 관계를 표현하지 않으므로(2.1절 원칙),
Ring/Stack/MLAG/Dual-homing도 Graph 구조로 자연스럽게 표현된다.
"""
from __future__ import annotations

import datetime as dt
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.classification.device_role import (
    INFRASTRUCTURE_TYPES,
    RoleEvidence,
    _core_score,
    classify_device_role,
    extract_floor_label,
)
from app.models import (
    ArpEntry,
    DeviceInterface,
    LldpNeighbor,
    MacFdb,
    NetworkDevice,
    NetworkLink,
    NetworkLinkEvidence,
    RouteEntry,
    utcnow,
)

# 12장 표: Evidence 기본 Confidence
CONFIDENCE_LLDP = 100
CONFIDENCE_CDP = 95
CONFIDENCE_LLDP_FDB = 100
CONFIDENCE_FDB_ARP = 80
CONFIDENCE_FDB_ONLY = 65
CONFIDENCE_ARP_ONLY = 40
CONFIDENCE_IP_SCAN = 20
CONFIDENCE_MANUAL = 100
# [KOS20260922] dot1dStpPortDesignatedBridge 기반 링크 - LLDP보다는 신뢰도가
# 낮다(상대 포트 번호까지는 확정하지 못해 dst_interface_id를 채우지 않음)지만,
# 실제 프로토콜 상태(STP)에 기반하므로 ARP 상관관계보다는 신뢰도가 높다.
CONFIDENCE_STP_DESIGNATED_BRIDGE = 70

SWITCH_TYPES = {"L2_SWITCH", "L2_POE_SWITCH", "L3_SWITCH", "L3_POE_SWITCH"}


def _get_or_create_link(
    session: Session,
    src_device_id: int,
    src_interface_id: int | None,
    dst_device_id: int,
    dst_interface_id: int | None,
    source: str,
    confidence: int,
) -> NetworkLink:
    """동일 두 장비 간 링크가 이미 있으면 재사용하고, 더 신뢰도 높은 Evidence가
    오면 confidence/source를 갱신한다."""
    if src_device_id > dst_device_id:
        src_device_id, dst_device_id = dst_device_id, src_device_id
        src_interface_id, dst_interface_id = dst_interface_id, src_interface_id

    # [KOS20260921] LLDP는 두 장비 각각을 따로 처리하며 같은 물리 링크에 대해
    # infer_lldp_links()가 두 번 호출된다 - 각 호출은 "자기 로컬 포트"는 알지만
    # "상대 포트"는 이름/ifIndex 매칭에 실패해 모를 수 있다(NULL). 같은 장비 쌍에서
    # 이미 알려진 포트와 모순되지 않는(한쪽이 NULL이거나 값이 같은) 기존 링크를
    # 전부 찾아 하나로 합친다.
    # [KOS20260921] 처음엔 "정확히 일치 -> 없으면 첫 호환 후보 하나"만 봤는데,
    # 이미 서로 다른 NULL 패턴으로 갈라진 행 2개(예: (PortA, NULL)과 (NULL, PortB))가
    # 각자 계속 "자기 자신과 정확히 일치"하는 호출만 받으면 둘 다 영원히 완벽한
    # 매칭 대상이라 loose match까지 갈 일이 없어 다시는 합쳐지지 않는 문제가 있었다
    # (근거: NSH-3228/NHM-3228F 포트 25-25 링크가 재스캔을 반복해도 두 행으로
    # 계속 남아있던 실제 사례). 그래서 호환되는 후보를 전부 모아 가장 오래된
    # 행(id가 가장 작음)을 대표로 삼고, 나머지는 값을 흡수시킨 뒤 삭제해 합친다.
    candidates = list(
        session.scalars(
            select(NetworkLink).where(
                NetworkLink.src_device_id == src_device_id,
                NetworkLink.dst_device_id == dst_device_id,
            )
        )
    )

    def _compatible(c: NetworkLink) -> bool:
        src_ok = c.src_interface_id is None or src_interface_id is None or c.src_interface_id == src_interface_id
        dst_ok = c.dst_interface_id is None or dst_interface_id is None or c.dst_interface_id == dst_interface_id
        return src_ok and dst_ok

    compatible = sorted((c for c in candidates if _compatible(c)), key=lambda c: c.id)
    link = compatible[0] if compatible else None
    duplicates = compatible[1:]

    now = utcnow()
    if link is None:
        link = NetworkLink(
            src_device_id=src_device_id,
            src_interface_id=src_interface_id,
            dst_device_id=dst_device_id,
            dst_interface_id=dst_interface_id,
            source=source,
            confidence=confidence,
            status="UP",
            first_seen_at=now,
            last_seen_at=now,
        )
        session.add(link)
        session.flush()
    else:
        link.last_seen_at = now
        link.status = "UP"
        if src_interface_id is not None and link.src_interface_id is None:
            link.src_interface_id = src_interface_id
        if dst_interface_id is not None and link.dst_interface_id is None:
            link.dst_interface_id = dst_interface_id
        if confidence > link.confidence:
            link.confidence = confidence
            link.source = source

    for dup in duplicates:
        if dup.src_interface_id is not None and link.src_interface_id is None:
            link.src_interface_id = dup.src_interface_id
        if dup.dst_interface_id is not None and link.dst_interface_id is None:
            link.dst_interface_id = dup.dst_interface_id
        if dup.confidence > link.confidence:
            link.confidence = dup.confidence
            link.source = dup.source
        if dup.first_seen_at < link.first_seen_at:
            link.first_seen_at = dup.first_seen_at
        session.query(NetworkLinkEvidence).filter(NetworkLinkEvidence.link_id == dup.id).update(
            {NetworkLinkEvidence.link_id: link.id}, synchronize_session=False
        )
        session.delete(dup)
    if duplicates:
        session.flush()

    return link


def _add_evidence(session: Session, link: NetworkLink, evidence_type: str, confidence: int, raw: dict) -> None:
    session.add(
        NetworkLinkEvidence(
            link_id=link.id,
            evidence_type=evidence_type,
            raw_data=json.dumps(raw, ensure_ascii=False),
            confidence=confidence,
            observed_at=utcnow(),
        )
    )


def infer_lldp_links(session: Session, device: NetworkDevice) -> None:
    """해당 장비가 관측한 LLDP Neighbor를 이용해 network_link를 생성/갱신한다."""
    neighbors = session.scalars(select(LldpNeighbor).where(LldpNeighbor.local_device_id == device.id))
    for neighbor in neighbors:
        remote_device = None
        if neighbor.remote_chassis_id:
            remote_device = session.scalar(
                select(NetworkDevice).where(NetworkDevice.lldp_chassis_id == neighbor.remote_chassis_id)
            )
        if remote_device is None and neighbor.remote_mgmt_ip:
            remote_device = session.scalar(
                select(NetworkDevice).where(NetworkDevice.management_ip == neighbor.remote_mgmt_ip)
            )
        if remote_device is None or remote_device.id == device.id:
            continue

        remote_interface = None
        if neighbor.remote_port_id:
            remote_interface = session.scalar(
                select(DeviceInterface).where(
                    DeviceInterface.device_id == remote_device.id,
                    (DeviceInterface.name == neighbor.remote_port_id),
                )
            )
            if remote_interface is None:
                # [KOS20260921] lldpRemPortIdSubtype이 "local"인 장비는 포트 이름 대신
                # 원시 ifIndex 숫자를 remote_port_id로 보고한다. 이름 매칭이 실패하면
                # 숫자로 보고 ifIndex 매칭을 한 번 더 시도해 Topology 링크 레이블의
                # "?"(포트 미확인)를 줄인다.
                try:
                    remote_if_index = int(neighbor.remote_port_id)
                except ValueError:
                    remote_if_index = None
                if remote_if_index is not None:
                    remote_interface = session.scalar(
                        select(DeviceInterface).where(
                            DeviceInterface.device_id == remote_device.id,
                            DeviceInterface.if_index == remote_if_index,
                        )
                    )

        confidence = CONFIDENCE_CDP if neighbor.protocol == "CDP" else CONFIDENCE_LLDP
        link = _get_or_create_link(
            session,
            device.id,
            neighbor.local_interface_id,
            remote_device.id,
            remote_interface.id if remote_interface else None,
            source=neighbor.protocol,
            confidence=confidence,
        )
        _add_evidence(
            session,
            link,
            neighbor.protocol,
            confidence,
            {
                "local_device_id": device.id,
                "remote_chassis_id": neighbor.remote_chassis_id,
                "remote_port_id": neighbor.remote_port_id,
            },
        )

        # 12장 bullet: 양방향 LLDP 정보가 모두 있으면 링크 신뢰도를 최상으로 처리한다.
        reciprocal = session.scalar(
            select(LldpNeighbor).where(
                LldpNeighbor.local_device_id == remote_device.id,
                LldpNeighbor.remote_chassis_id == device.lldp_chassis_id,
            )
        )
        if reciprocal is not None:
            link.confidence = max(link.confidence, CONFIDENCE_LLDP_FDB)
            _add_evidence(session, link, "LLDP_BIDIRECTIONAL", CONFIDENCE_LLDP_FDB, {"note": "양방향 LLDP 확인"})


def infer_stp_designated_bridge_links(session: Session, device: NetworkDevice) -> None:
    """[KOS20260922] "STP 링크 정보는 하나도 못 가져오는 것은 장비의 문제인가?"
    질문을 조사하다 발견 - LLDP-MIB을 아예 지원하지 않는 장비가 많아(실사용
    데이터로 확인, 이 벤더 스위치 라인 다수) infer_lldp_links()만으로는 상당수
    실제 링크를 놓친다. dot1dStpPortDesignatedBridge(RFC 1493)는 STP를 지원하는
    모든 Bridge가 노출하는 값으로, 그 포트가 속한 세그먼트의 Designated Bridge
    MAC을 담고 있어 LLDP 없이도 실제 이웃 Bridge를 알아낼 수 있다(실 사례: 한
    장비의 특정 포트가 다른 장비의 Bridge MAC을 정확히 가리키는 것을 라이브
    SNMP로 확인).

    상대 포트 번호(dot1dStpPortDesignatedPort)까지는 신뢰성 있게 로컬 ifIndex로
    환산할 방법이 없어(장비마다 dot1dBasePort와 ifIndex 대응 규칙이 다를 수
    있음) 일부러 시도하지 않는다 - "포트가 잘못 나온다"는 새로운 종류의 오류를
    만드느니, 다른 근거들처럼 상대 포트를 모른 채(dst_interface_id=None) 링크
    존재 자체만 신뢰도 있게 남긴다.
    """
    own_mac = (device.primary_mac or "").lower()
    interfaces = session.scalars(
        select(DeviceInterface).where(
            DeviceInterface.device_id == device.id,
            DeviceInterface.stp_designated_bridge_mac.isnot(None),
        )
    )
    for iface in interfaces:
        designated_mac = (iface.stp_designated_bridge_mac or "").lower()
        if not designated_mac or designated_mac == own_mac:
            continue  # 자기 자신이 Designated Bridge - 정상적인 자기 세그먼트 지정이거나 루프, 이웃 아님

        remote_device = session.scalar(
            select(NetworkDevice).where(
                (NetworkDevice.primary_mac == designated_mac) | (NetworkDevice.lldp_chassis_id == designated_mac)
            )
        )
        if remote_device is None or remote_device.id == device.id:
            continue

        link = _get_or_create_link(
            session,
            device.id,
            iface.id,
            remote_device.id,
            None,
            source="STP",
            confidence=CONFIDENCE_STP_DESIGNATED_BRIDGE,
        )
        _add_evidence(
            session,
            link,
            "STP",
            CONFIDENCE_STP_DESIGNATED_BRIDGE,
            {"local_interface": iface.name, "designated_bridge_mac": designated_mac},
        )


def _is_excluded_management_ip(management_ip: str | None) -> bool:
    """[KOS20260922] Discovery를 실행하는 서버 자신(또는 운영자가 지정한 다른
    모니터링 호스트)의 IP인지 확인한다. 이런 호스트는 대상 장비를 SNMP로 조회할
    때마다 그 장비의 ARP 테이블에 자연히 등록되므로(SNMP 응답을 받으려면 먼저
    ARP로 이 호스트의 MAC을 알아내야 함), ARP/FDB_ARP 근거만으로 이 호스트와
    스캔한 모든 장비 사이에 가짜 링크가 만들어지는 것을 막는다(운영 환경에서
    "특정 IP 하나가 유독 많은 장비와 연결된 것처럼 보인다"고 확인됐는데, 실제로는
    이 NMS 서버 자신이었던 실사례가 있다)."""
    return bool(management_ip) and management_ip in config.TOPOLOGY_EXCLUDE_IPS


def correlate_fdb_arp_endpoints(session: Session) -> None:
    """11장/12장: Switch FDB + L3 ARP를 결합해 단말의 Port 위치를 추정하고 링크를 만든다."""
    from app.identity import DeviceObservation, resolve_or_create_device

    arp_by_mac: dict[str, list[ArpEntry]] = {}
    for arp in session.scalars(select(ArpEntry)):
        arp_by_mac.setdefault(arp.mac.lower(), []).append(arp)

    for fdb in session.scalars(select(MacFdb)):
        mac = fdb.mac.lower()
        switch_device_id = fdb.device_id
        endpoint_device = session.scalar(select(NetworkDevice).where(NetworkDevice.primary_mac == mac))

        matching_arps = arp_by_mac.get(mac, [])
        if endpoint_device is None:
            if not matching_arps:
                continue  # MAC만 보이고 IP 상관관계가 없으면 아직 장비를 생성하지 않는다.
            if _is_excluded_management_ip(matching_arps[0].ip):
                continue
            endpoint_device, _created = resolve_or_create_device(
                session, DeviceObservation(primary_mac=mac, management_ip=matching_arps[0].ip)
            )
            endpoint_device.device_type = endpoint_device.device_type or "UNKNOWN"

        if endpoint_device.id == switch_device_id or _is_excluded_management_ip(endpoint_device.management_ip):
            continue

        confidence = CONFIDENCE_FDB_ARP if matching_arps else CONFIDENCE_FDB_ONLY
        link = _get_or_create_link(
            session,
            switch_device_id,
            fdb.interface_id,
            endpoint_device.id,
            None,
            source="FDB_ARP" if matching_arps else "FDB",
            confidence=confidence,
        )
        _add_evidence(
            session,
            link,
            "FDB_ARP" if matching_arps else "FDB",
            confidence,
            {"mac": mac, "vlan": fdb.vlan, "ip": matching_arps[0].ip if matching_arps else None},
        )


def infer_arp_only_links(session: Session) -> None:
    """FDB 근거 없이 L3 ARP만으로 확인되는 Neighbor 관계 (물리 Port 불명확)."""
    from app.identity import DeviceObservation, resolve_or_create_device

    linked_pairs = {
        (link.src_device_id, link.dst_device_id) for link in session.scalars(select(NetworkLink))
    }

    for arp in session.scalars(select(ArpEntry)):
        if _is_excluded_management_ip(arp.ip):
            continue
        neighbor = session.scalar(select(NetworkDevice).where(NetworkDevice.primary_mac == arp.mac.lower()))
        if neighbor is None:
            neighbor, _created = resolve_or_create_device(
                session, DeviceObservation(primary_mac=arp.mac.lower(), management_ip=arp.ip)
            )
        if neighbor.id == arp.device_id or _is_excluded_management_ip(neighbor.management_ip):
            continue
        pair = tuple(sorted((arp.device_id, neighbor.id)))
        if pair in linked_pairs:
            continue
        link = _get_or_create_link(
            session, arp.device_id, arp.interface_id, neighbor.id, None, source="ARP", confidence=CONFIDENCE_ARP_ONLY
        )
        _add_evidence(session, link, "ARP", CONFIDENCE_ARP_ONLY, {"ip": arp.ip, "mac": arp.mac})
        linked_pairs.add(pair)


def create_manual_link(session: Session, src_device_id: int, dst_device_id: int) -> NetworkLink:
    """13.3절/12장 표: 운영자 지정(MANUAL) 링크. 신뢰도는 항상 최상(100)이다."""
    link = _get_or_create_link(session, src_device_id, None, dst_device_id, None, source="MANUAL", confidence=CONFIDENCE_MANUAL)
    _add_evidence(session, link, "MANUAL", CONFIDENCE_MANUAL, {"note": "운영자 수동 지정"})
    return link


def update_link_liveness(session: Session) -> int:
    """12장 bullet: 일정 시간 보이지 않으면 stale -> down 순으로 상태를 바꾼다."""
    now = utcnow()
    changed = 0
    for link in session.scalars(select(NetworkLink)):
        age = (now - link.last_seen_at).total_seconds()
        new_status = link.status
        if age >= config.LINK_DOWN_AFTER_SECONDS:
            new_status = "DOWN"
        elif age >= config.LINK_STALE_AFTER_SECONDS:
            new_status = "STALE"
        else:
            new_status = "UP"
        if new_status != link.status:
            link.status = new_status
            changed += 1
    return changed


def compute_graph_metrics(session: Session, core_switch_ids: set[int] | None = None) -> dict[int, dict]:
    """Core/Floor 판정에 필요한 Graph 지표를 계산한다.

    Graph Centrality는 완전한 betweenness/closeness 계산 대신 정규화된 Degree
    Centrality로 근사한다(대규모 그래프에서의 계산 비용을 피하기 위한 실용적
    단순화이며, 9장의 '최대 10점' 배점 규모에서는 순위 구분력이 충분하다).

    core_switch_ids: 지정하면 이 집합에 속한 device_id를 CORE_SWITCH로 간주해
    core_neighbor를 계산한다(classify_and_persist_roles의 1차 예비 판정 결과).
    생략하면 DB에 이미 저장된 device_role을 그대로 읽는다.
    """
    devices = {d.id: d for d in session.scalars(select(NetworkDevice))}
    metrics: dict[int, dict] = {
        device_id: {
            "degree": 0,
            "switch_neighbor_count": 0,
            "endpoint_density": 0,
            "parent_switch_count": 0,
            "downstream_switch_count": 0,
            "core_neighbor": False,
        }
        for device_id in devices
    }

    links = session.scalars(select(NetworkLink).where(NetworkLink.status != "DOWN")).all()
    for link in links:
        for me, other in ((link.src_device_id, link.dst_device_id), (link.dst_device_id, link.src_device_id)):
            if me not in metrics or other not in devices:
                continue
            metrics[me]["degree"] += 1
            other_device = devices[other]
            if other_device.device_type in SWITCH_TYPES:
                metrics[me]["switch_neighbor_count"] += 1
                if other_device.discovery_depth < devices[me].discovery_depth:
                    metrics[me]["parent_switch_count"] += 1
                elif other_device.discovery_depth > devices[me].discovery_depth:
                    metrics[me]["downstream_switch_count"] += 1
            if other_device.device_role in ("ENDPOINT", "SERVER"):
                metrics[me]["endpoint_density"] += 1
            is_core = other in core_switch_ids if core_switch_ids is not None else other_device.device_role == "CORE_SWITCH"
            if is_core:
                metrics[me]["core_neighbor"] = True

    max_degree = max((m["degree"] for m in metrics.values()), default=0) or 1
    for m in metrics.values():
        m["centrality"] = m["degree"] / max_degree

    return metrics


def _build_role_evidence(session: Session, device: NetworkDevice, m: dict) -> RoleEvidence:
    # device.vlan_count는 Q-BRIDGE-MIB dot1qVlanStaticTable 기준 장비 전체 VLAN
    # 개수(DETAILED Profile 수집, discovery/engine.py). Trunk가 나르는 VLAN까지
    # 포함하므로, Access Port PVID만 모은 값보다 더 정확한 상한이다. 둘 중 더 큰
    # 값을 사용해 어느 한쪽만 수집된 경우에도 과소평가하지 않는다.
    pvid_distinct_count = len(
        {
            iface.vlan
            for iface in session.scalars(select(DeviceInterface).where(DeviceInterface.device_id == device.id))
            if iface.vlan
        }
    )
    vlan_count = max(device.vlan_count, pvid_distinct_count)
    uplink_trunk_count = len(
        [
            i
            for i in session.scalars(select(DeviceInterface).where(DeviceInterface.device_id == device.id))
            if i.is_trunk or i.is_uplink
        ]
    )
    route_diversity = len(
        {r.destination for r in session.scalars(select(RouteEntry).where(RouteEntry.device_id == device.id))}
    )
    has_route = route_diversity > 0

    return RoleEvidence(
        device_type=device.device_type,
        layer3_capable=device.layer3_capable,
        switch_neighbor_count=m.get("switch_neighbor_count", 0),
        graph_degree=m.get("degree", 0),
        vlan_count=vlan_count,
        subnet_route_diversity=route_diversity,
        uplink_trunk_count=uplink_trunk_count,
        graph_centrality=m.get("centrality", 0.0),
        discovery_depth=device.discovery_depth,
        parent_switch_count=m.get("parent_switch_count", 0),
        downstream_switch_count=m.get("downstream_switch_count", 0),
        endpoint_density=m.get("endpoint_density", 0),
        core_neighbor=m.get("core_neighbor", False),
        partial_l3=has_route and not device.layer3_capable,
        hostname=device.hostname or "",
        sys_location=device.sys_location or "",
    )


def classify_and_persist_roles(session: Session) -> int:
    """9~11장: Role 재계산 및 저장. role_source=MANUAL 장비는 건드리지 않는다."""
    devices = session.scalars(select(NetworkDevice)).all()

    # [KOS20260921] compute_graph_metrics()가 DB에 저장된 device_role을 그대로
    # 읽어 core_neighbor를 계산하면, 최초 Discovery처럼 아직 어떤 장비도
    # CORE_SWITCH로 저장된 적이 없는 상황에서는 이번 호출에서 막 CORE_SWITCH로
    # 확정될 장비의 이웃조차 core_neighbor=False로 계산돼 Distribution/Floor
    # 판정이 한 Cycle(다음 Discovery/재계산)까지 밀리는 문제가 있었다. 이번
    # 호출 안에서 CORE_SWITCH부터 먼저 예비 판정해 같은 Cycle에 반영한다.
    provisional_metrics = compute_graph_metrics(session)
    core_switch_ids: set[int] = {d.id for d in devices if d.role_source == "MANUAL" and d.device_role == "CORE_SWITCH"}
    for device in devices:
        if device.role_source == "MANUAL" or device.device_type not in INFRASTRUCTURE_TYPES:
            continue
        evidence = _build_role_evidence(session, device, provisional_metrics.get(device.id, {}))
        core_total, _ = _core_score(evidence)
        if core_total >= config.CORE_SCORE_THRESHOLD:
            core_switch_ids.add(device.id)

    metrics = compute_graph_metrics(session, core_switch_ids=core_switch_ids)
    updated = 0

    for device in devices:
        if device.role_source == "MANUAL":
            continue

        evidence = _build_role_evidence(session, device, metrics.get(device.id, {}))
        result = classify_device_role(evidence)
        device.device_role = result.device_role
        device.role_score = result.score
        device.role_detail = json.dumps(result.detail, ensure_ascii=False)

        if result.device_role == "FLOOR_SWITCH":
            floor, source = extract_floor_label(device.hostname or "", device.sys_location or "")
            device.physical_floor = floor
            device.floor_source = source
        updated += 1

    return updated


def compute_interface_link_directions(session: Session) -> None:
    """[KOS20260921] DeviceInterface.is_uplink/is_downlink을 Link 반대편 장비의
    discovery_depth와 비교해 자동 계산한다. is_uplink는 원래 스키마부터 있었지만
    실제로 계산해 채우는 코드가 없어 항상 False였다.

    반대편 장비가 더 상위(discovery_depth가 작음)면 이 인터페이스는 그 쪽으로
    향하는 Uplink(상향), 더 하위(depth가 큼)면 Downlink(하향)다. 같은 depth끼리의
    연결(Peer/이중화, 예: Core 스위치 2대 간 Stack 링크)은 방향을 판단하지 않고
    둘 다 False로 둔다. 매번 전체를 다시 계산하므로 더 이상 유효하지 않은 이전
    표시부터 지운다.
    """
    session.query(DeviceInterface).update(
        {DeviceInterface.is_uplink: False, DeviceInterface.is_downlink: False}, synchronize_session=False
    )
    devices = {d.id: d for d in session.scalars(select(NetworkDevice))}
    links = session.scalars(select(NetworkLink).where(NetworkLink.status != "DOWN")).all()
    for link in links:
        src_device = devices.get(link.src_device_id)
        dst_device = devices.get(link.dst_device_id)
        if src_device is None or dst_device is None or src_device.discovery_depth == dst_device.discovery_depth:
            continue  # 장비 미상 또는 같은 depth(Peer/이중화) - 방향 없음
        src_is_upper = src_device.discovery_depth < dst_device.discovery_depth
        if link.src_interface_id is not None:
            src_iface = session.get(DeviceInterface, link.src_interface_id)
            if src_iface is not None:
                if src_is_upper:
                    src_iface.is_downlink = True
                else:
                    src_iface.is_uplink = True
        if link.dst_interface_id is not None:
            dst_iface = session.get(DeviceInterface, link.dst_interface_id)
            if dst_iface is not None:
                if src_is_upper:
                    dst_iface.is_uplink = True
                else:
                    dst_iface.is_downlink = True


def prune_self_referential_links(session: Session) -> int:
    """[KOS20260922] _is_excluded_management_ip()가 신규 링크 생성은 막아 주지만,
    설정을 나중에 추가했거나 이미 예전에 만들어진 링크는 그대로 남아있다 - 이미
    저장된 것들 중 한쪽이라도 제외 대상 IP인 링크를 정리한다. NetworkLinkEvidence는
    FK ondelete=CASCADE라 함께 삭제된다."""
    devices = {d.id: d for d in session.scalars(select(NetworkDevice))}
    removed = 0
    for link in session.scalars(select(NetworkLink)).all():
        src = devices.get(link.src_device_id)
        dst = devices.get(link.dst_device_id)
        if (src and _is_excluded_management_ip(src.management_ip)) or (
            dst and _is_excluded_management_ip(dst.management_ip)
        ):
            session.delete(link)
            removed += 1
    if removed:
        session.flush()
    return removed


def prune_excluded_management_devices(session: Session) -> int:
    """[KOS20260922] discovery/engine.py가 이제 제외 대상 IP(NMS 서버 자신 등)를
    스캔 대상에서 건너뛰지만, 그 전에 CIDR 스캔 대상에 포함되어 이미 만들어진
    장비 Row는 그대로 남는다. 이 Row는 링크만 prune_self_referential_links()로
    지워지고 장비 자체는 계속 남아 Topology에 "아무 데도 연결 안 된 장비"로
    홀로 표시된다(운영 환경에서 NMS 서버 자신이 이런 식으로 리포트된 실사례가
    있다) - 장비 자체를 정리한다. FK ondelete=CASCADE라 Interface/ARP/FDB/Route/PoE/Link도
    함께 삭제된다."""
    removed = 0
    for device in session.scalars(select(NetworkDevice)).all():
        if _is_excluded_management_ip(device.management_ip):
            session.delete(device)
            removed += 1
    if removed:
        session.flush()
    return removed


def clear_all_links(session: Session) -> int:
    """[KOS20260922] "기존 링크 정보를 모두 지우고 새로 하기" 기능. 평소 Discovery는
    증분 방식(기존 링크 재사용/갱신, 안 보이면 STALE/DOWN 전이만)이라, 배선을 바꾼
    뒤 예전 링크가 지워지지 않고 계속 남아있는 것을 답답해할 수 있다 - 사용자가
    명시적으로 요청했을 때만 전체를 비우고 이번 Discovery Run의 근거만으로 다시
    쌓게 한다. NetworkLinkEvidence는 FK ondelete=CASCADE라 함께 삭제된다."""
    removed = session.query(NetworkLink).count()
    session.query(NetworkLink).delete(synchronize_session=False)
    session.flush()
    return removed


def clear_all_devices(session: Session) -> int:
    """[KOS20260922] "장비 정보도 모두 지우고 새로 하기" 기능. 장비를 통째로
    지우면 FK ondelete=CASCADE로 Interface/ARP/FDB/Route/PoE/Neighbor/Link가
    전부 함께 지워지므로 clear_all_links()를 별도로 호출할 필요가 없다."""
    removed = session.query(NetworkDevice).count()
    session.query(NetworkDevice).delete(synchronize_session=False)
    session.flush()
    return removed


def recompute_topology_and_roles(session: Session) -> None:
    """부록 B: compute_graph_metrics() + classify_device_roles() 단계에 대응.

    0) 이 서버(또는 운영자가 지정한 다른 호스트) 자신인 장비 Row 정리
    1) 모든 장비의 LLDP 근거로 링크 생성/갱신
    1-1) LLDP-MIB 미지원 장비를 위한 STP Designated Bridge 근거로 링크 보강
    2) FDB+ARP로 단말 위치 링크 보강
    3) ARP-only 보조 링크 보강
    4) 위 장비 정리 이전에 만들어졌던 가짜 ARP 기반 링크 잔여분 정리
    5) 링크 stale/down 상태 갱신
    6) 인터페이스 Uplink/Downlink 방향 계산(Role 채점의 Uplink/Trunk 근거로도 쓰임)
    7) Graph 지표 계산 후 Role 재분류
    """
    prune_excluded_management_devices(session)
    for device in session.scalars(select(NetworkDevice)).all():
        infer_lldp_links(session, device)
        infer_stp_designated_bridge_links(session, device)
    correlate_fdb_arp_endpoints(session)
    infer_arp_only_links(session)
    prune_self_referential_links(session)
    update_link_liveness(session)
    compute_interface_link_directions(session)
    classify_and_persist_roles(session)
    session.commit()
