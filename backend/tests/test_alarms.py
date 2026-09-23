"""18.1/12.1절: 파생 Alarms 로직 테스트."""
from __future__ import annotations

import datetime as dt

from app.alarms import list_alarms
from app.identity import DeviceObservation, resolve_or_create_device
from app.models import DeviceControlLog, DiscoveryRun, NetworkLink, utcnow


def _make_device(session, **kwargs):
    device, _ = resolve_or_create_device(session, DeviceObservation(**kwargs))
    session.flush()
    return device


def test_offline_device_produces_critical_alarm(db_session):
    device = _make_device(db_session, management_ip="10.0.0.1")
    device.status = "OFFLINE"
    db_session.commit()

    alarms = list_alarms(db_session)
    device_alarms = [a for a in alarms if a.category == "DEVICE"]
    assert len(device_alarms) == 1
    assert device_alarms[0].severity == "CRITICAL"
    assert device_alarms[0].device_id == device.id
    assert device_alarms[0].suppressed is False


def test_stale_endpoint_suppressed_when_upstream_switch_is_down(db_session):
    """12.1절: 상위 Floor Switch가 죽으면 하위 단말의 장애는 Root Cause가 아닌
    Suppressed로 표시되어야 한다."""
    switch = _make_device(db_session, management_ip="10.0.0.1")
    switch.status = "OFFLINE"
    switch.discovery_depth = 0

    endpoint = _make_device(db_session, management_ip="10.0.0.10", primary_mac="aa:aa:aa:aa:aa:aa")
    endpoint.status = "STALE"
    endpoint.discovery_depth = 1
    db_session.flush()

    db_session.add(
        NetworkLink(src_device_id=switch.id, dst_device_id=endpoint.id, source="FDB_ARP", confidence=80, status="UP")
    )
    db_session.commit()

    alarms = list_alarms(db_session)
    endpoint_alarm = next(a for a in alarms if a.device_id == endpoint.id)
    assert endpoint_alarm.suppressed is True
    assert "억제" in endpoint_alarm.suppressed_reason

    switch_alarm = next(a for a in alarms if a.device_id == switch.id)
    assert switch_alarm.suppressed is False  # 최상위 장애 자신은 Root Cause로 남아야 한다


def test_down_link_produces_alarm(db_session):
    a = _make_device(db_session, management_ip="10.0.0.1")
    b = _make_device(db_session, management_ip="10.0.0.2")
    db_session.flush()
    link = NetworkLink(src_device_id=a.id, dst_device_id=b.id, source="LLDP", confidence=100, status="DOWN")
    db_session.add(link)
    db_session.commit()

    alarms = list_alarms(db_session)
    link_alarms = [x for x in alarms if x.category == "LINK"]
    assert len(link_alarms) == 1
    assert link_alarms[0].severity == "CRITICAL"


def test_control_log_failed_is_critical_denied_is_info(db_session):
    device = _make_device(db_session, management_ip="10.0.0.1")
    db_session.flush()
    db_session.add(
        DeviceControlLog(
            device_id=device.id,
            action="PORT_DISABLE",
            before_value="UP",
            requested_value="DOWN",
            after_value="UP",
            result="FAILED",
            error_message="timeout",
            performed_by="tester",
        )
    )
    db_session.add(
        DeviceControlLog(
            device_id=device.id,
            action="PORT_DISABLE",
            before_value="UP",
            requested_value="DOWN",
            after_value="UP",
            result="DENIED",
            performed_by="tester",
        )
    )
    db_session.commit()

    alarms = {a.id.split(":")[0] + a.severity: a for a in list_alarms(db_session) if a.category == "CONTROL"}
    severities = {a.severity for a in list_alarms(db_session) if a.category == "CONTROL"}
    assert "CRITICAL" in severities
    assert "INFO" in severities


def test_config_change_from_discovery_is_warning_manual_is_info(db_session):
    """[KOS20260923] 구성 이상/비인가 변경 탐지 - 재탐색이 감지한 변경(source=
    DISCOVERY, 운영자가 모르는 사이 바뀐 값)은 WARNING, 운영자가 API로 직접 바꾼
    변경(source=MANUAL)은 이미 누가 바꿨는지 알고 있으므로 INFO여야 한다."""
    from app.models import ConfigChangeLog

    device = _make_device(db_session, management_ip="10.0.0.1")
    db_session.flush()
    db_session.add(
        ConfigChangeLog(
            device_id=device.id,
            field_name="vlan",
            old_value="10",
            new_value="20",
            source="DISCOVERY",
        )
    )
    db_session.add(
        ConfigChangeLog(
            device_id=device.id,
            field_name="device_role",
            old_value="ACCESS_SWITCH",
            new_value="CORE_SWITCH",
            source="MANUAL",
            performed_by="alice",
        )
    )
    db_session.commit()

    config_alarms = {a.severity: a for a in list_alarms(db_session) if a.category == "CONFIG"}
    assert config_alarms["WARNING"].message.startswith(device.management_ip)
    assert "alice" in config_alarms["INFO"].message


def test_failed_discovery_run_produces_alarm(db_session):
    run = DiscoveryRun(profile="STANDARD", status="FAILED", started_at=utcnow(), ended_at=utcnow())
    db_session.add(run)
    db_session.commit()

    alarms = [a for a in list_alarms(db_session) if a.category == "DISCOVERY"]
    assert len(alarms) == 1
    assert alarms[0].discovery_run_id == run.id


def test_no_alarms_when_everything_healthy(db_session):
    device = _make_device(db_session, management_ip="10.0.0.1")
    device.status = "ONLINE"
    db_session.commit()
    assert list_alarms(db_session) == []
