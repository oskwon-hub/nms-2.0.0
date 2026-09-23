"""16장 REST API 응답/요청 Pydantic 스키마."""
from __future__ import annotations

import datetime as dt
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class DeviceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    hostname: Optional[str]
    management_ip: Optional[str]
    primary_mac: Optional[str]
    vendor: Optional[str]
    model: Optional[str]
    serial_number: Optional[str]
    device_type: str
    device_role: str
    role_source: str
    classification_score: int
    role_score: int
    status: str
    physical_floor: Optional[str]
    discovery_depth: int
    layer2_capable: bool
    layer3_capable: bool
    poe_capable: bool
    # [KOS20260922] BRIDGE-MIB dot1dStpRootPort==0으로 확인된 STP Root Bridge 여부.
    is_stp_root: bool
    first_seen_at: dt.datetime
    last_seen_at: dt.datetime


class DeviceDetailOut(DeviceOut):
    sys_location: Optional[str]
    # [KOS20260921] vendor마다 sysDescr 형식이 달라 model을 안전하게 자동 파싱할
    # 수 없어, SNMP sysDescr 원문을 그대로 노출해 사용자가 직접 모델을 확인할 수
    # 있게 한다("장비 정보에 모델명이 있는데 표시가 안 된다" 리포트 대응).
    sys_descr: Optional[str]
    classification_method: Optional[str]
    classification_detail: Optional[str]
    role_detail: Optional[str]
    floor_source: Optional[str]


class DiagnosticHopOut(BaseModel):
    hop: int
    ip: Optional[str]
    device_id: Optional[int] = None
    hostname: Optional[str] = None
    device_role: Optional[str] = None


class DeviceDiagnosticOut(BaseModel):
    target: str
    source_ip: Optional[str] = None
    command: str
    protocol: str
    success: bool
    output: str
    hops: list[DiagnosticHopOut] = Field(default_factory=list)


class L2SwitchEvidenceOut(BaseModel):
    switch_id: int
    hostname: Optional[str]
    sys_name: Optional[str] = None
    management_ip: Optional[str]
    sys_descr: Optional[str] = None
    device_type: str
    vlan: Optional[int]
    source_ports: list[str]
    target_ports: list[str]
    source_seen_at: Optional[dt.datetime]
    target_seen_at: Optional[dt.datetime]
    relation: str


class L2PathEvidenceOut(BaseModel):
    source_ip: Optional[str]
    source_mac: Optional[str]
    target_ip: str
    target_mac: Optional[str]
    switches: list[L2SwitchEvidenceOut] = Field(default_factory=list)


class InterfaceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    device_id: int
    if_index: int
    name: Optional[str]
    mac: Optional[str]
    admin_status: str
    oper_status: str
    speed_mbps: Optional[int]
    vlan: Optional[int]
    is_trunk: bool
    is_uplink: bool
    is_downlink: bool
    is_protected: bool
    protected_reason: Optional[str]
    poe_capable: bool
    # [KOS20260921] IF-MIB ifInOctets/ifOutOctets를 마지막 수집 시점 값 그대로 노출한다.
    # 누적 카운터이므로 순간 전송률이 아니라 last_seen_at까지 누적된 바이트다.
    in_octets: Optional[int]
    out_octets: Optional[int]
    # [KOS20260922] BRIDGE-MIB dot1dStpPortState. STP 미지원/비대상 포트는 None.
    stp_state: Optional[str]


class NeighborOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    local_interface_id: Optional[int]
    remote_chassis_id: Optional[str]
    remote_port_id: Optional[str]
    remote_sys_name: Optional[str]
    remote_mgmt_ip: Optional[str]
    protocol: str
    last_seen_at: dt.datetime


class FdbOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    interface_id: Optional[int]
    vlan: Optional[int]
    mac: str
    entry_type: str
    last_seen_at: dt.datetime


class ArpOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    interface_id: Optional[int]
    ip: str
    mac: str
    state: str
    last_seen_at: dt.datetime


class RouteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    destination: str
    prefix_len: int
    next_hop: Optional[str]
    interface_id: Optional[int]
    route_type: str


class PoeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    interface_id: int
    enabled: bool
    status: str
    power_mw: Optional[float]
    poe_class: Optional[str]


class TopologyNodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    hostname: Optional[str]
    sys_name: Optional[str] = None
    management_ip: Optional[str]
    sys_descr: Optional[str]
    # [KOS20260921] Topology 화면에 IP/MAC 검색 기능을 추가하기 위해 노출한다.
    primary_mac: Optional[str]
    device_type: str
    device_role: str
    status: str
    discovery_depth: int
    # [KOS20260922] Topology 그래프에서 STP Root 노드를 시각적으로 표시하기 위해 노출한다.
    is_stp_root: bool


class TopologyLinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    src_device_id: int
    src_interface_id: Optional[int]
    dst_device_id: int
    dst_interface_id: Optional[int]
    source: str
    label: Optional[str] = None
    confidence: int
    status: str
    first_seen_at: dt.datetime
    last_seen_at: dt.datetime
    # [KOS20260921] src/dst_interface_id는 있지만 이름이 없어 Topology 화면에서
    # 포트 번호를 보여줄 수 없었다. routes_topology.py에서 DeviceInterface.name을
    # 채워 넣는다(둘 다 없거나 하나만 있을 수 있음 - ARP-only 근거는 대개 둘 다 None).
    src_port: Optional[str] = None
    dst_port: Optional[str] = None
    # [KOS20260922] STP로 차단(BLOCKING)된 링크를 Topology에서 구분해 보여주기
    # 위해 노출한다(포트가 알려진 경우에만 채워짐 - routes_topology.py 참고).
    src_stp_state: Optional[str] = None
    dst_stp_state: Optional[str] = None
    # [KOS20260921] 두 장비의 discovery_depth를 비교해 계산한 값. HIERARCHICAL이면
    # depth가 더 큰(하위) 쪽 장비 관점에서 이 링크는 Uplink다(반대쪽에서는 Downlink).
    # 같은 depth끼리의 연결(Peer/이중화)이면 PEER.
    link_role: str = "PEER"


class TopologyOut(BaseModel):
    nodes: list[TopologyNodeOut]
    links: list[TopologyLinkOut]


class LinkPingOut(BaseModel):
    link_id: int
    from_device_id: int
    from_ip: str
    to_device_id: int
    to_ip: str
    supported: bool
    success: bool
    protocol: Optional[str] = None
    command: Optional[str] = None
    packet_loss_percent: Optional[float] = None
    rtt_min_ms: Optional[float] = None
    rtt_avg_ms: Optional[float] = None
    rtt_max_ms: Optional[float] = None
    output: str


class LinkPingIn(BaseModel):
    direction: str = "FORWARD"
    protocol: Optional[str] = None
    username: Optional[str] = Field(default=None, min_length=1, max_length=128)
    password: Optional[str] = Field(default=None, min_length=1, max_length=512)
    port: Optional[int] = Field(default=None, ge=1, le=65535)


class ManualTopologyLinkIn(BaseModel):
    src_device_id: int
    dst_device_id: int
    label: str = Field(min_length=1, max_length=255)


class ProfileProtocolOut(BaseModel):
    key: str
    label: str
    enabled: bool


class ProfileScopeOut(BaseModel):
    """[KOS20260921] Discovery 시작 화면에서 각 Profile이 실제로 수집하는 프로토콜/
    MIB을 사용자가 직접 확인할 수 있도록 6.4절 Profile 표를 그대로 노출한다."""

    profile: str
    expand_neighbors: bool
    protocols: list[ProfileProtocolOut]


class DiscoveryStartIn(BaseModel):
    seed_targets: Optional[list[str]] = None
    cidrs: Optional[list[str]] = None
    profile: str = "STANDARD"
    community: Optional[str] = None
    # [KOS20260921] 동시 스캔 개수(생략 시 NMS_DEFAULT_DISCOVERY_CONCURRENCY)와
    # hostname 식별 보조 규칙(DNS/NETBIOS/MDNS 중 선택, SNMP sysName이 없을 때만 시도).
    concurrency: Optional[int] = None
    hostname_rules: Optional[list[str]] = None
    # [KOS20260922] "기존 링크 정보를 모두 지우고 새로 하기" 옵션. 평소 Discovery는
    # 증분(기존 링크 재사용/갱신) 방식이라, 배선을 바꾼 뒤 예전 링크가 stale로만
    # 남고 안 지워지는 것을 답답해하는 사용자를 위해 명시적으로 초기화할 수 있게 한다.
    reset_links: bool = False
    # [KOS20260922] "장비 정보도 모두 지우고 새로 하기" 옵션. 장비를 지우면
    # FK CASCADE로 링크/인터페이스/ARP/FDB 등도 함께 지워지므로 reset_links보다
    # 훨씬 파괴적이다 - 전체 인벤토리를 이번 Discovery Run 결과로만 다시 채우고
    # 싶을 때만 사용한다.
    reset_devices: bool = False


class DiscoveryRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    profile: str
    cidr: Optional[str]
    status: str
    current_ip: Optional[str]
    current_ips: Optional[str]  # JSON-encoded list, 동시 스캔 중인 IP 전체
    started_at: dt.datetime
    ended_at: Optional[dt.datetime]
    scanned_count: int
    alive_count: int
    snmp_count: int
    switch_count: int
    camera_count: int
    pc_count: int
    unknown_count: int
    error_count: int
    concurrency: int
    hostname_rules: Optional[str]  # JSON-encoded list, e.g. '["DNS","NETBIOS"]'


class RoleUpdateIn(BaseModel):
    device_role: str
    performed_by: str = "operator"


class ControlActionIn(BaseModel):
    performed_by: str = "operator"
    force: bool = False


class VlanSetIn(BaseModel):
    vlan: int
    performed_by: str = "operator"
    force: bool = False


class DescriptionSetIn(BaseModel):
    description: str
    performed_by: str = "operator"
    force: bool = False


class ControlLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    device_id: int
    interface_id: Optional[int]
    action: str
    before_value: Optional[str]
    requested_value: Optional[str]
    after_value: Optional[str]
    result: str
    error_message: Optional[str]
    performed_by: str
    created_at: dt.datetime


class RestoreConfigChangeIn(BaseModel):
    performed_by: str = "operator"
    force: bool = False


class RestoreResultOut(BaseModel):
    result: str  # SUCCESS|FAILED|DENIED
    new_value: Optional[str]
    error_message: Optional[str]


class ConfigChangeLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    device_id: int
    interface_id: Optional[int]
    field_name: str
    old_value: Optional[str]
    new_value: Optional[str]
    source: str
    performed_by: Optional[str]
    detected_at: dt.datetime


class AlarmOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    severity: str
    category: str
    message: str
    occurred_at: dt.datetime
    device_id: Optional[int]
    link_id: Optional[int]
    discovery_run_id: Optional[int]
    suppressed: bool
    suppressed_reason: Optional[str]


class AlarmDismissEntryIn(BaseModel):
    id: str
    occurred_at: dt.datetime


class AlarmDismissIn(BaseModel):
    alarms: list[AlarmDismissEntryIn]


class BulkDeleteIn(BaseModel):
    ids: list[int]


class BulkDeleteOut(BaseModel):
    deleted: list[int]
    skipped: list[int] = []


class ReclassifyOut(BaseModel):
    changed: int


class DismissOut(BaseModel):
    dismissed_count: int


class CredentialProfileOut(BaseModel):
    """18.1절: Credential은 평문으로 재노출하지 않으므로 community 값 자체는
    응답에 절대 포함하지 않는다 - 이름/생성시각만 노출한다."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    has_snmp: bool
    has_ssh: bool
    has_telnet: bool
    has_cli: bool
    ssh_port: int
    cli_protocol: str
    created_at: dt.datetime
    updated_at: dt.datetime


class CredentialProfileCreateIn(BaseModel):
    snmp_community: str
    name: Optional[str] = None


class CredentialProfileSshUpdateIn(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=512)
    port: int = Field(default=22, ge=1, le=65535)
    protocol: str = "SSH"


class SystemInfoOut(BaseModel):
    schema_version: int
    db_path: str
    max_seed_hosts: int
    max_discovery_depth: int
    classification_threshold: int
    classification_review_floor: int
    core_score_threshold: int
    floor_score_threshold: int
    stale_after_seconds: int
    offline_after_seconds: int
    auto_discovery_enabled: bool
    auto_discovery_interval_seconds: int
