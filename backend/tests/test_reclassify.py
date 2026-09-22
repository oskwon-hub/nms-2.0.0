"""[KOS20260921] 저장된 근거만으로 재분류하는 유지보수 기능 테스트.

실사례: 라이브 DB의 "NSH2228CF"(층 2 스위치)가 예전 분류 규칙 시절 저장되어
device_type=UNKNOWN으로 남아 있었는데, 재탐색 없이도 NSH/NHM 명명 규칙으로
즉시 L2_SWITCH로 바로잡을 수 있어야 한다.
"""
from __future__ import annotations

from app.classification.reclassify import reclassify_all_devices, reclassify_device_from_stored_evidence
from app.identity import DeviceObservation, resolve_or_create_device
from app.models import DeviceInterface, MacFdb, PoePort


def _make_device(session, **kwargs):
    device, _ = resolve_or_create_device(session, DeviceObservation(**kwargs))
    session.flush()
    return device


def test_stale_unknown_device_reclassified_via_naming_rule_from_stored_evidence(db_session):
    device = _make_device(db_session, management_ip="172.16.1.220", hostname="NSH2228CF")
    device.device_type = "UNKNOWN"
    device.classification_method = "REVIEW_REQUIRED"
    device.layer2_capable = True
    device.layer3_capable = False
    device.poe_capable = False
    db_session.flush()

    iface = DeviceInterface(device_id=device.id, if_index=1, name="Gi0/1")
    db_session.add(iface)
    db_session.flush()
    db_session.add(MacFdb(device_id=device.id, interface_id=iface.id, vlan=1, mac="00:11:22:33:44:55", entry_type="DYNAMIC"))
    db_session.commit()

    changed = reclassify_device_from_stored_evidence(db_session, device)
    db_session.commit()

    assert changed is True
    assert device.device_type == "L2_SWITCH"
    assert device.classification_method == "STRONG_SIGNATURE"


def test_reclassify_clears_stale_interface_poe_flags_when_not_actually_poe(db_session):
    """[KOS20260921] 실사례: PoE가 아닌 장비인데 Interfaces/PoE 탭에 PoE On/Off가
    보이는 문제 - 원시 SNMP 근거(NST 사설 MIB 오탐 등) 기준으로 미리 표시해 둔
    DeviceInterface.poe_capable/PoePort가, 명명 규칙으로 최종 확정된 device_type과
    어긋나 있어도 그대로 남아 있었다. 재분류 시 최종 결과 기준으로 되돌려야 한다."""
    device = _make_device(db_session, management_ip="172.16.1.205", hostname="NSH-2128")
    device.device_type = "L2_POE_SWITCH"  # 예전(오탐) 결과
    device.classification_method = "CONFIRMED"
    device.layer2_capable = True
    device.layer3_capable = False
    device.poe_capable = True  # 오탐으로 표시됐던 상태
    db_session.flush()

    iface = DeviceInterface(device_id=device.id, if_index=1, name="Gi0/1", poe_capable=True)
    db_session.add(iface)
    db_session.flush()
    db_session.add(MacFdb(device_id=device.id, interface_id=iface.id, vlan=1, mac="00:11:22:33:44:55", entry_type="DYNAMIC"))
    db_session.add(PoePort(device_id=device.id, interface_id=iface.id, enabled=True, status="SEARCHING"))
    db_session.commit()

    changed = reclassify_device_from_stored_evidence(db_session, device)
    db_session.commit()

    assert changed is True
    assert device.device_type == "L2_SWITCH"  # 명명 규칙: NSH-2128 -> L2 (P 없음)
    assert device.poe_capable is False
    db_session.refresh(iface)
    assert iface.poe_capable is False
    assert db_session.query(PoePort).filter(PoePort.device_id == device.id).count() == 0


def test_reclassify_backfills_vendor_from_stored_sys_object_id(db_session):
    """[KOS20260921] vendor는 재탐색 없이도 이미 저장된 sys_object_id만으로 채울 수
    있다("장비 정보에 모델명이 있는데 표시가 안 된다" 리포트 - 실사례: NST 장비의
    sys_object_id는 enterprise 38333으로 이미 확인돼 있다)."""
    device = _make_device(db_session, management_ip="172.16.1.221", hostname="NHM-2408Series")
    device.sys_object_id = "1.3.6.1.4.1.38333.100"
    db_session.commit()
    assert device.vendor is None

    reclassify_device_from_stored_evidence(db_session, device)
    db_session.commit()

    assert device.vendor == "NST"


def test_reclassify_does_not_overwrite_known_vendor(db_session):
    device = _make_device(db_session, management_ip="172.16.1.222", hostname="SOME-OTHER-SWITCH")
    device.sys_object_id = "1.3.6.1.4.1.38333.100"
    device.vendor = "Manually Set Vendor"
    db_session.commit()

    reclassify_device_from_stored_evidence(db_session, device)
    db_session.commit()

    assert device.vendor == "Manually Set Vendor"


def test_reclassify_all_devices_only_counts_actual_changes(db_session):
    stable = _make_device(db_session, management_ip="10.0.0.1", hostname="already-correct")
    stable.device_type = "UNKNOWN"
    stable.layer2_capable = False
    stable.layer3_capable = False

    stale = _make_device(db_session, management_ip="10.0.0.2", hostname="NHM-3228")
    stale.device_type = "UNKNOWN"
    stale.layer2_capable = True
    stale.layer3_capable = False
    db_session.commit()

    changed_count = reclassify_all_devices(db_session)
    db_session.commit()

    assert changed_count == 1
    assert stable.device_type == "UNKNOWN"
    assert stale.device_type == "L3_SWITCH"
