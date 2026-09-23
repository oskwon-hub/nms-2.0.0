import pytest

from app.collectors.base import InterfaceInfo
from app.control.config_restore import (
    ConfigChangeNotFoundError,
    UnsupportedRestoreFieldError,
    restore_config_change,
)
from app.identity import DeviceObservation, resolve_or_create_device
from app.models import ConfigChangeLog, DeviceInterface


class _FakeDriver:
    def __init__(self, host, community=None, **kwargs):
        self.host = host
        self.enabled_ports: set[int] = {1}  # 기본 상태: UP
        self.pvids: dict[int, int] = {1: 99}  # 기본 상태: VLAN 99 (복구 전 "새 값")

    async def enable_port(self, if_index):
        self.enabled_ports.add(if_index)

    async def disable_port(self, if_index):
        self.enabled_ports.discard(if_index)

    async def get_interfaces(self):
        return [InterfaceInfo(if_index=1, name="Gi0/1", admin_status="UP" if 1 in self.enabled_ports else "DOWN")]

    async def get_port_pvids(self):
        return dict(self.pvids)

    async def set_vlan(self, if_index, vlan):
        self.pvids[if_index] = vlan


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
async def test_restore_admin_status_disables_port(db_session, monkeypatch):
    """이력의 old_value가 DOWN이면 "복구"는 그 포트를 다시 DOWN으로 되돌린다."""
    device, interface = _make_device_with_interface(db_session)
    monkeypatch.setattr("app.control.port_control.GenericSNMPDriver", _FakeDriver)
    change = ConfigChangeLog(
        device_id=device.id,
        interface_id=interface.id,
        field_name="admin_status",
        old_value="DOWN",
        new_value="UP",
        source="DISCOVERY",
    )
    db_session.add(change)
    db_session.commit()

    result = await restore_config_change(db_session, change.id, performed_by="alice")

    assert result["result"] == "SUCCESS"
    assert result["new_value"] == "DOWN"
    db_session.refresh(interface)
    assert interface.admin_status == "DOWN"


@pytest.mark.asyncio
async def test_restore_admin_status_denied_on_protected_port_without_force(db_session, monkeypatch):
    device, interface = _make_device_with_interface(db_session, is_protected=True, protected_reason="TRUNK")
    monkeypatch.setattr("app.control.port_control.GenericSNMPDriver", _FakeDriver)
    change = ConfigChangeLog(
        device_id=device.id,
        interface_id=interface.id,
        field_name="admin_status",
        old_value="DOWN",  # 복구하려면 Disable해야 하므로 보호 포트 규칙에 걸린다
        new_value="UP",
        source="DISCOVERY",
    )
    db_session.add(change)
    db_session.commit()

    result = await restore_config_change(db_session, change.id, performed_by="alice", force=False)

    assert result["result"] == "DENIED"


@pytest.mark.asyncio
async def test_restore_vlan_calls_set_port_vlan(db_session, monkeypatch):
    """이력의 old_value(VLAN 10)로 되돌리는지 확인한다 - Fake 드라이버는 기본
    VLAN 99(변경된 상태)에서 시작한다."""
    device, interface = _make_device_with_interface(db_session)
    monkeypatch.setattr("app.control.interface_config_control.GenericSNMPDriver", _FakeDriver)
    change = ConfigChangeLog(
        device_id=device.id,
        interface_id=interface.id,
        field_name="vlan",
        old_value="10",
        new_value="99",
        source="DISCOVERY",
    )
    db_session.add(change)
    db_session.commit()

    result = await restore_config_change(db_session, change.id, performed_by="alice")

    assert result["result"] == "SUCCESS"
    assert result["new_value"] == "10"
    db_session.refresh(interface)
    assert interface.vlan == 10


@pytest.mark.asyncio
async def test_restore_device_role_updates_device_and_logs_manual_change(db_session):
    device, _ = resolve_or_create_device(db_session, DeviceObservation(management_ip="10.0.0.20"))
    device.device_role = "CORE_SWITCH"
    db_session.commit()
    change = ConfigChangeLog(
        device_id=device.id,
        field_name="device_role",
        old_value="ACCESS_SWITCH",
        new_value="CORE_SWITCH",
        source="DISCOVERY",
    )
    db_session.add(change)
    db_session.commit()

    result = await restore_config_change(db_session, change.id, performed_by="bob")

    assert result["result"] == "SUCCESS"
    assert result["new_value"] == "ACCESS_SWITCH"
    db_session.refresh(device)
    assert device.device_role == "ACCESS_SWITCH"
    assert device.role_source == "MANUAL"

    restore_logs = [
        c for c in db_session.query(ConfigChangeLog).filter_by(device_id=device.id, source="MANUAL").all()
    ]
    assert len(restore_logs) == 1
    assert restore_logs[0].performed_by == "bob"


@pytest.mark.asyncio
async def test_restore_unsupported_field_raises(db_session):
    device, _ = resolve_or_create_device(db_session, DeviceObservation(management_ip="10.0.0.30"))
    db_session.flush()
    change = ConfigChangeLog(
        device_id=device.id, field_name="firmware_version", old_value="1.0", new_value="2.0", source="DISCOVERY"
    )
    db_session.add(change)
    db_session.commit()

    with pytest.raises(UnsupportedRestoreFieldError):
        await restore_config_change(db_session, change.id, performed_by="alice")


@pytest.mark.asyncio
async def test_restore_missing_change_raises(db_session):
    with pytest.raises(ConfigChangeNotFoundError):
        await restore_config_change(db_session, 999999, performed_by="alice")
