import pytest

from app.collectors.base import CollectorError, InterfaceInfo, PoeInfo
from app.control import poe_control, port_control
from app.control.port_control import ProtectedPortError
from app.control.protected import refresh_protected_flags
from app.identity import DeviceObservation, resolve_or_create_device
from app.models import DeviceControlLog, DeviceInterface, PoePort


class _FakeDriver:
    """GenericSNMPDriver를 대체하는 테스트용 Fake. 실제 네트워크 호출을 하지 않는다."""

    def __init__(self, host, community=None, **kwargs):
        self.host = host
        self.enabled_ports: set[int] = set()
        self.enabled_poe: set[int] = set()

    async def enable_port(self, if_index):
        self.enabled_ports.add(if_index)

    async def disable_port(self, if_index):
        self.enabled_ports.discard(if_index)

    async def get_interfaces(self):
        return [InterfaceInfo(if_index=1, name="Gi0/1", admin_status="UP" if 1 in self.enabled_ports else "DOWN")]

    async def enable_poe(self, peth_port_index):
        self.enabled_poe.add(peth_port_index)

    async def disable_poe(self, peth_port_index):
        self.enabled_poe.discard(peth_port_index)

    async def get_poe_status(self):
        return [PoeInfo(if_index=1, enabled=1 in self.enabled_poe, status="DELIVERING" if 1 in self.enabled_poe else "SEARCHING")]


class _FailingDriver(_FakeDriver):
    async def disable_port(self, if_index):
        raise CollectorError("SNMP SET 실패(테스트)")


def _make_device_with_interface(session, is_protected=False, protected_reason=None):
    device, _ = resolve_or_create_device(session, DeviceObservation(management_ip="10.0.0.10"))
    session.flush()
    interface = DeviceInterface(
        device_id=device.id, if_index=1, name="Gi0/1", admin_status="UP", is_protected=is_protected, protected_reason=protected_reason
    )
    session.add(interface)
    session.commit()
    return device, interface


@pytest.mark.asyncio
async def test_disable_protected_port_denied_without_force(db_session, monkeypatch):
    device, interface = _make_device_with_interface(db_session, is_protected=True, protected_reason="TRUNK")
    monkeypatch.setattr("app.control.port_control.GenericSNMPDriver", _FakeDriver)

    with pytest.raises(ProtectedPortError):
        await port_control.set_port_status(db_session, device.id, interface.id, enable=False, performed_by="tester")

    log = db_session.query(DeviceControlLog).one()
    assert log.result == "DENIED"


@pytest.mark.asyncio
async def test_disable_protected_port_allowed_with_force(db_session, monkeypatch):
    device, interface = _make_device_with_interface(db_session, is_protected=True, protected_reason="TRUNK")
    monkeypatch.setattr("app.control.port_control.GenericSNMPDriver", _FakeDriver)

    log = await port_control.set_port_status(db_session, device.id, interface.id, enable=False, performed_by="tester", force=True)
    assert log.result == "SUCCESS"
    assert interface.admin_status == "DOWN"


@pytest.mark.asyncio
async def test_port_control_records_failure_when_driver_raises(db_session, monkeypatch):
    device, interface = _make_device_with_interface(db_session)
    monkeypatch.setattr("app.control.port_control.GenericSNMPDriver", _FailingDriver)

    # set_port_status는 Collector 실패를 예외로 다시 던지지 않고 FAILED 로그로 남긴다.
    log = await port_control.set_port_status(db_session, device.id, interface.id, enable=False, performed_by="tester")
    assert log.result == "FAILED"
    assert log.error_message is not None


@pytest.mark.asyncio
async def test_poe_disable_independent_of_port_status(db_session, monkeypatch):
    """13.2절 bullet: Port Disable과 PoE Disable은 반드시 다른 API로 제공한다."""
    device, interface = _make_device_with_interface(db_session)
    db_session.add(PoePort(device_id=device.id, interface_id=interface.id, enabled=True, status="DELIVERING"))
    db_session.commit()

    fake = _FakeDriver(device.management_ip)
    fake.enabled_poe.add(1)
    monkeypatch.setattr("app.control.poe_control.GenericSNMPDriver", lambda *a, **k: fake)

    log = await poe_control.set_poe_status(db_session, device.id, interface.id, enable=False, performed_by="tester")
    assert log.result == "SUCCESS"
    assert interface.admin_status == "UP"  # Port 상태는 변하지 않았어야 한다


def test_refresh_protected_flags_marks_core_uplink_and_trunk(db_session):
    device, interface = _make_device_with_interface(db_session)
    device.device_role = "CORE_SWITCH"
    interface.is_uplink = True
    db_session.commit()

    changed = refresh_protected_flags(db_session)
    assert changed == 1
    assert interface.is_protected is True
    assert interface.protected_reason == "CORE_DISTRIBUTION_UPLINK"


def test_refresh_protected_flags_preserves_manual_override(db_session):
    device, interface = _make_device_with_interface(db_session, is_protected=True, protected_reason="MANUAL")
    db_session.commit()

    refresh_protected_flags(db_session)
    assert interface.is_protected is True
    assert interface.protected_reason == "MANUAL"
