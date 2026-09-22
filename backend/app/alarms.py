"""18.1/12.1절: Notification Trigger를 Event로 정규화한 Alarms 화면의 데이터 소스.

전용 event/alarm 테이블과 백그라운드 폴링 엔진을 새로 두는 대신(Phase 9~10 범위),
이미 저장되어 있는 현재 상태(장비 status, network_link status, 제어 Audit Log,
Discovery Run 결과)에서 파생(derive)하는 방식으로 구현한다. DB에 새 이력을 쌓지
않으므로 "현재 시점의 활성 알람 목록"이며, 과거 알람의 별도 이력 조회는 지원하지
않는다 - 이 한계는 README/ai-log에 명시한다.

12.1절 Dependency 기반 억제: 하위 장비(Endpoint 방향)가 죽은 원인이 상위(코어
방향) 경로 장애일 가능성이 높으면 Root Cause가 아닌 Suppressed로 표시한다.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DeviceControlLog, DiscoveryRun, NetworkDevice, NetworkLink

DOWN_DEVICE_STATUSES = ("STALE", "OFFLINE")
DOWN_LINK_STATUSES = ("STALE", "DOWN")


@dataclass
class Alarm:
    id: str
    severity: str  # CRITICAL | WARNING | INFO
    category: str  # DEVICE | LINK | CONTROL | DISCOVERY
    message: str
    occurred_at: dt.datetime
    device_id: Optional[int] = None
    link_id: Optional[int] = None
    discovery_run_id: Optional[int] = None
    suppressed: bool = False
    suppressed_reason: Optional[str] = None


def _device_label(device: NetworkDevice) -> str:
    return device.hostname or device.management_ip or f"#{device.id}"


def _find_down_upstream(session: Session, device: NetworkDevice, devices_by_id: dict[int, NetworkDevice]) -> Optional[NetworkDevice]:
    """12.1절: 해당 장비보다 Core 방향(discovery_depth가 작은)으로 연결된 이웃 중
    이미 장애 상태인 장비가 있으면 그 장비를 반환한다 (Suppression 판단용)."""
    links = session.scalars(
        select(NetworkLink).where(
            (NetworkLink.src_device_id == device.id) | (NetworkLink.dst_device_id == device.id)
        )
    )
    for link in links:
        other_id = link.dst_device_id if link.src_device_id == device.id else link.src_device_id
        other = devices_by_id.get(other_id)
        if (
            other is not None
            and other.id != device.id
            and other.discovery_depth < device.discovery_depth
            and other.status in DOWN_DEVICE_STATUSES
        ):
            return other
    return None


def list_alarms(session: Session, control_log_limit: int = 50) -> list[Alarm]:
    alarms: list[Alarm] = []
    devices = session.scalars(select(NetworkDevice)).all()
    devices_by_id = {d.id: d for d in devices}

    for device in devices:
        if device.status not in DOWN_DEVICE_STATUSES:
            continue
        severity = "CRITICAL" if device.status == "OFFLINE" else "WARNING"
        upstream = _find_down_upstream(session, device, devices_by_id)
        alarm = Alarm(
            id=f"device:{device.id}:{device.status}",
            severity=severity,
            category="DEVICE",
            message=f"{_device_label(device)} 응답 없음 ({device.status})",
            occurred_at=device.last_seen_at,
            device_id=device.id,
        )
        if upstream is not None:
            alarm.suppressed = True
            alarm.suppressed_reason = f"상위 장비 {_device_label(upstream)} 장애로 억제됨 (Root Cause 아님)"
        alarms.append(alarm)

    for link in session.scalars(select(NetworkLink)):
        if link.status not in DOWN_LINK_STATUSES:
            continue
        src = devices_by_id.get(link.src_device_id)
        dst = devices_by_id.get(link.dst_device_id)
        src_label = _device_label(src) if src else f"#{link.src_device_id}"
        dst_label = _device_label(dst) if dst else f"#{link.dst_device_id}"
        alarms.append(
            Alarm(
                id=f"link:{link.id}:{link.status}",
                severity="CRITICAL" if link.status == "DOWN" else "WARNING",
                category="LINK",
                message=f"Link {src_label} <-> {dst_label} {link.status}",
                occurred_at=link.last_seen_at,
                link_id=link.id,
            )
        )

    control_stmt = (
        select(DeviceControlLog)
        .where(DeviceControlLog.result.in_(("FAILED", "DENIED")))
        .order_by(DeviceControlLog.created_at.desc())
        .limit(control_log_limit)
    )
    for log in session.scalars(control_stmt):
        device = devices_by_id.get(log.device_id)
        label = _device_label(device) if device else f"#{log.device_id}"
        alarms.append(
            Alarm(
                id=f"control:{log.id}",
                severity="CRITICAL" if log.result == "FAILED" else "INFO",
                category="CONTROL",
                message=f"{label} {log.action} {log.result}" + (f" - {log.error_message}" if log.error_message else ""),
                occurred_at=log.created_at,
                device_id=log.device_id,
            )
        )

    for run in session.scalars(select(DiscoveryRun).where(DiscoveryRun.status == "FAILED")):
        alarms.append(
            Alarm(
                id=f"discovery:{run.id}",
                severity="WARNING",
                category="DISCOVERY",
                message=f"Discovery Run #{run.id} 실패 (profile={run.profile})",
                occurred_at=run.ended_at or run.started_at,
                discovery_run_id=run.id,
            )
        )

    alarms.sort(key=lambda a: a.occurred_at, reverse=True)
    return alarms
