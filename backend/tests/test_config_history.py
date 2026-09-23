from app.config_history import record_config_change
from app.identity import DeviceObservation, resolve_or_create_device
from app.models import ConfigChangeLog


def test_record_config_change_creates_row_when_value_changed(db_session):
    device, _ = resolve_or_create_device(db_session, DeviceObservation(management_ip="10.0.0.1", primary_mac="00:11:22:33:44:55"))
    db_session.commit()

    log = record_config_change(
        db_session,
        device_id=device.id,
        field_name="vlan",
        old_value="10",
        new_value="20",
        source="DISCOVERY",
    )
    db_session.commit()

    assert log is not None
    assert log.old_value == "10"
    assert log.new_value == "20"
    assert log.source == "DISCOVERY"
    assert db_session.query(ConfigChangeLog).count() == 1


def test_record_config_change_is_noop_when_value_unchanged(db_session):
    device, _ = resolve_or_create_device(db_session, DeviceObservation(management_ip="10.0.0.1", primary_mac="00:11:22:33:44:55"))
    db_session.commit()

    log = record_config_change(
        db_session,
        device_id=device.id,
        field_name="vlan",
        old_value="10",
        new_value="10",
        source="DISCOVERY",
    )
    db_session.commit()

    assert log is None
    assert db_session.query(ConfigChangeLog).count() == 0


def test_record_config_change_is_noop_when_both_none():
    """두 값이 모두 비어 있던 경우(None==None)는 변경이 아니므로 기록하지 않는다."""
    log = record_config_change(
        None,  # type: ignore[arg-type]
        device_id=1,
        field_name="vlan",
        old_value=None,
        new_value=None,
        source="DISCOVERY",
    )
    assert log is None
