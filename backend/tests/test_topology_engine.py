from app import config
from app.identity import DeviceObservation, resolve_or_create_device
from app.models import ArpEntry, DeviceInterface, LldpNeighbor, MacFdb
from app.topology.engine import (
    CONFIDENCE_FDB_ARP,
    CONFIDENCE_LLDP,
    CONFIDENCE_STP_DESIGNATED_BRIDGE,
    compute_graph_metrics,
    compute_interface_link_directions,
    correlate_fdb_arp_endpoints,
    infer_arp_only_links,
    infer_lldp_links,
    infer_stp_designated_bridge_links,
    prune_excluded_management_devices,
    prune_self_referential_links,
    recompute_topology_and_roles,
)
from app.models import ConfigChangeLog, NetworkDevice, NetworkLink


def _make_device(session, **kwargs):
    device, _ = resolve_or_create_device(session, DeviceObservation(**kwargs))
    session.flush()
    return device


def test_infer_lldp_links_creates_link_with_max_confidence(db_session):
    core = _make_device(db_session, management_ip="10.0.0.1", lldp_chassis_id="core-chassis")
    floor = _make_device(db_session, management_ip="10.0.0.2", lldp_chassis_id="floor-chassis")
    db_session.flush()

    core_if = DeviceInterface(device_id=core.id, if_index=1, name="Gi0/1")
    db_session.add(core_if)
    db_session.flush()

    db_session.add(
        LldpNeighbor(
            local_device_id=core.id,
            local_interface_id=core_if.id,
            remote_chassis_id="floor-chassis",
            remote_port_id="Gi0/24",
            remote_sys_name="FLOOR-SW-01",
            remote_mgmt_ip="10.0.0.2",
            protocol="LLDP",
        )
    )
    db_session.flush()

    infer_lldp_links(db_session, core)
    db_session.commit()

    links = db_session.query(NetworkLink).all()
    assert len(links) == 1
    link = links[0]
    assert link.confidence == CONFIDENCE_LLDP
    assert {link.src_device_id, link.dst_device_id} == {core.id, floor.id}


def test_infer_lldp_links_falls_back_to_if_index_when_port_name_does_not_match(db_session):
    """일부 장비는 lldpRemPortIdSubtype이 'local'이라 포트 이름 대신 원시 ifIndex 숫자를
    remote_port_id로 보고한다(예: "24"). 이름 매칭이 실패해도 상대 장비의 ifIndex와
    일치하면 포트를 식별할 수 있어야 한다(Topology 링크 레이블의 "?" 완화)."""
    core = _make_device(db_session, management_ip="10.0.0.1", lldp_chassis_id="core-chassis")
    floor = _make_device(db_session, management_ip="10.0.0.2", lldp_chassis_id="floor-chassis")
    db_session.flush()

    core_if = DeviceInterface(device_id=core.id, if_index=1, name="Gi0/1")
    db_session.add(core_if)
    floor_if = DeviceInterface(device_id=floor.id, if_index=24, name="GigabitEthernet0/24")
    db_session.add(floor_if)
    db_session.flush()

    db_session.add(
        LldpNeighbor(
            local_device_id=core.id,
            local_interface_id=core_if.id,
            remote_chassis_id="floor-chassis",
            remote_port_id="24",  # 이름이 아니라 ifIndex 숫자
            remote_sys_name="FLOOR-SW-01",
            remote_mgmt_ip="10.0.0.2",
            protocol="LLDP",
        )
    )
    db_session.flush()

    infer_lldp_links(db_session, core)
    db_session.commit()

    links = db_session.query(NetworkLink).all()
    assert len(links) == 1
    link = links[0]
    assert floor_if.id in {link.src_interface_id, link.dst_interface_id}


def test_infer_lldp_links_merges_both_directions_into_one_link_with_both_ports(db_session):
    """LLDP는 두 장비를 각각 따로 처리한다 - 각 호출은 '자기 로컬 포트'는 알지만
    상대 포트 이름/ifIndex 매칭은 실패할 수 있다. 두 방향을 합치지 않으면 같은
    케이블이 (PortA, ?)와 (?, PortB) 두 개의 평행 링크로 쪼개져 보이는 문제가
    있었다 - 이제는 한쪽이 채운 포트를 다른 쪽 호출이 이어받아 링크 하나에
    양쪽 포트가 모두 채워져야 한다."""
    core = _make_device(db_session, management_ip="10.0.0.1", lldp_chassis_id="core-chassis")
    floor = _make_device(db_session, management_ip="10.0.0.2", lldp_chassis_id="floor-chassis")
    db_session.flush()

    core_if = DeviceInterface(device_id=core.id, if_index=1000025, name="Switch  1 - Port 25")
    db_session.add(core_if)
    floor_if = DeviceInterface(device_id=floor.id, if_index=1000025, name="Switch  1 - Port 25")
    db_session.add(floor_if)
    db_session.flush()

    # core -> floor 방향: core는 자기 로컬 포트(core_if)를 알지만, floor의 포트
    # 이름/ifIndex는 매칭에 실패한다(remote_port_id가 어느 쪽으로도 안 맞는 값).
    db_session.add(
        LldpNeighbor(
            local_device_id=core.id,
            local_interface_id=core_if.id,
            remote_chassis_id="floor-chassis",
            remote_port_id="no-match-on-floor",
            remote_sys_name="FLOOR-SW-01",
            remote_mgmt_ip="10.0.0.2",
            protocol="LLDP",
        )
    )
    # floor -> core 방향: floor는 자기 로컬 포트(floor_if)를 알지만, core의 포트는
    # 매칭에 실패한다.
    db_session.add(
        LldpNeighbor(
            local_device_id=floor.id,
            local_interface_id=floor_if.id,
            remote_chassis_id="core-chassis",
            remote_port_id="no-match-on-core",
            remote_sys_name="CORE-SW-01",
            remote_mgmt_ip="10.0.0.1",
            protocol="LLDP",
        )
    )
    db_session.flush()

    infer_lldp_links(db_session, core)
    infer_lldp_links(db_session, floor)
    db_session.commit()

    links = db_session.query(NetworkLink).all()
    assert len(links) == 1
    link = links[0]
    assert {link.src_interface_id, link.dst_interface_id} == {core_if.id, floor_if.id}


def test_infer_stp_designated_bridge_links_creates_link_when_mac_matches_another_device(db_session):
    """[KOS20260922] "STP 링크 정보는 하나도 못 가져오는 것은 장비의 문제인가?"
    조사 중 확인한 실 사례 - LLDP-MIB을 지원하지 않는 장비라도 BRIDGE-MIB STP는
    지원하는 경우가 많아, dot1dStpPortDesignatedBridge가 다른 장비의 Bridge
    MAC을 가리키면 그것만으로 링크를 만들 수 있어야 한다."""
    local = _make_device(db_session, management_ip="10.0.0.1", primary_mac="aa:aa:aa:aa:aa:01")
    remote = _make_device(db_session, management_ip="10.0.0.2", primary_mac="bb:bb:bb:bb:bb:02")
    db_session.flush()

    local_if = DeviceInterface(
        device_id=local.id, if_index=23, name="GigabitEthernet23", stp_designated_bridge_mac="bb:bb:bb:bb:bb:02"
    )
    db_session.add(local_if)
    db_session.flush()

    infer_stp_designated_bridge_links(db_session, local)
    db_session.commit()

    links = db_session.query(NetworkLink).all()
    assert len(links) == 1
    link = links[0]
    assert {link.src_device_id, link.dst_device_id} == {local.id, remote.id}
    assert link.source == "STP"
    assert link.confidence == CONFIDENCE_STP_DESIGNATED_BRIDGE
    assert local_if.id in {link.src_interface_id, link.dst_interface_id}


def test_infer_stp_designated_bridge_links_skips_self_reference(db_session):
    """[KOS20260922] 운영 환경 실사례 - Root Bridge를 자처하면서 활성 포트 2개의
    Designated Bridge가 전부 자기 자신인 상태(자기 BPDU가 되돌아오는 루프 의심
    상태)로 확인됐다. 이런 자기 참조는 실제 이웃이 아니므로 링크를 만들면
    안 된다."""
    local = _make_device(db_session, management_ip="10.0.0.1", primary_mac="aa:aa:aa:aa:aa:01")
    db_session.flush()

    local_if = DeviceInterface(
        device_id=local.id, if_index=23, name="GigabitEthernet23", stp_designated_bridge_mac="aa:aa:aa:aa:aa:01"
    )
    db_session.add(local_if)
    db_session.flush()

    infer_stp_designated_bridge_links(db_session, local)
    db_session.commit()

    assert db_session.query(NetworkLink).count() == 0


def test_infer_stp_designated_bridge_links_skips_unknown_bridge_mac(db_session):
    """Designated Bridge가 DB에 없는 장비의 MAC을 가리키면(아직 발견 못 한
    장비이거나 LLDP 없이는 식별 불가한 장비) 링크를 만들지 않고 조용히 넘어간다."""
    local = _make_device(db_session, management_ip="10.0.0.1", primary_mac="aa:aa:aa:aa:aa:01")
    db_session.flush()

    local_if = DeviceInterface(
        device_id=local.id, if_index=23, name="GigabitEthernet23", stp_designated_bridge_mac="cc:cc:cc:cc:cc:99"
    )
    db_session.add(local_if)
    db_session.flush()

    infer_stp_designated_bridge_links(db_session, local)
    db_session.commit()

    assert db_session.query(NetworkLink).count() == 0


def test_get_or_create_link_merges_already_diverged_rows(db_session):
    """실제 운영 데이터에서 재현된 문제: (PortA, NULL)과 (NULL, PortB)로 이미
    갈라진 링크 2개가 있으면, 이후 재스캔 호출은 매번 '자기 자신과 정확히
    일치'하는 행만 찾아내 서로 영원히 합쳐지지 않았다(NSH-3228/NHM-3228F 포트
    25-25 링크가 재스캔을 반복해도 두 행으로 계속 남아있던 사례). 이미 갈라진
    행이 있어도 호환되는 후보를 전부 모아 하나로 합치고, 각 행에 쌓여있던
    Evidence도 보존돼야 한다."""
    from app.models import NetworkLinkEvidence, utcnow
    from app.topology.engine import _add_evidence, _get_or_create_link

    a = _make_device(db_session, management_ip="10.0.0.21")
    b = _make_device(db_session, management_ip="10.0.0.22")
    db_session.flush()
    a_if = DeviceInterface(device_id=a.id, if_index=1, name="Gi0/1")
    b_if = DeviceInterface(device_id=b.id, if_index=1, name="Gi0/1")
    db_session.add_all([a_if, b_if])
    db_session.flush()

    now = utcnow()
    link1 = NetworkLink(
        src_device_id=a.id, src_interface_id=a_if.id, dst_device_id=b.id, dst_interface_id=None,
        source="LLDP", confidence=100, status="UP", first_seen_at=now, last_seen_at=now,
    )
    link2 = NetworkLink(
        src_device_id=a.id, src_interface_id=None, dst_device_id=b.id, dst_interface_id=b_if.id,
        source="FDB", confidence=65, status="UP", first_seen_at=now, last_seen_at=now,
    )
    db_session.add_all([link1, link2])
    db_session.flush()
    _add_evidence(db_session, link1, "LLDP", 100, {"side": "a"})
    _add_evidence(db_session, link2, "FDB", 65, {"side": "b"})
    db_session.commit()

    # 재스캔: device a 쪽 호출이 예전 코드에서는 link1과 정확히 일치해 link2를
    # 영영 찾지 못했다.
    _get_or_create_link(db_session, a.id, a_if.id, b.id, None, source="LLDP", confidence=100)
    db_session.commit()

    all_links = db_session.query(NetworkLink).filter(
        NetworkLink.src_device_id == a.id, NetworkLink.dst_device_id == b.id
    ).all()
    assert len(all_links) == 1
    merged = all_links[0]
    assert merged.src_interface_id == a_if.id
    assert merged.dst_interface_id == b_if.id

    evidence = db_session.query(NetworkLinkEvidence).filter(NetworkLinkEvidence.link_id == merged.id).all()
    assert len(evidence) == 2  # 두 행의 근거가 모두 보존됨(유실 없이 재배정)


def test_correlate_fdb_arp_creates_endpoint_link_with_expected_confidence(db_session):
    """19장 T4 시나리오: LLDP 미지원 Endpoint는 FDB+ARP로 Switch Port-MAC-IP 연결."""
    switch = _make_device(db_session, management_ip="10.0.0.1", primary_mac="aa:aa:aa:aa:aa:aa")
    db_session.flush()
    switch_if = DeviceInterface(device_id=switch.id, if_index=5, name="Gi1/0/5")
    db_session.add(switch_if)
    db_session.flush()

    mac = "00:11:22:33:44:55"
    db_session.add(MacFdb(device_id=switch.id, interface_id=switch_if.id, vlan=20, mac=mac, entry_type="DYNAMIC"))
    db_session.add(ArpEntry(device_id=switch.id, ip="192.168.20.101", mac=mac, state="REACHABLE"))
    db_session.commit()

    correlate_fdb_arp_endpoints(db_session)
    db_session.commit()

    links = db_session.query(NetworkLink).all()
    assert len(links) == 1
    assert links[0].confidence == CONFIDENCE_FDB_ARP
    assert links[0].source == "FDB_ARP"

    endpoint_ids = {links[0].src_device_id, links[0].dst_device_id} - {switch.id}
    assert len(endpoint_ids) == 1


def test_correlate_fdb_arp_skips_excluded_management_ip(db_session, monkeypatch):
    """[재현 시나리오] 172.16.1.99(이 NMS 서버 자신)가 스캔한 모든 장비의 ARP
    테이블에 등록돼, FDB_ARP 근거만으로 모든 장비와 연결된 것처럼 보이는 문제가
    실제로 있었다 - TOPOLOGY_EXCLUDE_IPS에 있는 IP는 endpoint로 링크를 만들지
    않아야 한다."""
    monkeypatch.setattr(config, "TOPOLOGY_EXCLUDE_IPS", {"192.168.20.101"})

    switch = _make_device(db_session, management_ip="10.0.0.1", primary_mac="aa:aa:aa:aa:aa:aa")
    db_session.flush()
    switch_if = DeviceInterface(device_id=switch.id, if_index=5, name="Gi1/0/5")
    db_session.add(switch_if)
    db_session.flush()

    mac = "00:11:22:33:44:55"
    db_session.add(MacFdb(device_id=switch.id, interface_id=switch_if.id, vlan=20, mac=mac, entry_type="DYNAMIC"))
    db_session.add(ArpEntry(device_id=switch.id, ip="192.168.20.101", mac=mac, state="REACHABLE"))
    db_session.commit()

    correlate_fdb_arp_endpoints(db_session)
    db_session.commit()

    assert db_session.query(NetworkLink).count() == 0


def test_infer_arp_only_links_skips_excluded_management_ip(db_session, monkeypatch):
    monkeypatch.setattr(config, "TOPOLOGY_EXCLUDE_IPS", {"192.168.20.101"})

    switch = _make_device(db_session, management_ip="10.0.0.1")
    db_session.flush()
    db_session.add(ArpEntry(device_id=switch.id, ip="192.168.20.101", mac="00:11:22:33:44:66", state="REACHABLE"))
    db_session.commit()

    infer_arp_only_links(db_session)
    db_session.commit()

    assert db_session.query(NetworkLink).count() == 0


def test_prune_self_referential_links_removes_existing_bad_links(db_session, monkeypatch):
    """새로 만들어지는 링크뿐 아니라, 이미 저장돼 있던 자기참조 링크도 정리돼야
    한다(예: TOPOLOGY_EXCLUDE_IPS를 나중에 추가/변경한 경우)."""
    scanner = _make_device(db_session, management_ip="172.16.1.99")
    switch = _make_device(db_session, management_ip="10.0.0.1")
    db_session.flush()
    from app.topology.engine import _add_evidence, _get_or_create_link

    link = _get_or_create_link(db_session, switch.id, None, scanner.id, None, source="ARP", confidence=40)
    _add_evidence(db_session, link, "ARP", 40, {"note": "self-referential"})
    db_session.commit()
    link_id = link.id

    monkeypatch.setattr(config, "TOPOLOGY_EXCLUDE_IPS", {"172.16.1.99"})

    removed = prune_self_referential_links(db_session)
    db_session.commit()

    assert removed == 1
    assert db_session.get(NetworkLink, link_id) is None


def test_prune_excluded_management_devices_removes_scanner_itself(db_session, monkeypatch):
    """[KOS20260922] CIDR 스캔 대상에 NMS 서버 자신의 IP가 포함돼 이미 장비
    Row로 만들어진 경우, 링크만 지워지고 장비는 남아 Topology에 "아무 데도
    연결 안 된 장비"로 홀로 표시된다("172.16.1.99가 연결 안 된 채 혼자
    있다" 리포트) - 장비 자체를 정리해야 한다."""
    scanner = _make_device(db_session, management_ip="172.16.1.99")
    real_device = _make_device(db_session, management_ip="10.0.0.1")
    scanner_id = scanner.id
    db_session.commit()

    monkeypatch.setattr(config, "TOPOLOGY_EXCLUDE_IPS", {"172.16.1.99"})

    removed = prune_excluded_management_devices(db_session)
    db_session.commit()

    assert removed == 1
    assert db_session.get(NetworkDevice, scanner_id) is None
    assert db_session.get(NetworkDevice, real_device.id) is not None


def test_recompute_topology_and_roles_prunes_excluded_management_devices(db_session, monkeypatch):
    """개별 함수 호출이 아니라 실제 Discovery/재분류 경로인
    recompute_topology_and_roles()에도 연결돼야 매 실행마다 자가 치유된다."""
    scanner = _make_device(db_session, management_ip="172.16.1.99")
    scanner_id = scanner.id
    db_session.commit()

    monkeypatch.setattr(config, "TOPOLOGY_EXCLUDE_IPS", {"172.16.1.99"})

    recompute_topology_and_roles(db_session)

    assert db_session.get(NetworkDevice, scanner_id) is None


def test_compute_graph_metrics_core_switch_ids_override_marks_neighbor_as_core(db_session):
    """[KOS20260921] compute_graph_metrics()가 DB에 저장된 device_role만 읽으면,
    이번 호출에서 막 CORE_SWITCH로 판정될 장비의 이웃은 core_neighbor=False로
    계산되어 Distribution/Floor 판정이 한 Cycle 뒤처지는 문제가 있었다.
    core_switch_ids를 넘기면 아직 DB에 반영되지 않은 예비 판정도 즉시 반영돼야 한다."""
    core = _make_device(db_session, management_ip="10.0.0.1", lldp_chassis_id="core-chassis")
    core.device_type = "L3_SWITCH"
    neighbor = _make_device(db_session, management_ip="10.0.0.2", lldp_chassis_id="dist-chassis")
    neighbor.device_type = "L2_SWITCH"
    db_session.flush()

    link = NetworkLink(src_device_id=core.id, dst_device_id=neighbor.id, source="LLDP", confidence=100, status="UP")
    db_session.add(link)
    db_session.commit()

    # device_role이 아직 둘 다 UNKNOWN인 상태(최초 Discovery 상황) - override 없이는
    # neighbor 입장에서 core가 CORE_SWITCH임을 알 수 없다.
    stale_metrics = compute_graph_metrics(db_session)
    assert stale_metrics[neighbor.id]["core_neighbor"] is False

    fresh_metrics = compute_graph_metrics(db_session, core_switch_ids={core.id})
    assert fresh_metrics[neighbor.id]["core_neighbor"] is True


def test_recompute_topology_and_roles_end_to_end_core_and_floor(db_session):
    """19장 T1 유사 시나리오: Core L3 -> Floor PoE 연결에서 Role이 자동 산출된다."""
    core = _make_device(db_session, management_ip="10.0.0.1", lldp_chassis_id="core-chassis")
    core.layer3_capable = True
    core.device_type = "L3_SWITCH"

    floor = _make_device(db_session, management_ip="10.0.0.2", lldp_chassis_id="floor-chassis", hostname="FLOOR-SW-01")
    floor.device_type = "L2_POE_SWITCH"
    floor.discovery_depth = 1
    db_session.flush()

    core_if = DeviceInterface(device_id=core.id, if_index=1, name="Gi0/1", is_uplink=True)
    floor_if = DeviceInterface(device_id=floor.id, if_index=1, name="Gi0/24", is_uplink=True)
    db_session.add_all([core_if, floor_if])
    db_session.flush()

    db_session.add(
        LldpNeighbor(
            local_device_id=core.id,
            local_interface_id=core_if.id,
            remote_chassis_id="floor-chassis",
            remote_port_id="Gi0/24",
            remote_mgmt_ip="10.0.0.2",
        )
    )
    db_session.add(
        LldpNeighbor(
            local_device_id=floor.id,
            local_interface_id=floor_if.id,
            remote_chassis_id="core-chassis",
            remote_port_id="Gi0/1",
            remote_mgmt_ip="10.0.0.1",
        )
    )
    db_session.commit()

    recompute_topology_and_roles(db_session)

    db_session.refresh(core)
    db_session.refresh(floor)
    assert core.device_role in ("CORE_SWITCH", "DISTRIBUTION_SWITCH", "ACCESS_SWITCH")
    # L3 + 실제 Neighbor가 1개뿐이라 임계치 미달일 수 있으나, 최소한 Gate를 통과해 UNKNOWN은 아니어야 한다.
    assert floor.device_role != "UNKNOWN"


def test_recompute_topology_and_roles_logs_auto_role_change(db_session):
    """[KOS20260923] 구성 변경 이력 - 자동 재분류로 device_role이 바뀌면
    ConfigChangeLog(source=DISCOVERY)에 남아야 한다."""
    core = _make_device(db_session, management_ip="10.0.0.1", lldp_chassis_id="core-chassis")
    core.layer3_capable = True
    core.device_type = "L3_SWITCH"
    core.device_role = "SERVER"  # 이 시나리오에서 나올 수 없는, 의도적으로 틀린 이전 값

    floor = _make_device(db_session, management_ip="10.0.0.2", lldp_chassis_id="floor-chassis", hostname="FLOOR-SW-01")
    floor.device_type = "L2_POE_SWITCH"
    floor.discovery_depth = 1
    db_session.flush()

    core_if = DeviceInterface(device_id=core.id, if_index=1, name="Gi0/1", is_uplink=True)
    floor_if = DeviceInterface(device_id=floor.id, if_index=1, name="Gi0/24", is_uplink=True)
    db_session.add_all([core_if, floor_if])
    db_session.flush()

    db_session.add(
        LldpNeighbor(
            local_device_id=core.id,
            local_interface_id=core_if.id,
            remote_chassis_id="floor-chassis",
            remote_port_id="Gi0/24",
            remote_mgmt_ip="10.0.0.2",
        )
    )
    db_session.add(
        LldpNeighbor(
            local_device_id=floor.id,
            local_interface_id=floor_if.id,
            remote_chassis_id="core-chassis",
            remote_port_id="Gi0/1",
            remote_mgmt_ip="10.0.0.1",
        )
    )
    db_session.commit()

    recompute_topology_and_roles(db_session)

    db_session.refresh(core)
    assert core.device_role != "SERVER"
    change = db_session.query(ConfigChangeLog).filter_by(device_id=core.id, field_name="device_role").one()
    assert change.old_value == "SERVER"
    assert change.new_value == core.device_role
    assert change.source == "DISCOVERY"
    assert change.performed_by is None


def test_compute_interface_link_directions_marks_uplink_and_downlink(db_session):
    """[KOS20260921] is_uplink는 원래 스키마부터 있었지만 실제로 계산해 채우는
    코드가 없어 항상 False였다. depth가 더 작은(상위) 장비로 향하는 인터페이스는
    Uplink, 더 큰(하위) 장비로 향하는 인터페이스는 Downlink로 표시되어야 한다."""
    core = _make_device(db_session, management_ip="10.0.0.1")
    core.discovery_depth = 0
    floor = _make_device(db_session, management_ip="10.0.0.2")
    floor.discovery_depth = 1
    db_session.flush()

    core_if = DeviceInterface(device_id=core.id, if_index=1, name="Gi0/1")
    floor_if = DeviceInterface(device_id=floor.id, if_index=1, name="Gi0/24")
    db_session.add_all([core_if, floor_if])
    db_session.flush()

    db_session.add(
        NetworkLink(
            src_device_id=core.id,
            src_interface_id=core_if.id,
            dst_device_id=floor.id,
            dst_interface_id=floor_if.id,
            source="LLDP",
            confidence=100,
            status="UP",
        )
    )
    db_session.commit()

    compute_interface_link_directions(db_session)
    db_session.commit()

    db_session.refresh(core_if)
    db_session.refresh(floor_if)
    # core(depth 0)는 상위이므로 floor(depth 1)로 향하는 core_if는 Downlink.
    assert core_if.is_downlink is True
    assert core_if.is_uplink is False
    # floor(depth 1)는 하위이므로 core(depth 0)로 향하는 floor_if는 Uplink.
    assert floor_if.is_uplink is True
    assert floor_if.is_downlink is False


def test_compute_interface_link_directions_leaves_peer_links_undirected(db_session):
    """같은 depth끼리의 연결(Peer/이중화, 예: Core 스위치 2대 간 Stack 링크)은
    Uplink도 Downlink도 아니어야 한다."""
    core1 = _make_device(db_session, management_ip="10.0.0.1")
    core1.discovery_depth = 0
    core2 = _make_device(db_session, management_ip="10.0.0.2")
    core2.discovery_depth = 0
    db_session.flush()

    if1 = DeviceInterface(device_id=core1.id, if_index=1, name="Gi0/1")
    if2 = DeviceInterface(device_id=core2.id, if_index=1, name="Gi0/1")
    db_session.add_all([if1, if2])
    db_session.flush()

    db_session.add(
        NetworkLink(
            src_device_id=core1.id,
            src_interface_id=if1.id,
            dst_device_id=core2.id,
            dst_interface_id=if2.id,
            source="LLDP",
            confidence=100,
            status="UP",
        )
    )
    db_session.commit()

    compute_interface_link_directions(db_session)
    db_session.commit()

    db_session.refresh(if1)
    db_session.refresh(if2)
    assert (if1.is_uplink, if1.is_downlink) == (False, False)
    assert (if2.is_uplink, if2.is_downlink) == (False, False)
