"""6.4절: Auto-Discovery Profile과 Device Template.

큰 네트워크에서 모든 프로토콜을 매번 실행하지 않도록 Profile별로 수집 범위를
분리한다 (표 6.4절: LIGHT/STANDARD/DETAILED/TEMPLATE).
"""
from __future__ import annotations

from dataclasses import dataclass

PROFILES = ("LIGHT", "STANDARD", "DETAILED", "TEMPLATE")


@dataclass(frozen=True)
class ProfileScope:
    collect_interfaces: bool
    collect_lldp: bool
    collect_fdb: bool
    collect_arp: bool
    collect_routes: bool
    collect_poe: bool
    collect_vlans: bool  # Q-BRIDGE-MIB VLAN 목록/PVID 수집 (6.4절: DETAILED 범위)
    expand_neighbors: bool  # 재귀 탐색으로 새 이웃을 큐에 추가할지 여부


PROFILE_SCOPES: dict[str, ProfileScope] = {
    "LIGHT": ProfileScope(
        collect_interfaces=False,
        collect_lldp=False,
        collect_fdb=False,
        collect_arp=False,
        collect_routes=False,
        collect_poe=False,
        collect_vlans=False,
        expand_neighbors=False,
    ),
    "STANDARD": ProfileScope(
        collect_interfaces=True,
        collect_lldp=True,
        collect_fdb=True,
        collect_arp=True,
        collect_routes=False,
        collect_poe=True,
        collect_vlans=False,
        expand_neighbors=True,
    ),
    "DETAILED": ProfileScope(
        collect_interfaces=True,
        collect_lldp=True,
        collect_fdb=True,
        collect_arp=True,
        collect_routes=True,
        collect_poe=True,
        collect_vlans=True,
        expand_neighbors=True,
    ),
    "TEMPLATE": ProfileScope(
        collect_interfaces=True,
        collect_lldp=True,
        collect_fdb=True,
        collect_arp=True,
        collect_routes=True,
        collect_poe=True,
        collect_vlans=True,
        expand_neighbors=False,
    ),
}


def get_profile_scope(profile: str) -> ProfileScope:
    if profile not in PROFILE_SCOPES:
        raise ValueError(f"알 수 없는 Discovery Profile: {profile}")
    return PROFILE_SCOPES[profile]


# [KOS20260921] Discovery 시작 화면에서 Profile별로 실제 어떤 프로토콜/MIB이
# 적용되는지 사용자가 직접 확인할 수 있도록, ProfileScope 필드 순서·라벨을
# 고정해 둔다 (routes_discovery.py::get_profile_scopes에서 사용).
PROTOCOL_LABELS: tuple[tuple[str, str], ...] = (
    ("collect_interfaces", "IF-MIB 인터페이스 목록/상태"),
    # [KOS20260922] STP는 별도 ProfileScope 필드가 없고 collect_interfaces와
    # 같은 조건으로 수집된다(discovery/engine.py 참고) - 같은 key를 재사용해
    # Profile 비교 표에 별도 행으로 노출한다.
    ("collect_interfaces", "BRIDGE-MIB STP (Root Bridge 판정 / Port Forwarding·Blocking 상태 / Designated Bridge 기반 링크 추론)"),
    ("collect_lldp", "LLDP-MIB 이웃 탐색 (토폴로지 연결 근거)"),
    ("collect_fdb", "BRIDGE-MIB / Q-BRIDGE-MIB FDB(MAC 테이블)"),
    ("collect_arp", "IP-MIB ARP 테이블"),
    ("collect_routes", "IP-MIB Route/ipAddrTable (L3·SVI 판정 근거)"),
    ("collect_poe", "POWER-ETHERNET-MIB / 사설 PoE MIB"),
    ("collect_vlans", "Q-BRIDGE-MIB VLAN 목록 및 Port PVID"),
)
