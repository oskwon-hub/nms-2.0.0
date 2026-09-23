"""SQLite3 스키마 정의 (설계서 15장, 15.2절).

표 15장에 정의된 10개 테이블 + schema_version(15.3절 자동 마이그레이션용) +
collector(3.2절 분산 Collector/Probe 아키텍처의 데이터 모델)를 정의한다.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

SCHEMA_VERSION = 14


def utcnow() -> dt.datetime:
    """SQLite는 timezone-aware datetime을 왕복 저장하지 못해(재조회 시 naive로
    돌아옴) 커밋 전/후 값 비교가 깨질 수 있으므로, 전체 스키마를 naive UTC로
    통일한다."""
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class SchemaVersion(Base):
    __tablename__ = "schema_version"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)


class CredentialProfile(Base):
    """18.1절: Credential Profile. 장비 필드에 평문을 직접 저장하지 않고, 이 테이블에
    암호화된 값으로 저장한 뒤 장비는 참조 ID(credential_profile_id)로만 연결한다.

    SNMP v2c community와 링크 원격 Ping에 사용하는 SSH 사용자명/암호를 다룬다.
    비밀값은 모두 암호화된 문자열만 저장하며 API 응답에는 설정 여부만 노출한다.
    """

    __tablename__ = "credential_profile"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    snmp_community_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ssh_username_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ssh_password_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ssh_port: Mapped[int] = mapped_column(Integer, nullable=False, default=22)
    cli_protocol: Mapped[str] = mapped_column(String(8), nullable=False, default="SSH")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(), default=utcnow, onupdate=utcnow)

    @property
    def has_snmp(self) -> bool:
        return bool(self.snmp_community_encrypted)

    @property
    def has_ssh(self) -> bool:
        return self.has_cli and self.cli_protocol == "SSH"

    @property
    def has_telnet(self) -> bool:
        return self.has_cli and self.cli_protocol == "TELNET"

    @property
    def has_cli(self) -> bool:
        return bool(self.ssh_username_encrypted and self.ssh_password_encrypted)


class DismissedAlarm(Base):
    """[KOS20260921] Alarms는 저장된 이벤트 테이블이 아니라 장비/링크/제어로그/
    Discovery 상태에서 매 요청마다 파생(derive)되는 값이라 "삭제"할 원본 행이
    없다(alarms.py 상단 설명 참고). 사용자가 목록에서 체크해 지우는 조작은 실제로는
    "이 알람(occurred_at 시점 기준)을 다시 보지 않기"에 해당하므로, 알람의 결정적
    id와 occurred_at 조합을 별도로 기록해 두고 list_alarms()에서 걸러낸다.
    같은 id라도 occurred_at이 달라지면(예: 다시 장애가 발생) 새 발생으로 간주해
    다시 표시된다.
    """

    __tablename__ = "dismissed_alarm"

    alarm_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    occurred_at: Mapped[dt.datetime] = mapped_column(DateTime(), primary_key=True)
    dismissed_at: Mapped[dt.datetime] = mapped_column(DateTime(), default=utcnow)


class Collector(Base):
    """3.2절 NMS Core / Local Collector / Remote Collector 아키텍처의 데이터 모델."""

    __tablename__ = "collector"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="LOCAL")  # LOCAL|REMOTE
    cidr_scope: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ONLINE")
    last_heartbeat_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(), default=utcnow)


class NetworkDevice(Base):
    """15.2절 network_device 핵심 필드."""

    __tablename__ = "network_device"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hostname: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    management_ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    primary_mac: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)

    vendor: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    serial_number: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    os_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    os_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    firmware_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # 7장: 장비 고유 식별자 체인 (우선순위 순)
    snmp_engine_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    lldp_chassis_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    sys_object_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # [KOS20260921] vendor/model이 채워지는 경로가 없어 장비 정보 화면에 항상
    # "-"로만 보였다("모델명이 있는데 표시가 안 된다" 리포트). sysDescr 원문은
    # SNMP sysDescr(1.3.6.1.2.1.1.1.0) 그대로 저장해, 텍스트 형식을 확신할 수
    # 없는 model을 억지로 파싱하는 대신 사용자가 직접 원문에서 모델을 확인할 수
    # 있게 한다. vendor는 sys_object_id의 enterprise 번호로 확인된 것만 채운다.
    sys_descr: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    device_type: Mapped[str] = mapped_column(String(32), nullable=False, default="UNKNOWN")
    device_role: Mapped[str] = mapped_column(String(32), nullable=False, default="UNKNOWN")
    role_source: Mapped[str] = mapped_column(String(16), nullable=False, default="AUTO")  # AUTO|MANUAL

    classification_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    classification_method: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    classification_detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON: evidence+score

    role_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    role_detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON: evidence+score

    layer2_capable: Mapped[bool] = mapped_column(Boolean, default=False)
    layer3_capable: Mapped[bool] = mapped_column(Boolean, default=False)
    poe_capable: Mapped[bool] = mapped_column(Boolean, default=False)
    # [KOS20260922] BRIDGE-MIB dot1dStpRootPort==0으로 판정한 STP Root Bridge
    # 여부. Topology 그래프에서 Root 노드를 시각적으로 표시하는 데 쓴다.
    is_stp_root: Mapped[bool] = mapped_column(Boolean, default=False)
    snmp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    ssh_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    capabilities: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON list

    physical_floor: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    floor_source: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    sys_location: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Q-BRIDGE-MIB dot1qVlanStaticTable 기준 장비에 구성된 VLAN 개수 (9장 VLAN 채점 근거).
    # Port PVID만으로는 Trunk가 나르는 VLAN을 과소산정하므로 장비 단위로 별도 저장한다.
    vlan_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    discovery_depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    preferred_collector_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("collector.id", ondelete="SET NULL"), nullable=True
    )

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="UNKNOWN")
    # ONLINE|STALE|OFFLINE|UNKNOWN

    # DEPRECATED(v3, 18.1절): SNMP community 평문 저장 컬럼. 새 코드는 더 이상 이
    # 컬럼에 값을 쓰지 않고 credential_profile_id를 사용한다. v2 이전 데이터와의
    # 하위호환을 위해 컬럼 자체는 유지한다(SQLite는 컬럼 삭제 비용이 크다).
    snmp_community: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    credential_profile_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("credential_profile.id", ondelete="SET NULL"), nullable=True
    )

    first_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(), default=utcnow)
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(), default=utcnow)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(), default=utcnow, onupdate=utcnow)

    interfaces: Mapped[list["DeviceInterface"]] = relationship(
        back_populates="device", cascade="all, delete-orphan"
    )


Index("ix_network_device_type_role", NetworkDevice.device_type, NetworkDevice.device_role)


class DeviceInterface(Base):
    __tablename__ = "device_interface"
    __table_args__ = (UniqueConstraint("device_id", "if_index", name="uq_device_ifindex"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("network_device.id", ondelete="CASCADE"), index=True)
    if_index: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    mac: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    admin_status: Mapped[str] = mapped_column(String(8), nullable=False, default="UNKNOWN")  # UP|DOWN
    oper_status: Mapped[str] = mapped_column(String(8), nullable=False, default="UNKNOWN")
    speed_mbps: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    vlan: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    is_trunk: Mapped[bool] = mapped_column(Boolean, default=False)
    # [KOS20260921] is_uplink는 원래 스키마부터 있었지만 실제로 계산해 채우는 코드가
    # 없어 항상 False였다(Uplink/Trunk 근거 부족이 Distribution/Floor 판정 정확도에
    # 영향을 준다고 ai-log에도 기록되어 있던 문제). topology/engine.py에서 이 인터페이스가
    # 속한 Link의 반대편 장비 discovery_depth와 비교해 자동 계산한다: 반대편이 더
    # 상위(depth가 작음)면 Uplink(상향), 더 하위(depth가 큼)면 Downlink(하향).
    # 같은 depth(Peer/이중화 연결)면 둘 다 False로 둔다.
    is_uplink: Mapped[bool] = mapped_column(Boolean, default=False)
    is_downlink: Mapped[bool] = mapped_column(Boolean, default=False)
    is_protected: Mapped[bool] = mapped_column(Boolean, default=False)
    protected_reason: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    poe_capable: Mapped[bool] = mapped_column(Boolean, default=False)
    # [KOS20260922] BRIDGE-MIB dot1dStpPortState: DISABLED|BLOCKING|LISTENING|
    # LEARNING|FORWARDING|BROKEN. STP 미지원 장비/비대상 포트는 NULL.
    stp_state: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    # [KOS20260922] BRIDGE-MIB dot1dStpPortDesignatedBridge에서 뽑아낸 MAC.
    # LLDP-MIB 미지원 장비가 많아(실사용 데이터로 확인) 이 필드로 LLDP 없이도
    # 실제 이웃 장비를 알아낸다(topology/engine.py의 infer_stp_designated_bridge_links
    # 참고). 자기 자신의 Bridge MAC이거나 Designated Bridge가 없으면 NULL.
    stp_designated_bridge_mac: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    # [KOS20260921] IF-MIB ifInOctets/ifOutOctets(누적 카운터, 32bit wrap 가능)를
    # 수집 시점 값 그대로 저장한다. 순간 전송률이 아니라 "마지막 수집 시각까지의
    # 누적 바이트"이므로 화면에는 last_seen_at과 함께 표시해야 의미가 있다.
    in_octets: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    out_octets: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(), default=utcnow)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(), default=utcnow, onupdate=utcnow)

    device: Mapped["NetworkDevice"] = relationship(back_populates="interfaces")


class LldpNeighbor(Base):
    __tablename__ = "lldp_neighbor"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    local_device_id: Mapped[int] = mapped_column(ForeignKey("network_device.id", ondelete="CASCADE"), index=True)
    local_interface_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("device_interface.id", ondelete="CASCADE"), nullable=True
    )
    remote_chassis_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    remote_port_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    remote_sys_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    remote_mgmt_ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    remote_capabilities: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    protocol: Mapped[str] = mapped_column(String(8), nullable=False, default="LLDP")  # LLDP|CDP
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(), default=utcnow)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(), default=utcnow)


class MacFdb(Base):
    __tablename__ = "mac_fdb"
    __table_args__ = (UniqueConstraint("device_id", "interface_id", "vlan", "mac", name="uq_fdb_entry"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("network_device.id", ondelete="CASCADE"), index=True)
    interface_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("device_interface.id", ondelete="CASCADE"), nullable=True
    )
    vlan: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    mac: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    entry_type: Mapped[str] = mapped_column(String(16), nullable=False, default="DYNAMIC")
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(), default=utcnow)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(), default=utcnow)


class ArpEntry(Base):
    __tablename__ = "arp_entry"
    __table_args__ = (UniqueConstraint("device_id", "ip", "mac", name="uq_arp_entry"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("network_device.id", ondelete="CASCADE"), index=True)
    interface_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("device_interface.id", ondelete="CASCADE"), nullable=True
    )
    ip: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    mac: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="REACHABLE")
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(), default=utcnow)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(), default=utcnow)


class RouteEntry(Base):
    __tablename__ = "route_entry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("network_device.id", ondelete="CASCADE"), index=True)
    destination: Mapped[str] = mapped_column(String(64), nullable=False)
    prefix_len: Mapped[int] = mapped_column(Integer, nullable=False, default=32)
    next_hop: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    interface_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("device_interface.id", ondelete="SET NULL"), nullable=True
    )
    metric: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    route_type: Mapped[str] = mapped_column(String(16), nullable=False, default="STATIC")
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(), default=utcnow)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(), default=utcnow)


class NetworkLink(Base):
    """12장: Topology 원본 Graph Edge."""

    __tablename__ = "network_link"
    __table_args__ = (
        UniqueConstraint(
            "src_device_id", "src_interface_id", "dst_device_id", "dst_interface_id", name="uq_link_endpoints"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    src_device_id: Mapped[int] = mapped_column(ForeignKey("network_device.id", ondelete="CASCADE"), index=True)
    src_interface_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("device_interface.id", ondelete="SET NULL"), nullable=True
    )
    dst_device_id: Mapped[int] = mapped_column(ForeignKey("network_device.id", ondelete="CASCADE"), index=True)
    dst_interface_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("device_interface.id", ondelete="SET NULL"), nullable=True
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False)  # LLDP|CDP|FDB_ARP|ARP|IP_SCAN|MANUAL 등
    label: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="UP")  # UP|STALE|DOWN

    first_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(), default=utcnow)
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(), default=utcnow)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(), default=utcnow, onupdate=utcnow)

    evidence: Mapped[list["NetworkLinkEvidence"]] = relationship(
        back_populates="link", cascade="all, delete-orphan"
    )


class NetworkLinkEvidence(Base):
    __tablename__ = "network_link_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    link_id: Mapped[int] = mapped_column(ForeignKey("network_link.id", ondelete="CASCADE"), index=True)
    evidence_type: Mapped[str] = mapped_column(String(32), nullable=False)
    raw_data: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON
    confidence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    observed_at: Mapped[dt.datetime] = mapped_column(DateTime(), default=utcnow)

    link: Mapped["NetworkLink"] = relationship(back_populates="evidence")


class PoePort(Base):
    __tablename__ = "poe_port"
    __table_args__ = (UniqueConstraint("device_id", "interface_id", name="uq_poe_port"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("network_device.id", ondelete="CASCADE"), index=True)
    interface_id: Mapped[int] = mapped_column(ForeignKey("device_interface.id", ondelete="CASCADE"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="UNKNOWN")  # DELIVERING|SEARCHING|FAULT
    power_mw: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    voltage_v: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    current_ma: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    poe_class: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(), default=utcnow, onupdate=utcnow)


class DiscoveryRun(Base):
    __tablename__ = "discovery_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile: Mapped[str] = mapped_column(String(16), nullable=False, default="STANDARD")
    cidr: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    collector_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("collector.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="RUNNING")
    # RUNNING|COMPLETED|FAILED|CANCELLED

    # 17.5절 Discovery Run 화면: 현재 스캔 중인 IP를 실시간으로 보여주기 위한 필드.
    # RUNNING 동안만 값이 있고, 종료되면 None으로 비운다.
    current_ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # [KOS20260921] concurrency>1이면 여러 IP가 동시에 처리 중인데 current_ip
    # 하나만으로는 "마지막으로 시작한 IP"밖에 못 보여준다. 지금 실제로 진행
    # 중인 IP 전체를 JSON 배열로 보여주기 위한 필드 (예: '["10.0.0.1","10.0.0.2"]').
    current_ips: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    started_at: Mapped[dt.datetime] = mapped_column(DateTime(), default=utcnow)
    ended_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(), nullable=True)

    scanned_count: Mapped[int] = mapped_column(Integer, default=0)
    alive_count: Mapped[int] = mapped_column(Integer, default=0)
    snmp_count: Mapped[int] = mapped_column(Integer, default=0)
    switch_count: Mapped[int] = mapped_column(Integer, default=0)
    camera_count: Mapped[int] = mapped_column(Integer, default=0)
    pc_count: Mapped[int] = mapped_column(Integer, default=0)
    unknown_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON list

    # [KOS20260921] 동시 스캔 개수(사용자가 Discovery 시작 시 지정) 및 hostname
    # 식별 보조 규칙(DNS/NETBIOS/MDNS, JSON list). 재현/감사 목적으로 Run별로
    # 기록해 둔다.
    concurrency: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    hostname_rules: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON list


class DeviceControlLog(Base):
    """13장/18.1절: 모든 SET/CLI 변경에 대한 Audit Log."""

    __tablename__ = "device_control_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("network_device.id", ondelete="CASCADE"), index=True)
    interface_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("device_interface.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    # PORT_ENABLE|PORT_DISABLE|POE_ENABLE|POE_DISABLE|VLAN_SET|DESCRIPTION_SET|ROLE_CHANGE
    # [KOS20260923] ROLE_CHANGE는 실제로는 여기(DeviceControlLog)가 아니라
    # ConfigChangeLog(source=MANUAL)에 남는다 - Role 변경 전용 제어 액션이 따로
    # 없어 이 값이 실제로 쓰인 적은 없다(기존 문서화 공백, 지금도 값만 예시로 남김).
    before_value: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    requested_value: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    after_value: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    result: Mapped[str] = mapped_column(String(16), nullable=False)  # SUCCESS|FAILED|DENIED
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    performed_by: Mapped[str] = mapped_column(String(64), nullable=False, default="system")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(), default=utcnow)


class ConfigChangeLog(Base):
    """구성 변경 이력 - NMS 화면에서 직접 실행한 포트/PoE 제어(DeviceControlLog가
    담당)가 아니라, 그 밖의 구성 필드(VLAN, STP Root, Role, 관리 IP, OS/Firmware
    버전 등)가 재탐색 중 "장비 쪽 값이 이전과 달라진 것을 발견"했거나(source=
    DISCOVERY) 운영자가 API로 직접 값을 바꿔서(source=MANUAL) 남는 이력이다.

    interface_id가 NULL이면 장비 단위 필드(예: management_ip, device_role)의
    변경이고, 값이 있으면 그 인터페이스 단위 필드(예: vlan)의 변경이다."""

    __tablename__ = "config_change_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("network_device.id", ondelete="CASCADE"), index=True)
    interface_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("device_interface.id", ondelete="SET NULL"), nullable=True
    )
    field_name: Mapped[str] = mapped_column(String(64), nullable=False)
    old_value: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    new_value: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # DISCOVERY|MANUAL
    performed_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # MANUAL만 값이 있음
    detected_at: Mapped[dt.datetime] = mapped_column(DateTime(), default=utcnow)
