"""13.x절: VLAN(PVID)/포트 설명(Description) 변경.

port_control.py/poe_control.py와 동일한 패턴이다 - 제어 전 현재 상태를 확인하고,
SET 수행 후 재조회해 실제 반영 여부를 검증하며, Audit Log(device_control_log)에
남긴다. 보호 포트는 force=True 없이는 변경을 거부한다.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.collectors.base import CollectorError
from app.collectors.drivers import GenericSNMPDriver
from app.control.port_control import PortNotFoundError, ProtectedPortError
from app.credentials import resolve_snmp_community
from app.models import DeviceControlLog, DeviceInterface, NetworkDevice


def _get_device_and_interface(session: Session, device_id: int, interface_id: int) -> tuple[NetworkDevice, DeviceInterface]:
    device = session.get(NetworkDevice, device_id)
    interface = session.get(DeviceInterface, interface_id)
    if device is None or interface is None or interface.device_id != device_id:
        raise PortNotFoundError(f"장비/포트를 찾을 수 없습니다: device={device_id} interface={interface_id}")
    return device, interface


async def set_port_vlan(
    session: Session,
    device_id: int,
    interface_id: int,
    vlan: int,
    performed_by: str,
    force: bool = False,
) -> DeviceControlLog:
    device, interface = _get_device_and_interface(session, device_id, interface_id)

    action = "VLAN_SET"
    before_value = str(interface.vlan) if interface.vlan is not None else None
    requested_value = str(vlan)

    if interface.is_protected and not force:
        log = DeviceControlLog(
            device_id=device_id,
            interface_id=interface_id,
            action=action,
            before_value=before_value,
            requested_value=requested_value,
            after_value=before_value,
            result="DENIED",
            error_message=f"보호 포트({interface.protected_reason})는 강제 옵션 없이 VLAN을 변경할 수 없습니다.",
            performed_by=performed_by,
        )
        session.add(log)
        session.commit()
        raise ProtectedPortError(log.error_message)

    driver = GenericSNMPDriver(device.management_ip, community=resolve_snmp_community(session, device))

    try:
        await driver.set_vlan(interface.if_index, vlan)
        fresh_pvids = await driver.get_port_pvids()
        after_vlan = fresh_pvids.get(interface.if_index)
        after = str(after_vlan) if after_vlan is not None else None
        interface.vlan = after_vlan
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


async def set_interface_description(
    session: Session,
    device_id: int,
    interface_id: int,
    description: str,
    performed_by: str,
    force: bool = False,
) -> DeviceControlLog:
    """[KOS20260923] ifAlias는 vlan과 달리 DeviceInterface에 상시 수집/저장하는
    컬럼이 없다(discovery 수집 범위 밖) - "현재 값"은 SET 직전에 장비에서 직접
    조회해 before_value로 쓴다."""
    device, interface = _get_device_and_interface(session, device_id, interface_id)

    action = "DESCRIPTION_SET"
    driver = GenericSNMPDriver(device.management_ip, community=resolve_snmp_community(session, device))

    try:
        before_value = await driver.get_if_alias(interface.if_index)
    except CollectorError:
        before_value = None

    if interface.is_protected and not force:
        log = DeviceControlLog(
            device_id=device_id,
            interface_id=interface_id,
            action=action,
            before_value=before_value,
            requested_value=description,
            after_value=before_value,
            result="DENIED",
            error_message=f"보호 포트({interface.protected_reason})는 강제 옵션 없이 설명을 변경할 수 없습니다.",
            performed_by=performed_by,
        )
        session.add(log)
        session.commit()
        raise ProtectedPortError(log.error_message)

    try:
        await driver.set_description(interface.if_index, description)
        after = await driver.get_if_alias(interface.if_index)
        result = "SUCCESS" if after == description else "FAILED"
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
        requested_value=description,
        after_value=after,
        result=result,
        error_message=error_message,
        performed_by=performed_by,
    )
    session.add(log)
    session.commit()
    return log
