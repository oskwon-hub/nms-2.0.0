"""5장: 수집 프로토콜 설계 - 공통 데이터 구조.

각 프로토콜 Collector는 이 표준화된 dataclass들을 반환하여, Discovery/Classification/
Topology 엔진이 프로토콜 세부사항과 무관하게 동작하도록 한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


class CollectorError(Exception):
    """수집 실패(Timeout, 권한 없음 등)를 나타낸다. Discovery는 이를 개별 오류로 기록하고 계속 진행한다."""


@dataclass
class SystemInfo:
    sys_descr: str = ""
    sys_object_id: str = ""
    sys_name: str = ""
    sys_location: str = ""
    sys_uptime: Optional[int] = None
    snmp_engine_id: Optional[str] = None
    vendor: Optional[str] = None
    model: Optional[str] = None
    serial_number: Optional[str] = None
    os_name: Optional[str] = None
    os_version: Optional[str] = None


@dataclass
class InterfaceInfo:
    if_index: int
    name: str
    mac: Optional[str] = None
    admin_status: str = "UNKNOWN"  # UP|DOWN
    oper_status: str = "UNKNOWN"
    speed_mbps: Optional[int] = None
    poe_capable: bool = False
    in_octets: Optional[int] = None
    out_octets: Optional[int] = None
    # [KOS20260922] BRIDGE-MIB dot1dStpPortState: DISABLED|BLOCKING|LISTENING|
    # LEARNING|FORWARDING|BROKEN. STP를 지원하지 않거나 이 포트가 STP 대상이
    # 아니면 None.
    stp_state: Optional[str] = None
    # [KOS20260922] BRIDGE-MIB dot1dStpPortDesignatedBridge - 이 포트가 속한
    # 세그먼트의 Designated Bridge MAC. LLDP-MIB을 지원하지 않는 장비가 많아
    # (실사용 데이터로 확인) LLDP만으로는 놓치는 실제 이웃 관계를, STP를 지원하는
    # 장비라면(LLDP보다 훨씬 보편적) 이 필드로 알아낼 수 있다. 이 값이 자기
    # 자신의 Bridge MAC이면(정상적인 자기 세그먼트 지정 또는 루프) 실제 이웃이
    # 아니므로 topology/engine.py에서 링크를 만들지 않는다.
    stp_designated_bridge_mac: Optional[str] = None


@dataclass
class StpInfo:
    """BRIDGE-MIB dot1dStp 그룹 - 이 장비가 STP Root Bridge인지 여부.

    dot1dStpRootPort == 0이면 이 장비 자신이 Root Bridge라는 뜻이다(RFC 1493).
    supported=False면 장비가 BRIDGE-MIB STP 그룹 자체를 지원하지 않는 것이므로,
    호출부는 이전에 저장된 is_stp_root 값을 덮어쓰지 않아야 한다.
    """

    is_root: bool = False
    supported: bool = False


@dataclass
class LldpNeighborInfo:
    local_if_index: Optional[int]
    remote_chassis_id: Optional[str]
    remote_port_id: Optional[str]
    remote_sys_name: Optional[str]
    remote_mgmt_ip: Optional[str]
    remote_capabilities: Optional[str] = None
    protocol: str = "LLDP"  # LLDP|CDP


@dataclass
class FdbEntryInfo:
    if_index: Optional[int]
    vlan: Optional[int]
    mac: str
    entry_type: str = "DYNAMIC"


@dataclass
class ArpEntryInfo:
    if_index: Optional[int]
    ip: str
    mac: str
    state: str = "REACHABLE"


@dataclass
class RouteInfo:
    destination: str
    prefix_len: int
    next_hop: Optional[str]
    if_index: Optional[int]
    metric: Optional[int] = None
    route_type: str = "STATIC"


@dataclass
class PoeInfo:
    if_index: int
    enabled: bool
    status: str = "UNKNOWN"  # DELIVERING|SEARCHING|FAULT|DISABLED
    power_mw: Optional[float] = None
    voltage_v: Optional[float] = None
    current_ma: Optional[float] = None
    poe_class: Optional[str] = None


@dataclass
class ProbeResult:
    """ICMP Seed Discovery 결과 (6.1절)."""

    ip: str
    alive: bool
    rtt_ms: Optional[float] = None
    loss_percent: float = 100.0
    ttl: Optional[int] = None


@dataclass
class CredentialProfile:
    """18.1절 Credential Profile 개념의 최소 구현. 값은 참조 ID가 아닌 평문 보관을
    현 MVP 범위에서는 메모리/설정 파일로 제한하고, DB에는 저장하지 않는다."""

    snmp_community: str = "public"
    snmp_version: str = "2c"
    ssh_username: Optional[str] = None
    ssh_password: Optional[str] = None
    ssh_pkey_path: Optional[str] = None
    extra: dict = field(default_factory=dict)
