from app.identity import DeviceObservation, resolve_or_create_device, stable_identity


def test_stable_identity_priority_order():
    obs = DeviceObservation(
        snmp_engine_id="engine-1",
        serial_number="SN-1",
        lldp_chassis_id="cc:cc:cc:cc:cc:cc",
        primary_mac="aa:bb:cc:dd:ee:ff",
        management_ip="10.0.0.1",
    )
    assert stable_identity(obs) == ("snmp_engine_id", "engine-1")

    obs2 = DeviceObservation(serial_number="SN-1", primary_mac="aa:bb:cc:dd:ee:ff", management_ip="10.0.0.1")
    assert stable_identity(obs2) == ("serial_number", "SN-1")

    obs3 = DeviceObservation(primary_mac="AA:BB:CC:DD:EE:FF", management_ip="10.0.0.1")
    assert stable_identity(obs3) == ("primary_mac", "aa:bb:cc:dd:ee:ff")

    obs4 = DeviceObservation(management_ip="10.0.0.1")
    assert stable_identity(obs4) == ("management_ip", "10.0.0.1")


def test_resolve_or_create_device_creates_new(db_session):
    device, created = resolve_or_create_device(db_session, DeviceObservation(management_ip="10.0.0.5", primary_mac="00:11:22:33:44:55"))
    assert created is True
    assert device.management_ip == "10.0.0.5"
    assert device.status == "ONLINE"


def test_resolve_or_create_device_merges_by_mac_on_ip_change(db_session):
    """7장 bullet: IP만 다르고 Chassis MAC/Serial이 같으면 IP 변경으로 처리한다."""
    device1, created1 = resolve_or_create_device(
        db_session, DeviceObservation(management_ip="10.0.0.5", primary_mac="00:11:22:33:44:55")
    )
    db_session.commit()
    assert created1 is True

    device2, created2 = resolve_or_create_device(
        db_session, DeviceObservation(management_ip="10.0.0.99", primary_mac="00:11:22:33:44:55")
    )
    assert created2 is False
    assert device2.id == device1.id
    assert device2.management_ip == "10.0.0.99"


def test_resolve_or_create_device_merges_by_serial_over_mac(db_session):
    device1, _ = resolve_or_create_device(
        db_session, DeviceObservation(management_ip="10.0.0.5", serial_number="SN-100", primary_mac="00:11:22:33:44:55")
    )
    db_session.commit()

    # MAC이 바뀌어도(예: 카드 교체) Serial이 같으면 같은 장비로 병합되어야 한다.
    device2, created2 = resolve_or_create_device(
        db_session, DeviceObservation(management_ip="10.0.0.5", serial_number="SN-100", primary_mac="aa:aa:aa:aa:aa:aa")
    )
    assert created2 is False
    assert device2.id == device1.id
    assert device2.primary_mac == "aa:aa:aa:aa:aa:aa"


def test_distinct_devices_without_shared_identifier_are_not_merged(db_session):
    device1, _ = resolve_or_create_device(db_session, DeviceObservation(management_ip="10.0.0.1", primary_mac="11:11:11:11:11:11"))
    db_session.commit()
    device2, created2 = resolve_or_create_device(db_session, DeviceObservation(management_ip="10.0.0.2", primary_mac="22:22:22:22:22:22"))
    assert created2 is True
    assert device2.id != device1.id
