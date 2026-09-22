from app.classification.device_role import RoleEvidence, classify_device_role


def test_core_switch_confirmed_with_strong_l3_and_graph_evidence():
    """부록 A.1 예시(합계 98점)에 대응."""
    evidence = RoleEvidence(
        device_type="L3_SWITCH",
        layer3_capable=True,
        switch_neighbor_count=6,
        graph_degree=10,
        vlan_count=40,
        subnet_route_diversity=5,
        uplink_trunk_count=5,
        graph_centrality=0.95,
        discovery_depth=1,
    )
    result = classify_device_role(evidence)
    assert result.device_role == "CORE_SWITCH"
    assert result.score >= 90


def test_core_switch_gate_blocks_non_l3_device_even_with_high_graph_degree():
    """9장 bullet: L3 기능이 없으면 Core로 확정될 수 없다 (Gate 조건)."""
    evidence = RoleEvidence(
        device_type="L2_SWITCH",
        layer3_capable=False,
        switch_neighbor_count=10,
        graph_degree=20,
        vlan_count=40,
        subnet_route_diversity=5,
        uplink_trunk_count=5,
        graph_centrality=1.0,
        discovery_depth=1,
    )
    result = classify_device_role(evidence)
    assert result.device_role != "CORE_SWITCH"


def test_floor_switch_confirmed_via_naming_and_syslocation():
    """부록 A.2 예시(합계 100점)에 대응."""
    evidence = RoleEvidence(
        device_type="L2_POE_SWITCH",
        layer3_capable=False,
        parent_switch_count=1,
        endpoint_density=28,
        downstream_switch_count=0,
        hostname="BLDG-A-3F-SW1",
        sys_location="3F IDF",
    )
    result = classify_device_role(evidence)
    assert result.device_role == "FLOOR_SWITCH"
    assert result.score >= 80


def test_unclear_switch_falls_back_to_access_switch():
    """11.1절: Floor 추론이 불확실하면 ACCESS_SWITCH로 남긴다."""
    evidence = RoleEvidence(device_type="L2_SWITCH", layer3_capable=False)
    result = classify_device_role(evidence)
    assert result.device_role == "ACCESS_SWITCH"
    assert result.method == "FALLBACK"


def test_unclear_l3_switch_falls_back_to_distribution_not_access():
    """[KOS20260921] ACCESS_SWITCH는 순수 L2 Edge 계층이므로, L3 스위치는
    Core/Distribution/Floor 임계치를 못 넘어도 ACCESS_SWITCH가 될 수 없다."""
    evidence = RoleEvidence(device_type="L3_SWITCH", layer3_capable=True)
    result = classify_device_role(evidence)
    assert result.device_role == "DISTRIBUTION_SWITCH"
    assert result.method == "FALLBACK"


def test_ip_camera_maps_to_endpoint_role():
    evidence = RoleEvidence(device_type="IP_CAMERA")
    result = classify_device_role(evidence)
    assert result.device_role == "ENDPOINT"


def test_access_point_maps_to_edge_device_role():
    evidence = RoleEvidence(device_type="ACCESS_POINT")
    result = classify_device_role(evidence)
    assert result.device_role == "EDGE_DEVICE"


def test_windows_pc_with_server_fingerprint_maps_to_server_role():
    evidence = RoleEvidence(device_type="WINDOWS_PC", is_server_fingerprint=True)
    result = classify_device_role(evidence)
    assert result.device_role == "SERVER"
