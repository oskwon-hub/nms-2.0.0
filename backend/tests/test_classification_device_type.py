from app.classification.device_type import ClassificationEvidence, classify_device_type


def test_ip_camera_strong_signature_overrides_weaker_scores():
    """8.1절: ONVIF Device Service 정상 응답은 강한 시그니처로 즉시 확정된다."""
    evidence = ClassificationEvidence(onvif_device_service_ok=True, has_rtsp=True, oui_category="CAMERA")
    result = classify_device_type(evidence)
    assert result.device_type == "IP_CAMERA"
    assert result.method == "STRONG_SIGNATURE"


def test_l2_switch_confirmed_when_score_and_margin_sufficient():
    evidence = ClassificationEvidence(
        has_bridge_mib=True, has_fdb=True, has_lldp=True, physical_port_count=24, has_ip_forwarding=False
    )
    result = classify_device_type(evidence)
    assert result.device_type == "L2_SWITCH"
    assert result.method == "CONFIRMED"
    assert result.score == 35 + 25 + 15 + 15 + 10


def test_l2_poe_switch_variant_applied_when_poe_mib_present():
    evidence = ClassificationEvidence(
        has_bridge_mib=True, has_fdb=True, has_lldp=True, physical_port_count=24, has_ip_forwarding=False, has_poe_mib=True
    )
    result = classify_device_type(evidence)
    assert result.device_type == "L2_POE_SWITCH"


def test_l3_switch_scored_above_l2_when_routing_present():
    evidence = ClassificationEvidence(
        has_bridge_mib=True,
        has_fdb=True,
        has_lldp=True,
        has_ip_forwarding=True,
        route_table_size=5,
        svi_or_l3_if_count=3,
        vlan_count=10,
    )
    result = classify_device_type(evidence)
    assert result.device_type == "L3_SWITCH"


def test_router_scored_when_no_bridge_but_strong_l3_evidence():
    evidence = ClassificationEvidence(
        has_ip_forwarding=True,
        route_table_size=10,
        svi_or_l3_if_count=4,
        has_wan_or_default_route=True,
        has_bridge_mib=False,
    )
    result = classify_device_type(evidence)
    assert result.device_type == "ROUTER"
    assert result.method == "CONFIRMED"


def test_l2_switch_confirm_suppressed_when_route_evidence_present():
    """[KOS20260921] L2_SWITCH 채점 항목은 L3 스위치도 대부분 만족하므로, Route
    근거가 있는데도 L2_SWITCH가 CONFIRMED로 확정되면 안 된다(실제 사례: NSH-3228
    이 ipForwarding=false만으로 L2_SWITCH CONFIRMED가 되어 버린 오분류)."""
    evidence = ClassificationEvidence(
        has_bridge_mib=True,
        has_fdb=True,
        has_lldp=True,
        physical_port_count=24,
        has_ip_forwarding=False,
        route_table_size=2,
    )
    result = classify_device_type(evidence)
    # Route 근거가 있으면 L2_SWITCH를 CONFIRMED로 단정하지 않는다(REVIEW_REQUIRED로
    # L2_SWITCH가 최유력 후보로 노출되는 것 자체는 허용 - L3_SWITCH 근거도 아직 약하므로).
    assert not (result.device_type == "L2_SWITCH" and result.method == "CONFIRMED")


def test_nsh_naming_strong_signature_confirms_l3_switch_over_weak_l2_score():
    """사용자 확인 규칙: CCC-NNNNP에서 NSH/NHM + 모델 번호 첫 자리 3 = L3, 뒤에
    'P' 없음 = PoE 아님. SNMP 근거가 L2_SWITCH 쪽으로 훨씬 강해도(NSH-3228 실사례)
    명명 규칙이 우선해야 한다."""
    evidence = ClassificationEvidence(
        has_bridge_mib=True,
        has_fdb=True,
        has_lldp=True,
        physical_port_count=24,
        has_ip_forwarding=False,  # SNMP상 라우팅 근거 없음 (L2로 오분류되던 상황 재현)
        hostname="NSH-3228",
    )
    result = classify_device_type(evidence)
    assert result.device_type == "L3_SWITCH"
    assert result.method == "STRONG_SIGNATURE"


def test_nsh_naming_strong_signature_applies_poe_variant():
    evidence = ClassificationEvidence(
        has_bridge_mib=True, has_fdb=True, has_lldp=True, hostname="NSH-2128PT"
    )
    result = classify_device_type(evidence)
    assert result.device_type == "L2_POE_SWITCH"


def test_nhm_naming_without_p_is_not_treated_as_poe_even_with_poe_mib_evidence():
    """실사례: NHM-3228F가 NST 사설 PoE MIB 오탐(has_poe_mib=True)에도 불구하고
    명명 규칙상 PoE 미지원 모델이면 L2_POE_SWITCH가 아닌 L2_SWITCH여야 한다."""
    evidence = ClassificationEvidence(
        has_bridge_mib=True, has_fdb=True, has_lldp=True, has_poe_mib=True, hostname="NHM-3228F"
    )
    result = classify_device_type(evidence)
    assert result.device_type == "L3_SWITCH"


def test_hostname_not_matching_nsh_nhm_pattern_falls_back_to_score_based_classification():
    evidence = ClassificationEvidence(
        has_bridge_mib=True, has_fdb=True, has_lldp=True, physical_port_count=24, has_ip_forwarding=False,
        hostname="SOME-OTHER-VENDOR-SW1",
    )
    result = classify_device_type(evidence)
    assert result.device_type == "L2_SWITCH"
    assert result.method == "CONFIRMED"


def test_ambiguous_evidence_falls_back_to_review_required():
    evidence = ClassificationEvidence()  # 아무 근거도 없음
    result = classify_device_type(evidence)
    assert result.device_type == "UNKNOWN"
    assert result.method == "REVIEW_REQUIRED"


def test_windows_pc_confirmed_via_wmi_and_smb():
    evidence = ClassificationEvidence(has_wmi=True, has_smb_or_rdp=True, windows_sysdescr=True, has_netbios_or_dns=True)
    result = classify_device_type(evidence)
    assert result.device_type == "WINDOWS_PC"


def test_hub_scored_when_management_absent_and_many_vendor_macs():
    evidence = ClassificationEvidence(has_management_response=False, distinct_mac_vendor_count_on_ports=5)
    result = classify_device_type(evidence)
    assert result.device_type == "HUB"


def test_linux_pc_review_required_without_credentials_but_with_ttl_and_ssh():
    """WMI/SSH 자격증명 없이 TTL+SSH 배너만으로는 60점 임계치를 넘지 못하지만,
    REVIEW_FLOOR 이상이면 UNKNOWN 대신 유력 후보(LINUX_PC)를 노출해야 한다."""
    evidence = ClassificationEvidence(
        has_ssh_banner=True,
        linux_service_fingerprint=True,  # SSH 배너에 "Ubuntu" 등 포함
        os_ttl_hint="UNIX",
    )
    result = classify_device_type(evidence)
    assert result.device_type == "LINUX_PC"
    assert result.method == "REVIEW_REQUIRED"
    assert result.score < 60


def test_windows_pc_crosses_threshold_with_smb_ttl_and_sysdescr():
    evidence = ClassificationEvidence(
        has_smb_or_rdp=True,
        os_ttl_hint="WINDOWS",
        windows_sysdescr=True,
        has_netbios_or_dns=True,
    )
    result = classify_device_type(evidence)
    assert result.device_type == "WINDOWS_PC"
    assert result.method == "CONFIRMED"


def test_mac_pc_sysdescr_requires_macos_specific_content_not_any_text():
    """[KOS20260920] 회귀 테스트: 이전에는 `if e.sys_descr:`가 참/거짓만 봐서
    macOS와 무관한 sysDescr에도 MAC_PC 점수가 잘못 붙는 버그가 있었다."""
    generic = ClassificationEvidence(sys_descr="Cisco IOS Software", macos_sysdescr=False)
    generic_result = classify_device_type(generic)
    assert generic_result.all_scores["MAC_PC"] == 0

    macos_evidence = ClassificationEvidence(sys_descr="Darwin Kernel Version", macos_sysdescr=True, oui_category="APPLE")
    macos_result = classify_device_type(macos_evidence)
    assert macos_result.all_scores["MAC_PC"] == 40  # Apple OUI(20) + macOS sysDescr(20)


def test_ambiguous_low_score_still_falls_back_to_unknown():
    """근거가 REVIEW_FLOOR에도 못 미치면 여전히 UNKNOWN으로 남아야 한다."""
    evidence = ClassificationEvidence(has_netbios_or_dns=True)  # Windows +10점뿐
    result = classify_device_type(evidence)
    assert result.device_type == "UNKNOWN"
