"""13.2절: PoE Enable/Disable.

Port Disable과 PoE Disable은 반드시 다른 API/버튼으로 제공한다(bullet 1). PoE
제어는 POWER-ETHERNET-MIB pethPsePortAdminEnable을 사용하며, PoE 단말의 서비스
중단을 의미하므로 Port 제어와 동일하게 보호 포트 검사를 거친다.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.collectors.base import CollectorError
from app.collectors.drivers import GenericSNMPDriver
from app.control.port_control import PortNotFoundError, ProtectedPortError
from app.credentials import resolve_snmp_community
from app.models import DeviceControlLog, DeviceInterface, NetworkDevice, PoePort


async def set_poe_status(
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

    poe_port = session.query(PoePort).filter_by(device_id=device_id, interface_id=interface_id).one_or_none()
    action = "POE_ENABLE" if enable else "POE_DISABLE"
    before_value = poe_port.status if poe_port else "UNKNOWN"
    requested_value = "ENABLED" if enable else "DISABLED"

    if not enable and interface.is_protected and not force:
        log = DeviceControlLog(
            device_id=device_id,
            interface_id=interface_id,
            action=action,
            before_value=before_value,
            requested_value=requested_value,
            after_value=before_value,
            result="DENIED",
            error_message=f"보호 포트({interface.protected_reason})는 강제 옵션 없이 PoE Disable할 수 없습니다.",
            performed_by=performed_by,
        )
        session.add(log)
        session.commit()
        raise ProtectedPortError(log.error_message)

    driver = GenericSNMPDriver(device.management_ip, community=resolve_snmp_community(session, device))

    # PoE 포트 인덱스는 수집 시 ifIndex로 근사 매핑했으므로(snmp_collector 주석 참고)
    # 제어 시에도 동일한 근사값(if_index)을 pethPsePortIndex로 사용한다.
    try:
        await driver.enable_poe(interface.if_index) if enable else await driver.disable_poe(interface.if_index)
        fresh_poe = await driver.get_poe_status()
        after_info = next((p for p in fresh_poe if p.if_index == interface.if_index), None)
        after_value = "ENABLED" if (after_info.enabled if after_info else enable) else "DISABLED"
        result = "SUCCESS" if after_value == requested_value else "FAILED"
        error_message = None if result == "SUCCESS" else "SET 이후 재조회 결과가 요청값과 다릅니다."

        if poe_port is None:
            poe_port = PoePort(device_id=device_id, interface_id=interface_id, enabled=enable, status=before_value)
            session.add(poe_port)
        poe_port.enabled = enable
        if after_info:
            poe_port.status = after_info.status
    except CollectorError as exc:
        after_value = before_value
        result = "FAILED"
        error_message = str(exc)

    log = DeviceControlLog(
        device_id=device_id,
        interface_id=interface_id,
        action=action,
        before_value=before_value,
        requested_value=requested_value,
        after_value=after_value,
        result=result,
        error_message=error_message,
        performed_by=performed_by,
    )
    session.add(log)
    session.commit()
    return log
