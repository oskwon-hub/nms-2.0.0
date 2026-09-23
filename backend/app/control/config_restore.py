"""13.x절: 구성 변경 이력에서 "이전 값으로 복구"를 실행한다.

모든 필드가 자동 복구 가능한 것은 아니다 - 실제 장비에 안전하게 되돌릴 수 있는
SET 경로가 있는 필드(admin_status/vlan - port_control.py/interface_config_control.py
재사용)와, NMS 내부 판정값이라 DB만 바꾸면 되는 필드(device_role)만 지원한다.
관리 IP나 펌웨어 버전처럼 자동 복구가 위험하거나(관리 경로 자체가 끊길 수 있음)
애초에 SNMP SET 경로가 없는 필드는 명시적으로 거부한다 - 지원하지 않는 걸 아무
일도 안 하고 성공한 것처럼 보이게 하지 않는다.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.config_history import record_config_change
from app.control.interface_config_control import set_port_vlan
from app.control.port_control import ProtectedPortError, set_port_status
from app.models import ConfigChangeLog, NetworkDevice

RESTORABLE_FIELDS = {"admin_status", "vlan", "device_role"}


class ConfigChangeNotFoundError(Exception):
    pass


class UnsupportedRestoreFieldError(Exception):
    pass


async def restore_config_change(
    session: Session,
    change_id: int,
    performed_by: str,
    force: bool = False,
) -> dict:
    change = session.get(ConfigChangeLog, change_id)
    if change is None:
        raise ConfigChangeNotFoundError(f"구성 변경 이력을 찾을 수 없습니다: {change_id}")
    if change.field_name not in RESTORABLE_FIELDS:
        raise UnsupportedRestoreFieldError(f"'{change.field_name}' 필드는 자동 복구를 지원하지 않습니다.")

    if change.field_name == "admin_status":
        if change.interface_id is None:
            raise UnsupportedRestoreFieldError("복구할 인터페이스 정보가 없습니다.")
        enable = change.old_value == "UP"
        try:
            log = await set_port_status(session, change.device_id, change.interface_id, enable, performed_by, force=force)
            return {"result": log.result, "new_value": log.after_value, "error_message": log.error_message}
        except ProtectedPortError as exc:
            return {"result": "DENIED", "new_value": None, "error_message": str(exc)}

    if change.field_name == "vlan":
        if change.interface_id is None or change.old_value is None:
            raise UnsupportedRestoreFieldError("복구할 인터페이스 정보 또는 이전 VLAN 값이 없습니다.")
        try:
            log = await set_port_vlan(
                session, change.device_id, change.interface_id, int(change.old_value), performed_by, force=force
            )
            return {"result": log.result, "new_value": log.after_value, "error_message": log.error_message}
        except ProtectedPortError as exc:
            return {"result": "DENIED", "new_value": None, "error_message": str(exc)}

    # field_name == "device_role" (RESTORABLE_FIELDS에 이 셋만 있으므로 나머지는 여기)
    device = session.get(NetworkDevice, change.device_id)
    if device is None:
        raise ConfigChangeNotFoundError(f"장비를 찾을 수 없습니다: {change.device_id}")
    old_role = device.device_role
    device.device_role = change.old_value
    device.role_source = "MANUAL"
    record_config_change(
        session,
        device_id=device.id,
        field_name="device_role",
        old_value=old_role,
        new_value=change.old_value,
        source="MANUAL",
        performed_by=performed_by,
    )
    session.commit()
    return {"result": "SUCCESS", "new_value": change.old_value, "error_message": None}
