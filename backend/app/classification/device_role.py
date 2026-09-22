"""9~11장: Core/Distribution/Floor/Access Switch 및 Endpoint Role 식별 로직.

Core/Floor 가중치는 설계서 9장·10.2절·부록 A에 제시된 예시 가중치를 그대로
반영한다. Distribution은 설계서에 수치 가중치표가 없고 정성적 판정 근거(10.1절)
만 제시되어 있어, 동일한 가중합 방식으로 합리적으로 확장한 값이다(코드 주석에
근거를 명시). 모든 결과는 "후보 추천" 성격이며 role_source=MANUAL인 장비는
자동 재계산이 덮어쓰지 않는다(9장 마지막 bullet, 2.1절 원칙).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app import config

INFRASTRUCTURE_TYPES = {"L2_SWITCH", "L2_POE_SWITCH", "L3_SWITCH", "L3_POE_SWITCH", "ROUTER"}
EDGE_TYPES = {"ACCESS_POINT", "HUB"}
ENDPOINT_TYPES = {"LINUX_PC", "WINDOWS_PC", "MAC_PC", "IP_CAMERA"}

DEVICE_ROLES = (
    "CORE_SWITCH",
    "DISTRIBUTION_SWITCH",
    "FLOOR_SWITCH",
    "ACCESS_SWITCH",
    "EDGE_DEVICE",
    "SERVER",
    "ENDPOINT",
    "UNKNOWN",
)

_FLOOR_NAME_PATTERN = re.compile(r"(FLOOR\s?-?\d+|[0-9]+\s?F\b|SW[-_]?\d*F)", re.IGNORECASE)
_FLOOR_LOCATION_PATTERN = re.compile(r"(\d+\s?F\b|\d+\s?층)", re.IGNORECASE)


@dataclass
class RoleEvidence:
    device_type: str
    layer3_capable: bool = False
    switch_neighbor_count: int = 0
    graph_degree: int = 0
    vlan_count: int = 0
    subnet_route_diversity: int = 0
    uplink_trunk_count: int = 0
    graph_centrality: float = 0.0  # 0.0 ~ 1.0 정규화된 중심성
    discovery_depth: int = 0

    parent_switch_count: int = 0
    downstream_switch_count: int = 0
    endpoint_density: int = 0
    core_neighbor: bool = False
    partial_l3: bool = False

    hostname: str = ""
    sys_location: str = ""

    is_server_fingerprint: bool = False
    extra: dict = field(default_factory=dict)


@dataclass
class RoleResult:
    device_role: str
    score: int
    method: str
    detail: list[dict]


def _core_score(e: RoleEvidence) -> tuple[int, list[dict]]:
    detail = []
    l3_score = 20 if e.layer3_capable else 0
    detail.append({"evidence": "L3 Capability", "score": l3_score})

    neighbor_score = min(e.switch_neighbor_count * 3, 20)
    detail.append({"evidence": f"Switch Neighbor 수({e.switch_neighbor_count})", "score": neighbor_score})

    degree_score = min(round(e.graph_degree * 1.5), 15)
    detail.append({"evidence": f"Graph Degree({e.graph_degree})", "score": degree_score})

    vlan_score = min(e.vlan_count // 4, 10)
    detail.append({"evidence": f"VLAN 수({e.vlan_count})", "score": vlan_score})

    subnet_score = min(e.subnet_route_diversity * 2, 10)
    detail.append({"evidence": f"Subnet/Route 다양성({e.subnet_route_diversity})", "score": subnet_score})

    uplink_score = min(e.uplink_trunk_count * 2, 10)
    detail.append({"evidence": f"Uplink/Trunk 수({e.uplink_trunk_count})", "score": uplink_score})

    centrality_score = min(round(e.graph_centrality * 10), 10)
    detail.append({"evidence": f"Graph Centrality({e.graph_centrality:.2f})", "score": centrality_score})

    depth_score = max(5 - e.discovery_depth + 1, 0)
    depth_score = min(depth_score, 5)
    detail.append({"evidence": f"Discovery Depth({e.discovery_depth})", "score": depth_score})

    total = (
        l3_score + neighbor_score + degree_score + vlan_score + subnet_score + uplink_score + centrality_score + depth_score
    )

    # 9장 bullet: "Core 후보는 L3 기능이 없으면 기본적으로 높은 점수를 받을 수 없도록 Gate 조건을 둔다."
    if not e.layer3_capable:
        total = min(total, config.CORE_SCORE_THRESHOLD - 1)
        detail.append({"evidence": "L3 미지원 Gate 적용", "score": 0})

    return total, detail


def _distribution_score(e: RoleEvidence) -> tuple[int, list[dict]]:
    """10.1절 정성적 판정 근거를 가중합으로 확장 구현 (설계서에 수치 미제공, 합리적 확장)."""
    detail = []
    core_link_score = 25 if e.core_neighbor else 0
    detail.append({"evidence": "상위 Core 연결", "score": core_link_score})

    downstream_score = min(e.downstream_switch_count * 5, 25)
    detail.append({"evidence": f"하위 Switch 다수({e.downstream_switch_count})", "score": downstream_score})

    l3_score = 15 if e.partial_l3 else 0
    detail.append({"evidence": "L3 가능성(SVI/Route 일부)", "score": l3_score})

    trunk_score = min(e.uplink_trunk_count * 4, 20)
    detail.append({"evidence": "Trunk 비중", "score": trunk_score})

    depth_score = 15 if e.discovery_depth in (1, 2) else 0
    detail.append({"evidence": f"Graph 위치(Depth {e.discovery_depth})", "score": depth_score})

    total = core_link_score + downstream_score + l3_score + trunk_score + depth_score
    return total, detail


def _floor_score(e: RoleEvidence) -> tuple[int, list[dict]]:
    detail = []
    parent_score = 20 if 1 <= e.parent_switch_count <= 2 else (10 if e.parent_switch_count > 2 else 0)
    detail.append({"evidence": f"상위 Switch({e.parent_switch_count})", "score": parent_score})

    endpoint_score = min(round(e.endpoint_density / 28 * 25), 25)
    detail.append({"evidence": f"Endpoint 다수({e.endpoint_density})", "score": endpoint_score})

    downstream_penalty = max(e.downstream_switch_count - 1, 0) * 5
    downstream_score = max(10 - downstream_penalty, -10)
    detail.append({"evidence": f"하위 Switch 적음({e.downstream_switch_count})", "score": downstream_score})

    l2_score = 15 if not e.layer3_capable else 0
    detail.append({"evidence": "L2 중심(Route 없음)", "score": l2_score})

    naming_score = 15 if _FLOOR_NAME_PATTERN.search(e.hostname or "") else 0
    detail.append({"evidence": "Naming", "score": naming_score})

    syslocation_score = 15 if _FLOOR_LOCATION_PATTERN.search(e.sys_location or "") else 0
    detail.append({"evidence": "sysLocation", "score": syslocation_score})

    total = parent_score + endpoint_score + downstream_score + l2_score + naming_score + syslocation_score
    return total, detail


def extract_floor_label(hostname: str, sys_location: str) -> tuple[str | None, str | None]:
    """sysName/sysLocation에서 층 번호를 추출한다 (10.2절 bullet)."""
    for text, source in ((sys_location or "", "sysLocation"), (hostname or "", "sysName")):
        match = _FLOOR_LOCATION_PATTERN.search(text) or _FLOOR_NAME_PATTERN.search(text)
        if match:
            digits = re.search(r"\d+", match.group(0))
            if digits:
                return digits.group(0), source
    return None, None


def classify_device_role(evidence: RoleEvidence) -> RoleResult:
    device_type = evidence.device_type

    if device_type in INFRASTRUCTURE_TYPES:
        core_total, core_detail = _core_score(evidence)
        if core_total >= config.CORE_SCORE_THRESHOLD:
            return RoleResult("CORE_SWITCH", core_total, "CONFIRMED", core_detail)

        dist_total, dist_detail = _distribution_score(evidence)
        if dist_total >= config.FLOOR_SCORE_THRESHOLD:
            return RoleResult("DISTRIBUTION_SWITCH", dist_total, "CONFIRMED", dist_detail)

        floor_total, floor_detail = _floor_score(evidence)
        if floor_total >= config.FLOOR_SCORE_THRESHOLD:
            return RoleResult("FLOOR_SWITCH", floor_total, "CONFIRMED", floor_detail)

        # [KOS20260921] best_score만 max()로 고르고 detail은 항상 dist_detail/
        # floor_detail을 고정으로 붙이면, 실제로 점수가 가장 높았던 항목과 화면에
        # 보이는 근거 목록이 서로 어긋나는 버그가 있었다(예: floor_total이 가장
        # 높은데 dist_detail의 합계는 그보다 작은 값으로 표시됨 - 실사용 중
        # "NST-Router" role_score=50인데 표시된 근거 합계는 25인 불일치로 발견).
        # 항상 실제로 최댓값을 만든 항목의 detail을 함께 반환한다.
        candidates = [(core_total, core_detail), (dist_total, dist_detail), (floor_total, floor_detail)]
        best_score = max(score for score, _detail in candidates)
        best_detail = next(detail for score, detail in candidates if score == best_score)

        # [KOS20260921] ACCESS_SWITCH는 순수 L2 Edge 계층을 뜻한다(9~11장 계층
        # 구조: Core/Distribution=L3, Floor/Access=L2). L3 스위치/라우터는
        # Core/Distribution/Floor 임계치를 못 넘었다고 해서 Access(L2 전용)로
        # 떨어질 수 없다 - 사용자 확인: "L3인데 ACCESS_SWITCH로 나오면 안 된다".
        # 이 경우 확신은 낮아도 최소 DISTRIBUTION_SWITCH로 남긴다.
        if evidence.layer3_capable:
            return RoleResult("DISTRIBUTION_SWITCH", best_score, "FALLBACK", best_detail)

        # 11.1절: Floor 추론이 불확실하면 device_role은 ACCESS_SWITCH로 두고
        # physical_floor는 미지정한다 (10.2절 bullet).
        return RoleResult("ACCESS_SWITCH", best_score, "FALLBACK", best_detail)

    if device_type in EDGE_TYPES:
        return RoleResult("EDGE_DEVICE", 0, "TYPE_MAPPED", [])

    if device_type in ENDPOINT_TYPES:
        if evidence.is_server_fingerprint:
            return RoleResult("SERVER", 0, "TYPE_MAPPED", [])
        return RoleResult("ENDPOINT", 0, "TYPE_MAPPED", [])

    return RoleResult("UNKNOWN", 0, "UNRESOLVED", [])
