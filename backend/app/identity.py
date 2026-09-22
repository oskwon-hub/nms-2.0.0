"""7장: 장비 고유 식별 및 중복 병합.

IP 주소는 DHCP/주소 변경으로 인해 안정적 식별키가 아니므로, 설계서 표에 정의된
우선순위(1.SNMP Engine ID > 2.Serial Number > 3.LLDP Chassis ID > 4.Base/Chassis MAC
> 5.sysObjectID+MAC+Vendor > 6.Management IP) 순으로 동일 장비를 판별하고 병합한다.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import NetworkDevice, utcnow


@dataclass
class DeviceObservation:
    """Collector들이 만들어내는 장비 후보 원시 관측치."""

    management_ip: Optional[str] = None
    primary_mac: Optional[str] = None
    snmp_engine_id: Optional[str] = None
    serial_number: Optional[str] = None
    lldp_chassis_id: Optional[str] = None
    sys_object_id: Optional[str] = None
    sys_descr: Optional[str] = None
    hostname: Optional[str] = None
    vendor: Optional[str] = None
    model: Optional[str] = None
    os_name: Optional[str] = None
    os_version: Optional[str] = None
    firmware_version: Optional[str] = None
    sys_location: Optional[str] = None
    snmp_enabled: bool = False
    ssh_enabled: bool = False
    # DEPRECATED(v3): 새 코드는 credential_profile_id를 사용한다 (18.1절, 평문 저장 금지).
    snmp_community: Optional[str] = None
    credential_profile_id: Optional[int] = None
    discovery_depth: int = 0
    extra: dict = field(default_factory=dict)


def stable_identity(observation: DeviceObservation) -> tuple[str, str]:
    """설계서 7장 우선순위에 따라 (식별자 종류, 값) 형태의 안정 식별 키를 만든다.

    BFS Discovery의 visited set에서 in-memory 중복 판정에 사용된다. DB 병합
    시점에는 resolve_or_create_device가 동일한 우선순위로 다시 한 번 조회하므로,
    아직 식별자가 확정되지 않은 장비가 큐에 중복 유입되더라도 DB 단계에서
    최종적으로 하나의 레코드로 수렴한다.
    """
    if observation.snmp_engine_id:
        return ("snmp_engine_id", observation.snmp_engine_id)
    if observation.serial_number:
        return ("serial_number", observation.serial_number)
    if observation.lldp_chassis_id:
        return ("lldp_chassis_id", observation.lldp_chassis_id)
    if observation.primary_mac:
        return ("primary_mac", observation.primary_mac.lower())
    if observation.sys_object_id and observation.primary_mac and observation.vendor:
        return ("composite", f"{observation.sys_object_id}|{observation.primary_mac}|{observation.vendor}")
    if observation.management_ip:
        return ("management_ip", observation.management_ip)
    raise ValueError("장비를 식별할 최소한의 정보(관리 IP 등)가 없습니다.")


def _find_existing(session: Session, observation: DeviceObservation) -> Optional[NetworkDevice]:
    if observation.snmp_engine_id:
        row = session.scalar(select(NetworkDevice).where(NetworkDevice.snmp_engine_id == observation.snmp_engine_id))
        if row:
            return row
    if observation.serial_number:
        row = session.scalar(select(NetworkDevice).where(NetworkDevice.serial_number == observation.serial_number))
        if row:
            return row
    if observation.lldp_chassis_id:
        row = session.scalar(
            select(NetworkDevice).where(NetworkDevice.lldp_chassis_id == observation.lldp_chassis_id)
        )
        if row:
            return row
    if observation.primary_mac:
        row = session.scalar(select(NetworkDevice).where(NetworkDevice.primary_mac == observation.primary_mac))
        if row:
            return row
    if observation.sys_object_id and observation.primary_mac and observation.vendor:
        row = session.scalar(
            select(NetworkDevice).where(
                NetworkDevice.sys_object_id == observation.sys_object_id,
                NetworkDevice.vendor == observation.vendor,
            )
        )
        if row:
            return row
    if observation.management_ip:
        row = session.scalar(
            select(NetworkDevice).where(NetworkDevice.management_ip == observation.management_ip)
        )
        if row:
            return row
    return None


def resolve_or_create_device(session: Session, observation: DeviceObservation) -> tuple[NetworkDevice, bool]:
    """동일 장비를 찾아 병합하거나 신규 생성한다. (device, created) 반환."""

    existing = _find_existing(session, observation)
    now = utcnow()

    if existing is None:
        device = NetworkDevice(
            hostname=observation.hostname,
            management_ip=observation.management_ip,
            primary_mac=observation.primary_mac,
            vendor=observation.vendor,
            model=observation.model,
            serial_number=observation.serial_number,
            os_name=observation.os_name,
            os_version=observation.os_version,
            firmware_version=observation.firmware_version,
            snmp_engine_id=observation.snmp_engine_id,
            lldp_chassis_id=observation.lldp_chassis_id,
            sys_object_id=observation.sys_object_id,
            sys_descr=observation.sys_descr,
            sys_location=observation.sys_location,
            snmp_enabled=observation.snmp_enabled,
            ssh_enabled=observation.ssh_enabled,
            snmp_community=observation.snmp_community,
            credential_profile_id=observation.credential_profile_id,
            discovery_depth=observation.discovery_depth,
            first_seen_at=now,
            last_seen_at=now,
            status="ONLINE",
        )
        session.add(device)
        session.flush()
        return device, True

    # 7장 bullet: "IP만 다르고 Chassis MAC/Serial이 같으면 IP 변경으로 처리"
    strong_match = bool(
        observation.snmp_engine_id
        or observation.serial_number
        or observation.lldp_chassis_id
        or observation.primary_mac
    )
    if strong_match and observation.management_ip and existing.management_ip != observation.management_ip:
        existing.management_ip = observation.management_ip

    for field_name in (
        "hostname",
        "primary_mac",
        "vendor",
        "model",
        "serial_number",
        "os_name",
        "os_version",
        "firmware_version",
        "snmp_engine_id",
        "lldp_chassis_id",
        "sys_object_id",
        "sys_descr",
        "sys_location",
    ):
        value = getattr(observation, field_name)
        if value:
            setattr(existing, field_name, value)

    existing.snmp_enabled = existing.snmp_enabled or observation.snmp_enabled
    existing.ssh_enabled = existing.ssh_enabled or observation.ssh_enabled
    if observation.snmp_community:
        existing.snmp_community = observation.snmp_community
    if observation.credential_profile_id:
        existing.credential_profile_id = observation.credential_profile_id
    existing.discovery_depth = min(existing.discovery_depth, observation.discovery_depth) if existing.discovery_depth else observation.discovery_depth
    existing.last_seen_at = now
    existing.status = "ONLINE"
    session.flush()
    return existing, False


def mark_stale_and_offline_devices(session: Session, stale_after_seconds: int, offline_after_seconds: int) -> int:
    """15장 status 전이: 오래 보이지 않는 장비를 즉시 삭제하지 않고 상태만 전이한다."""
    now = utcnow()
    changed = 0
    devices = session.scalars(select(NetworkDevice).where(NetworkDevice.status != "OFFLINE"))
    for device in devices:
        age = (now - device.last_seen_at).total_seconds()
        new_status = device.status
        if age >= offline_after_seconds:
            new_status = "OFFLINE"
        elif age >= stale_after_seconds:
            new_status = "STALE"
        else:
            new_status = "ONLINE"
        if new_status != device.status:
            device.status = new_status
            changed += 1
    return changed
