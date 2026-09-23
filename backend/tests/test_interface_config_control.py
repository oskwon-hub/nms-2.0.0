import pytest

from app.collectors.base import CollectorError
from app.control import interface_config_control
from app.control.port_control import ProtectedPortError
from app.identity import DeviceObservation, resolve_or_create_device
from app.models import DeviceInterface


class _FakeDriver:
    """GenericSNMPDriver를 대체하는 테스트용 Fake - VLAN/설명 SET만 다룬다."""

    def __init__(self, host, community=None, **kwargs):
        self.host = host
        self.pvids: dict[int, int] = {1: 10}
        self.aliases: dict[int, str] = {1: "old-desc"}

    async def get_port_pvids(self):
        return dict(self.pvids)

    async def set_vlan(self, if_index, vlan):
        self.pvids[if_index] = vlan

    async def get_if_alias(self, if_index):
        return self.aliases.get(if_index)

    async def set_description(self, if_index, description):
        self.aliases[if_index] = description


class _FailingVlanDriver(_FakeDriver):
    async def set_vlan(self, if_index, vlan):
        raise CollectorError("SNMP SET 실패(테스트)")


def _make_device_with_interface(session, is_protected=False, protected_reason=None, vlan=10):
    device, _ = resolve_or_create_device(session, DeviceObservation(management_ip="10.0.0.10"))
    session.flush()
    interface = DeviceInterface(
        device_id=device.id, if_index=1, name="Gi0/1", admin_status="UP", vlan=vlan,
        is_protected=is_protected, protected_reason=protected_reason,
    )
    session.add(interface)
    session.commit()
    return device, interface


@pytest.mark.asyncio
async def test_set_port_vlan_updates_interface_and_logs_success(db_session, monkeypatch):
    device, interface = _make_device_with_interface(db_session)
    monkeypatch.setattr("app.control.interface_config_control.GenericSNMPDriver", _FakeDriver)

    log = await interface_config_control.set_port_vlan(db_session, device.id, interface.id, 20, "tester")

    assert log.result == "SUCCESS"
    assert log.before_value == "10"
    assert log.after_value == "20"
    db_session.refresh(interface)
    assert interface.vlan == 20


@pytest.mark.asyncio
async def test_set_port_vlan_denied_on_protected_port_without_force(db_session, monkeypatch):
    device, interface = _make_device_with_interface(db_session, is_protected=True, protected_reason="TRUNK")
    monkeypatch.setattr("app.control.interface_config_control.GenericSNMPDriver", _FakeDriver)

    with pytest.raises(ProtectedPortError):
        await interface_config_control.set_port_vlan(db_session, device.id, interface.id, 20, "tester")


@pytest.mark.asyncio
async def test_set_port_vlan_records_failure_on_collector_error(db_session, monkeypatch):
    device, interface = _make_device_with_interface(db_session)
    monkeypatch.setattr("app.control.interface_config_control.GenericSNMPDriver", _FailingVlanDriver)

    log = await interface_config_control.set_port_vlan(db_session, device.id, interface.id, 20, "tester")

    assert log.result == "FAILED"
    db_session.refresh(interface)
    assert interface.vlan == 10  # 실패했으므로 원래 값 유지


@pytest.mark.asyncio
async def test_set_interface_description_success(db_session, monkeypatch):
    device, interface = _make_device_with_interface(db_session)
    monkeypatch.setattr("app.control.interface_config_control.GenericSNMPDriver", _FakeDriver)

    log = await interface_config_control.set_interface_description(db_session, device.id, interface.id, "new-desc", "tester")

    assert log.result == "SUCCESS"
    assert log.before_value == "old-desc"
    assert log.after_value == "new-desc"


@pytest.mark.asyncio
async def test_set_interface_description_denied_on_protected_port_without_force(db_session, monkeypatch):
    device, interface = _make_device_with_interface(db_session, is_protected=True, protected_reason="TRUNK")
    monkeypatch.setattr("app.control.interface_config_control.GenericSNMPDriver", _FakeDriver)

    with pytest.raises(ProtectedPortError):
        await interface_config_control.set_interface_description(db_session, device.id, interface.id, "new-desc", "tester")
