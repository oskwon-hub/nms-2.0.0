"""8장: 장비 유형(Device Type) 식별 로직.

"규칙 기반 점수 + 강한 시그니처 우선" 방식: 후보별 점수를 계산하고, 최고 점수가
임계치 이상이면서 2위와 충분한 차이가 있으면 확정한다. 강한 시그니처(예: ONVIF
Device Service 정상 응답)는 즉시 후보를 높여 약한 포트 스캔 증거보다 우선한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from app import config

DEVICE_TYPES = (
    "LINUX_PC",
    "WINDOWS_PC",
    "MAC_PC",
    "IP_CAMERA",
    "ACCESS_POINT",
    "HUB",
    "L2_SWITCH",
    "L2_POE_SWITCH",
    "L3_SWITCH",
    "L3_POE_SWITCH",
    "ROUTER",
    "UNKNOWN",
)


@dataclass
class ClassificationEvidence:
    """모든 Collector 결과를 정규화해 담는 입력 구조체."""

    # L2/L3 Switch, Router
    has_bridge_mib: bool = False
    has_fdb: bool = False
    has_lldp: bool = False
    physical_port_count: int = 0
    has_ip_forwarding: bool = False
    route_table_size: int = 0
    svi_or_l3_if_count: int = 0
    vlan_count: int = 0
    has_wan_or_default_route: bool = False
    bridge_capability_weak: bool = False
    has_poe_mib: bool = False
    hostname: str = ""

    # IP Camera
    has_onvif: bool = False
    onvif_device_service_ok: bool = False  # 강한 시그니처
    has_rtsp: bool = False
    oui_category: Optional[str] = None  # "CAMERA" | "APPLE" | "AP" | None
    http_title_hint: bool = False
    sys_descr: str = ""

    # Windows
    has_wmi: bool = False
    has_smb_or_rdp: bool = False
    windows_sysdescr: bool = False
    has_netbios_or_dns: bool = False

    # Linux
    has_ssh_banner: bool = False
    linux_sysdescr: bool = False
    linux_service_fingerprint: bool = False
    has_ssh_uname: bool = False

    # macOS
    has_mdns_bonjour: bool = False
    macos_fingerprint: bool = False
    macos_sysdescr: bool = False

    # ICMP TTL 기반 OS 계열 보조 힌트 ("UNIX"|"WINDOWS"|"NETWORK"|None).
    # SNMP/WMI/SSH 자격증명 없이도 저비용으로 얻을 수 있는 유일한 신호라, PC 계열
    # 세 유형(Windows/Linux/macOS) 모두의 채점에 소폭 반영한다 (icmp.classify_ttl_os_hint).
    os_ttl_hint: Optional[str] = None

    # AP
    lldp_capability_wlan: bool = False
    vendor_sysobjectid_ap: bool = False
    wireless_mib_present: bool = False

    # Hub 추정 (11.1절)
    distinct_mac_vendor_count_on_ports: int = 0
    has_management_response: bool = True

    extra: dict = field(default_factory=dict)


@dataclass
class ClassificationResult:
    device_type: str
    score: int
    method: str  # CONFIRMED | REVIEW_REQUIRED | STRONG_SIGNATURE
    detail: list[dict]
    all_scores: dict[str, int]


def _score_l2_switch(e: ClassificationEvidence) -> tuple[int, list[dict]]:
    detail = []
    score = 0
    if e.has_bridge_mib:
        score += 35
        detail.append({"evidence": "Bridge-MIB", "score": 35})
    if e.has_fdb:
        score += 25
        detail.append({"evidence": "FDB", "score": 25})
    if e.has_lldp:
        score += 15
        detail.append({"evidence": "LLDP", "score": 15})
    if e.physical_port_count >= 4:
        score += 15
        detail.append({"evidence": "다수 물리포트", "score": 15})
    if not e.has_ip_forwarding:
        score += 10
        detail.append({"evidence": "Routing 없음", "score": 10})

    # [KOS20260921] L2_SWITCH 채점 항목(Bridge/FDB/LLDP/포트 수)은 L3 스위치도
    # 대부분 동시에 만족한다(L3 스위치는 스위칭도 겸하므로). 이 때문에 SNMP상
    # ipForwarding/Route 근거가 약한 L3 스위치가 항상 L2_SWITCH에 밀려 오분류되는
    # 문제가 있었다(예: NSH-3228). 명확한 L3 근거(Route 존재 또는 SVI 2개 이상)가
    # 있으면 L2_SWITCH를 CONFIRMED 임계치 미만으로 제한해 L3_SWITCH가 경쟁할 수
    # 있게 한다.
    if e.route_table_size > 0 or e.svi_or_l3_if_count >= 2:
        score = min(score, config.CLASSIFICATION_THRESHOLD - 1)
        detail.append({"evidence": "L3 근거 존재로 L2 확정 억제", "score": 0})
    return score, detail


def _score_l3_switch(e: ClassificationEvidence) -> tuple[int, list[dict]]:
    detail = []
    score = 0
    if e.has_bridge_mib or e.has_fdb:
        score += 25
        detail.append({"evidence": "Bridge/FDB", "score": 25})
    if e.has_lldp:
        score += 10
        detail.append({"evidence": "LLDP", "score": 10})
    if e.has_ip_forwarding:
        score += 20
        detail.append({"evidence": "IP forwarding", "score": 20})
    if e.route_table_size > 0:
        score += 20
        detail.append({"evidence": "Route table", "score": 20})
    if e.svi_or_l3_if_count >= 2:
        score += 15
        detail.append({"evidence": "다수 SVI/L3 IF", "score": 15})
    if e.vlan_count >= 2:
        score += 10
        detail.append({"evidence": "VLAN", "score": 10})
    return score, detail


def _score_router(e: ClassificationEvidence) -> tuple[int, list[dict]]:
    detail = []
    score = 0
    if e.has_ip_forwarding:
        score += 30
        detail.append({"evidence": "IP forwarding", "score": 30})
    if e.route_table_size > 0:
        score += 30
        detail.append({"evidence": "Route table", "score": 30})
    if e.svi_or_l3_if_count >= 2:
        score += 20
        detail.append({"evidence": "다수 L3 IF", "score": 20})
    if e.bridge_capability_weak or not e.has_bridge_mib:
        score += 10
        detail.append({"evidence": "Bridge 기능 약함", "score": 10})
    if e.has_wan_or_default_route:
        score += 10
        detail.append({"evidence": "WAN/Default route", "score": 10})
    return score, detail


def _score_ip_camera(e: ClassificationEvidence) -> tuple[int, list[dict]]:
    detail = []
    score = 0
    if e.has_onvif:
        score += 45
        detail.append({"evidence": "ONVIF", "score": 45})
    if e.has_rtsp:
        score += 20
        detail.append({"evidence": "RTSP", "score": 20})
    if e.oui_category == "CAMERA":
        score += 15
        detail.append({"evidence": "Camera OUI", "score": 15})
    if e.http_title_hint:
        score += 10
        detail.append({"evidence": "HTTP title/model", "score": 10})
    if e.sys_descr:
        score += 10
        detail.append({"evidence": "SNMP sysDescr", "score": 10})
    return score, detail


def _score_windows_pc(e: ClassificationEvidence) -> tuple[int, list[dict]]:
    detail = []
    score = 0
    if e.has_wmi:
        score += 40
        detail.append({"evidence": "WMI/WinRM", "score": 40})
    if e.has_smb_or_rdp:
        score += 20
        detail.append({"evidence": "SMB/RDP", "score": 20})
    if e.windows_sysdescr:
        score += 20
        detail.append({"evidence": "Windows SNMP/sysDescr", "score": 20})
    if e.has_netbios_or_dns:
        score += 10
        detail.append({"evidence": "NetBIOS/DNS", "score": 10})
    if e.os_ttl_hint == "WINDOWS":
        score += 15
        detail.append({"evidence": "ICMP TTL(~128) Windows 추정", "score": 15})
    return score, detail


def _score_linux_pc(e: ClassificationEvidence) -> tuple[int, list[dict]]:
    detail = []
    score = 0
    if e.has_ssh_banner:
        score += 20
        detail.append({"evidence": "SSH banner", "score": 20})
    if e.linux_sysdescr:
        score += 35
        detail.append({"evidence": "Linux SNMP/sysDescr", "score": 35})
    if e.linux_service_fingerprint:
        score += 15
        detail.append({"evidence": "Linux service fingerprint", "score": 15})
    if e.has_ssh_uname:
        score += 30
        detail.append({"evidence": "uname via SSH", "score": 30})
    if e.os_ttl_hint == "UNIX":
        score += 15
        detail.append({"evidence": "ICMP TTL(~64) Unix계열 추정", "score": 15})
    return score, detail


def _score_mac_pc(e: ClassificationEvidence) -> tuple[int, list[dict]]:
    detail = []
    score = 0
    if e.has_mdns_bonjour:
        score += 30
        detail.append({"evidence": "mDNS/Bonjour", "score": 30})
    if e.oui_category == "APPLE":
        score += 20
        detail.append({"evidence": "Apple OUI", "score": 20})
    if e.macos_fingerprint:
        score += 30
        detail.append({"evidence": "SSH/macOS fingerprint", "score": 30})
    if e.macos_sysdescr:
        # [KOS20260920] 이전에는 `if e.sys_descr:`(빈 문자열이 아니기만 하면 True)로
        # 잘못 구현되어 SNMP가 있는 모든 장비가 MAC_PC 점수를 얻는 버그가 있었다.
        # macOS를 실제로 가리키는 sysDescr(예: "Darwin", "Mac OS")일 때만 가산한다.
        score += 20
        detail.append({"evidence": "SNMP sysDescr(macOS)", "score": 20})
    if e.os_ttl_hint == "UNIX":
        score += 10
        detail.append({"evidence": "ICMP TTL(~64) Unix계열 추정", "score": 10})
    return score, detail


def _score_access_point(e: ClassificationEvidence) -> tuple[int, list[dict]]:
    detail = []
    score = 0
    if e.lldp_capability_wlan:
        score += 25
        detail.append({"evidence": "LLDP capability WLAN", "score": 25})
    if e.oui_category == "AP":
        score += 20
        detail.append({"evidence": "AP OUI", "score": 20})
    if e.vendor_sysobjectid_ap:
        score += 30
        detail.append({"evidence": "Vendor sysObjectID", "score": 30})
    if e.wireless_mib_present:
        score += 25
        detail.append({"evidence": "무선 관련 MIB", "score": 25})
    return score, detail


def _score_hub(e: ClassificationEvidence) -> tuple[int, list[dict]]:
    """4장 표: '다수 MAC 공유, 관리 MIB 부재' / 11.1절 Hub 추정 로직."""
    detail = []
    score = 0
    if not e.has_management_response:
        score += 40
        detail.append({"evidence": "관리 MIB 부재", "score": 40})
    if e.distinct_mac_vendor_count_on_ports >= 3:
        score += 40
        detail.append({"evidence": "다수 Vendor MAC 공유", "score": 40})
    return score, detail


_SCORERS = {
    "L2_SWITCH": _score_l2_switch,
    "L3_SWITCH": _score_l3_switch,
    "ROUTER": _score_router,
    "IP_CAMERA": _score_ip_camera,
    "WINDOWS_PC": _score_windows_pc,
    "LINUX_PC": _score_linux_pc,
    "MAC_PC": _score_mac_pc,
    "ACCESS_POINT": _score_access_point,
    "HUB": _score_hub,
}


def _apply_poe_variant(device_type: str, e: ClassificationEvidence) -> str:
    if device_type == "L2_SWITCH" and e.has_poe_mib:
        return "L2_POE_SWITCH"
    if device_type == "L3_SWITCH" and e.has_poe_mib:
        return "L3_POE_SWITCH"
    return device_type


# [KOS20260921] 사용자가 확인한 이 배포 환경의 스위치 명명 규칙: "CCC-NNNNP".
# CCC가 NSH/NHM인 경우에 한정해, 모델 번호(NNNN)의 첫 자리가 3이면 L3, 2이면
# L2이며, 숫자 뒤에 'P'가 바로 붙으면 PoE 장비다. NST(enterprise 38333) 사설
# PoE MIB는 PoE 미지원 모델에서도 응답해 SNMP 근거만으로는 모든 스위치가 PoE로
# 오탐됐고, L2_SWITCH 채점 항목(Bridge/FDB/LLDP/포트 수)은 L3 스위치도 대부분
# 만족해 L3 스위치가 항상 L2로 밀려 오분류됐다. hostname이 이 규칙에 맞는
# 장비는 SNMP 점수 경쟁 없이 명명 규칙을 그대로 신뢰한다(강한 시그니처).
_NSH_NHM_MODEL_PATTERN = re.compile(r"^(?:NSH|NHM)-?(\d)(\d{2,3})(P)?", re.IGNORECASE)


def _parse_nsh_nhm_model_naming(hostname: str) -> tuple[Optional[str], bool]:
    match = _NSH_NHM_MODEL_PATTERN.match((hostname or "").strip())
    if not match:
        return None, False
    is_poe = match.group(3) is not None
    layer = "L3" if match.group(1) == "3" else "L2" if match.group(1) == "2" else None
    return layer, is_poe


def classify_device_type(
    evidence: ClassificationEvidence,
    threshold: int = config.CLASSIFICATION_THRESHOLD,
    min_margin: int = config.CLASSIFICATION_MIN_MARGIN,
) -> ClassificationResult:
    all_scores: dict[str, int] = {}
    all_details: dict[str, list[dict]] = {}
    for type_name, scorer in _SCORERS.items():
        score, detail = scorer(evidence)
        all_scores[type_name] = score
        all_details[type_name] = detail

    ranked = sorted(all_scores.items(), key=lambda kv: kv[1], reverse=True)
    best_type, best_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0

    # 8.1절: 강한 시그니처(ONVIF Device Service 정상 응답)는 약한 포트 스캔 증거보다 우선한다.
    if evidence.onvif_device_service_ok:
        detail = all_details["IP_CAMERA"] + [{"evidence": "ONVIF Device Service 정상 응답(강한 시그니처)", "score": 0}]
        return ClassificationResult(
            device_type="IP_CAMERA",
            score=max(all_scores["IP_CAMERA"], threshold),
            method="STRONG_SIGNATURE",
            detail=detail,
            all_scores=all_scores,
        )

    if evidence.has_bridge_mib or evidence.has_fdb:
        naming_layer, naming_poe = _parse_nsh_nhm_model_naming(evidence.hostname)
        if naming_layer is not None:
            base_type = "L3_SWITCH" if naming_layer == "L3" else "L2_SWITCH"
            resolved_type = (
                ("L3_POE_SWITCH" if base_type == "L3_SWITCH" else "L2_POE_SWITCH") if naming_poe else base_type
            )
            detail = all_details[base_type] + [
                {"evidence": f"명명 규칙(CCC-NNNNP, hostname={evidence.hostname})", "score": 0}
            ]
            return ClassificationResult(
                device_type=resolved_type,
                score=max(all_scores[base_type], threshold),
                method="STRONG_SIGNATURE",
                detail=detail,
                all_scores=all_scores,
            )

    if best_score >= threshold and (best_score - second_score) >= min_margin:
        resolved_type = _apply_poe_variant(best_type, evidence)
        return ClassificationResult(
            device_type=resolved_type,
            score=best_score,
            method="CONFIRMED",
            detail=all_details[best_type],
            all_scores=all_scores,
        )

    # [KOS20260920] WMI/SSH 자격증명 없이는 Windows/Linux/macOS가 임계치(60점)를
    # 넘기 어려운 경우가 많다. 근거가 아예 없는 것(0~24점)과 부분 근거는 있지만
    # 확정하기엔 부족한 것(25점 이상)을 구분해, 후자는 device_type에 가장 유력한
    # 후보를 노출하고 method=REVIEW_REQUIRED로 "미확정"임을 함께 표시한다.
    if best_score >= config.CLASSIFICATION_REVIEW_FLOOR:
        return ClassificationResult(
            device_type=best_type,
            score=best_score,
            method="REVIEW_REQUIRED",
            detail=all_details[best_type],
            all_scores=all_scores,
        )

    return ClassificationResult(
        device_type="UNKNOWN",
        score=best_score,
        method="REVIEW_REQUIRED",
        detail=all_details.get(best_type, []),
        all_scores=all_scores,
    )
