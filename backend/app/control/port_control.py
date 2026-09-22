"""13.1절: Port Enable/Disable.

제어 전 현재 상태를 GET하고, SET 수행 후 재조회하여 실제 반영 여부를 검증한다.
제어 사용자, 이전값, 요청값, 결과값, 오류를 Audit Log(device_control_log)에
남긴다. 보호 포트는 force=True와 명시적 승인 없이는 Disable을 거부한다.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.collectors.base import CollectorError
from app.collectors.drivers import GenericSNMPDriver
from app.credentials import resolve_snmp_community
from app.models import DeviceControlLog, DeviceInterface, NetworkDevice


class ProtectedPortError(Exception):
    pass


class PortNotFoundError(Exception):
    pass


async def set_port_status(
    session: Session,
    device_id: int,
    interface_id: int,
    enable: bool,
    performed_by: str,
    force: bool = False,
) -> DeviceControlLog:
    device = session.get(NetworkDevice, device_id)
    interface = session.get(DeviceInterface, interface_id)
    if device is None or interface is None or interface.device_id != device_id:
        raise PortNotFoundError(f"장비/포트를 찾을 수 없습니다: device={device_id} interface={interface_id}")

    action = "PORT_ENABLE" if enable else "PORT_DISABLE"
    before_value = interface.admin_status
    requested_value = "UP" if enable else "DOWN"

    if not enable and interface.is_protected and not force:
        log = DeviceControlLog(
            device_id=device_id,
            interface_id=interface_id,
            action=action,
            before_value=before_value,
            requested_value=requested_value,
            after_value=before_value,
            result="DENIED",
            error_message=f"보호 포트({interface.protected_reason})는 강제 옵션 없이 Disable할 수 없습니다.",
            performed_by=performed_by,
        )
        session.add(log)
        session.commit()
        raise ProtectedPortError(log.error_message)

    driver = GenericSNMPDriver(device.management_ip, community=resolve_snmp_community(session, device))

    try:
        await driver.enable_port(interface.if_index) if enable else await driver.disable_port(interface.if_index)
        fresh_interfaces = await driver.get_interfaces()
        after = next((i.admin_status for i in fresh_interfaces if i.if_index == interface.if_index), requested_value)
        interface.admin_status = after
        result = "SUCCESS" if after == requested_value else "FAILED"
        error_message = None if result == "SUCCESS" else "SET 이후 재조회 결과가 요청값과 다릅니다."
    except CollectorError as exc:
        after = before_value
        result = "FAILED"
        error_message = str(exc)

    log = DeviceControlLog(
        device_id=device_id,
        interface_id=interface_id,
        action=action,
        before_value=before_value,
        requested_value=requested_value,
        after_value=after,
        result=result,
        error_message=error_message,
        performed_by=performed_by,
    )
    session.add(log)
    session.commit()
    return log
