"""13.3절: 보호 포트.

자동 판정 가능한 두 가지 근거(Core/Distribution Uplink, Trunk Port)만 자동
갱신한다. 'NMS 관리 경로'와 'Stack/MLAG/Peer Link'는 표준 MIB만으로는 신뢰성
있게 자동 판별하기 어려워, 운영자가 수동 지정(protected_reason=MANUAL)하도록
남겨둔다 - 이 한계는 gap 분석에도 명시했다.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DeviceInterface, NetworkDevice


def refresh_protected_flags(session: Session) -> int:
    updated = 0
    for interface in session.scalars(select(DeviceInterface)):
        if interface.protected_reason == "MANUAL":
            continue  # 운영자 수동 지정은 자동 로직이 덮어쓰지 않는다 (2.1절 원칙과 동일한 취지).

        device = session.get(NetworkDevice, interface.device_id)
        if device is None:
            continue

        auto_protected = False
        reason = None
        if device.device_role in ("CORE_SWITCH", "DISTRIBUTION_SWITCH") and interface.is_uplink:
            auto_protected = True
            reason = "CORE_DISTRIBUTION_UPLINK"
        elif interface.is_trunk:
            auto_protected = True
            reason = "TRUNK"

        if interface.is_protected != auto_protected or interface.protected_reason != reason:
            interface.is_protected = auto_protected
            interface.protected_reason = reason
            updated += 1
    return updated


def set_manual_protection(interface: DeviceInterface, protected: bool) -> None:
    """운영자가 is_protected로 직접 지정한다 (13.3절 마지막 bullet)."""
    interface.is_protected = protected
    interface.protected_reason = "MANUAL" if protected else None
