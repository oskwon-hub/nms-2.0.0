from __future__ import annotations

import pytest

from app.collectors.snmp_collector import (
    OID_DOT1D_BASE_PORT_IFINDEX,
    OID_DOT1D_FDB_PORT,
    OID_DOT1D_STP_PORT_DESIGNATED_BRIDGE,
    OID_DOT1D_STP_PORT_STATE,
    OID_DOT1D_STP_ROOT_PORT,
    OID_DOT1Q_PVID,
    OID_DOT1Q_TP_FDB_PORT,
    OID_DOT1Q_VLAN_CURRENT_FDB_ID,
    OID_IF_ALIAS,
    OID_IF_DESCR,
    OID_IF_IN_OCTETS,
    OID_IF_OUT_OCTETS,
    OID_LLDP_REM_CHASSIS_ID,
    OID_LLDP_REM_PORT_ID,
    OID_NST_POE_ADMIN,
    OID_NST_POE_PORT_PRESENT,
    OID_NST_POE_POWER,
    OID_PETH_ADMIN_ENABLE,
    OID_SYS_DESCR,
    OID_SYS_LOCATION,
    OID_SYS_NAME,
    OID_SYS_OBJECT_ID,
    OID_SYS_UPTIME,
    CollectorError,
    SnmpCollector,
    vendor_from_sys_object_id,
)


@pytest.mark.asyncio
async def test_get_fdb_falls_back_to_q_bridge_and_maps_vlan_and_port(monkeypatch):
    collector = SnmpCollector("192.0.2.8")
    rows = {
        OID_DOT1D_BASE_PORT_IFINDEX: [
            (f"{OID_DOT1D_BASE_PORT_IFINDEX}.17", 1017),
            (f"{OID_DOT1D_BASE_PORT_IFINDEX}.19", 1019),
        ],
        OID_DOT1D_FDB_PORT: [],
        OID_DOT1Q_VLAN_CURRENT_FDB_ID: [
            (f"{OID_DOT1Q_VLAN_CURRENT_FDB_ID}.0.160", 160),
        ],
        OID_DOT1Q_TP_FDB_PORT: [
            (f"{OID_DOT1Q_TP_FDB_PORT}.160.56.213.71.200.1.5", 17),
            (f"{OID_DOT1Q_TP_FDB_PORT}.160.248.177.86.182.82.211", 19),
            (f"{OID_DOT1Q_TP_FDB_PORT}.160.1.2.3.4.5.6", 0),
        ],
    }

    async def fake_walk(oid: str, max_rows: int = 20000):
        return rows.get(oid, [])

    monkeypatch.setattr(collector, "_walk", fake_walk)
    result = await collector.get_fdb()

    assert [(entry.mac, entry.vlan, entry.if_index) for entry in result] == [
        ("38:d5:47:c8:01:05", 160, 1017),
        ("f8:b1:56:b6:52:d3", 160, 1019),
    ]


@pytest.mark.asyncio
async def test_nst_private_mib_is_used_when_standard_poe_mib_is_absent(monkeypatch):
    collector = SnmpCollector("192.0.2.1")
    rows = {
        OID_PETH_ADMIN_ENABLE: [],
        OID_NST_POE_PORT_PRESENT: [
            (f"{OID_NST_POE_PORT_PRESENT}.1", 0),
            (f"{OID_NST_POE_PORT_PRESENT}.2", 1),
        ],
        OID_NST_POE_ADMIN: [
            (f"{OID_NST_POE_ADMIN}.1", 1),
            (f"{OID_NST_POE_ADMIN}.2", 1),
        ],
        OID_NST_POE_POWER: [
            (f"{OID_NST_POE_POWER}.1", 94),
            (f"{OID_NST_POE_POWER}.2", 0),
        ],
    }

    async def fake_walk(oid: str, max_rows: int = 20000):
        return rows.get(oid, [])

    monkeypatch.setattr(collector, "_walk", fake_walk)

    result = await collector.get_poe_status()

    assert [(row.if_index, row.status) for row in result] == [(1, "DELIVERING"), (2, "SEARCHING")]
    assert result[0].power_mw == 9400


@pytest.mark.asyncio
async def test_nst_private_mib_uses_power_rows_when_port_table_is_absent(monkeypatch):
    collector = SnmpCollector("192.0.2.2")
    power_rows = [(f"{OID_NST_POE_POWER}.{port}", 7 if port == 1 else 0) for port in range(1, 11)]
    rows = {
        OID_PETH_ADMIN_ENABLE: [],
        OID_NST_POE_PORT_PRESENT: [],
        OID_NST_POE_ADMIN: [(f"{OID_NST_POE_ADMIN}.{port}", 1) for port in range(1, 11)],
        OID_NST_POE_POWER: power_rows,
    }

    async def fake_walk(oid: str, max_rows: int = 20000):
        return rows.get(oid, [])

    monkeypatch.setattr(collector, "_walk", fake_walk)

    result = await collector.get_poe_status()

    assert len(result) == 8
    assert result[0].status == "DELIVERING"
    assert result[-1].if_index == 8


@pytest.mark.asyncio
async def test_get_interfaces_collects_ifin_ifout_octets(monkeypatch):
    collector = SnmpCollector("192.0.2.3")
    rows = {
        OID_IF_DESCR: [
            (f"{OID_IF_DESCR}.1", "Gi0/1"),
            (f"{OID_IF_DESCR}.2", "Gi0/2"),
        ],
        OID_IF_IN_OCTETS: [
            (f"{OID_IF_IN_OCTETS}.1", 123456),
            (f"{OID_IF_IN_OCTETS}.2", 0),
        ],
        OID_IF_OUT_OCTETS: [
            (f"{OID_IF_OUT_OCTETS}.1", 654321),
            # 인터페이스 2는 ifOutOctets row 자체가 없는 경우(일부 vendor) -> None 유지
        ],
    }

    async def fake_walk(oid: str, max_rows: int = 20000):
        return rows.get(oid, [])

    monkeypatch.setattr(collector, "_walk", fake_walk)

    result = await collector.get_interfaces()
    by_index = {row.if_index: row for row in result}

    assert by_index[1].in_octets == 123456
    assert by_index[1].out_octets == 654321
    assert by_index[2].in_octets == 0
    assert by_index[2].out_octets is None


@pytest.mark.asyncio
async def test_get_lldp_neighbors_maps_local_port_via_bridge_port_ifindex(monkeypatch):
    """스택형/모듈형 스위치는 ifIndex가 'Switch 1 - Port 25' 처럼 dot1dBasePort(25)와
    다른 값(예: 1000025)을 쓴다. lldpLocPortNum을 ifIndex로 그대로 쓰면 로컬 포트가
    영원히 매칭되지 않으므로, dot1dBasePortIfIndex 매핑을 거쳐야 한다."""
    collector = SnmpCollector("192.0.2.4")
    rows = {
        OID_LLDP_REM_CHASSIS_ID: [
            (f"{OID_LLDP_REM_CHASSIS_ID}.0.25.1", b"\x30\x52\x5a\xaa\xbb\xcc"),
        ],
        OID_LLDP_REM_PORT_ID: [
            (f"{OID_LLDP_REM_PORT_ID}.0.25.1", "25"),
        ],
        OID_DOT1D_BASE_PORT_IFINDEX: [
            (f"{OID_DOT1D_BASE_PORT_IFINDEX}.25", 1000025),
        ],
    }

    async def fake_walk(oid: str, max_rows: int = 20000):
        return rows.get(oid, [])

    monkeypatch.setattr(collector, "_walk", fake_walk)

    result = await collector.get_lldp_neighbors()

    assert len(result) == 1
    assert result[0].local_if_index == 1000025


@pytest.mark.asyncio
async def test_get_lldp_neighbors_falls_back_to_raw_port_num_without_bridge_mib(monkeypatch):
    """BRIDGE-MIB이 없는 장비(예: 순수 라우터)는 기존처럼 lldpLocPortNum을 그대로 쓴다."""
    collector = SnmpCollector("192.0.2.5")
    rows = {
        OID_LLDP_REM_CHASSIS_ID: [
            (f"{OID_LLDP_REM_CHASSIS_ID}.0.3.1", b"\x30\x52\x5a\xaa\xbb\xcc"),
        ],
        OID_LLDP_REM_PORT_ID: [
            (f"{OID_LLDP_REM_PORT_ID}.0.3.1", "Gi0/3"),
        ],
        OID_DOT1D_BASE_PORT_IFINDEX: [],
    }

    async def fake_walk(oid: str, max_rows: int = 20000):
        return rows.get(oid, [])

    monkeypatch.setattr(collector, "_walk", fake_walk)

    result = await collector.get_lldp_neighbors()

    assert len(result) == 1
    assert result[0].local_if_index == 3


def test_vendor_from_sys_object_id_recognizes_known_enterprise():
    assert vendor_from_sys_object_id("1.3.6.1.4.1.38333.100") == "NST"


def test_vendor_from_sys_object_id_returns_none_for_unknown_enterprise():
    assert vendor_from_sys_object_id("1.3.6.1.4.1.9.1.1") is None  # Cisco - 이 코드베이스가 확인해두지 않은 값
    assert vendor_from_sys_object_id("not-an-oid") is None


@pytest.mark.asyncio
async def test_get_system_info_fills_vendor_from_sys_object_id(monkeypatch):
    collector = SnmpCollector("192.0.2.6")

    async def fake_get(oid: str):
        values = {
            OID_SYS_DESCR: "NST L2 PoE Switch",
            OID_SYS_OBJECT_ID: "1.3.6.1.4.1.38333.100",
            OID_SYS_NAME: "NSH-2128P",
            OID_SYS_LOCATION: "NST Co., LTD.",
            OID_SYS_UPTIME: "12345",
        }
        return values[oid]

    monkeypatch.setattr(collector, "_get", fake_get)

    info = await collector.get_system_info()

    assert info.vendor == "NST"


def test_stp_root_port_oid_has_scalar_instance_suffix():
    """[재현 시나리오] 실사용 장비들이 전부 is_stp_root=False로만 나와 라이브에서
    직접 snmpget으로 확인해보니, 스칼라 OID인데 인스턴스 접미사 .0이 빠져 있었다
    (모든 장비가 "No Such Instance"를 반환 -> supported=False로만 귀결). 다른 스칼라
    OID(OID_SYS_DESCR 등)와 동일하게 .0으로 끝나야 한다."""
    assert OID_DOT1D_STP_ROOT_PORT.endswith(".0")


@pytest.mark.asyncio
async def test_get_stp_info_detects_root_bridge(monkeypatch):
    """RFC 1493: dot1dStpRootPort == 0이면 이 장비 자신이 STP Root Bridge다."""
    collector = SnmpCollector("192.0.2.7")

    async def fake_get(oid: str):
        assert oid == OID_DOT1D_STP_ROOT_PORT
        return "0"

    monkeypatch.setattr(collector, "_get", fake_get)

    info = await collector.get_stp_info()

    assert info.supported is True
    assert info.is_root is True


@pytest.mark.asyncio
async def test_get_stp_info_detects_non_root_bridge(monkeypatch):
    collector = SnmpCollector("192.0.2.8")

    async def fake_get(oid: str):
        return "25"

    monkeypatch.setattr(collector, "_get", fake_get)

    info = await collector.get_stp_info()

    assert info.supported is True
    assert info.is_root is False


@pytest.mark.asyncio
async def test_get_stp_info_unsupported_device_reports_not_supported(monkeypatch):
    from app.collectors.base import CollectorError

    collector = SnmpCollector("192.0.2.9")

    async def fake_get(oid: str):
        raise CollectorError("no such object")

    monkeypatch.setattr(collector, "_get", fake_get)

    info = await collector.get_stp_info()

    assert info.supported is False
    assert info.is_root is False


@pytest.mark.asyncio
async def test_get_interfaces_resolves_stp_port_state_via_bridge_port_map(monkeypatch):
    """dot1dStpPortState의 인덱스는 dot1dBasePort라 ifIndex로 변환해야 한다
    (스택형 스위치는 ifIndex가 dot1dBasePort와 다른 값을 쓰므로)."""
    collector = SnmpCollector("192.0.2.10")
    rows = {
        OID_IF_DESCR: [
            (f"{OID_IF_DESCR}.1000025", "Switch  1 - Port 25"),
        ],
        OID_DOT1D_BASE_PORT_IFINDEX: [
            (f"{OID_DOT1D_BASE_PORT_IFINDEX}.25", 1000025),
        ],
        OID_DOT1D_STP_PORT_STATE: [
            (f"{OID_DOT1D_STP_PORT_STATE}.25", 5),  # forwarding
        ],
    }

    async def fake_walk(oid: str, max_rows: int = 20000):
        return rows.get(oid, [])

    monkeypatch.setattr(collector, "_walk", fake_walk)

    result = await collector.get_interfaces()

    assert len(result) == 1
    assert result[0].if_index == 1000025
    assert result[0].stp_state == "FORWARDING"


@pytest.mark.asyncio
async def test_get_interfaces_resolves_stp_designated_bridge_via_bridge_port_map(monkeypatch):
    """dot1dStpPortDesignatedBridge도 dot1dStpPortState와 동일하게 dot1dBasePort
    -> ifIndex 변환이 필요하다. BridgeId(8바이트 = Priority 2 + MAC 6)에서
    MAC만 뽑아내야 한다."""
    collector = SnmpCollector("192.0.2.11")
    designated_bridge = bytes([0x80, 0x00, 0x30, 0x52, 0x5A, 0xF7, 0xE6, 0xE0])
    rows = {
        OID_IF_DESCR: [
            (f"{OID_IF_DESCR}.1000025", "Switch  1 - Port 25"),
        ],
        OID_DOT1D_BASE_PORT_IFINDEX: [
            (f"{OID_DOT1D_BASE_PORT_IFINDEX}.25", 1000025),
        ],
        OID_DOT1D_STP_PORT_DESIGNATED_BRIDGE: [
            (f"{OID_DOT1D_STP_PORT_DESIGNATED_BRIDGE}.25", designated_bridge),
        ],
    }

    async def fake_walk(oid: str, max_rows: int = 20000):
        return rows.get(oid, [])

    monkeypatch.setattr(collector, "_walk", fake_walk)

    result = await collector.get_interfaces()

    assert len(result) == 1
    assert result[0].stp_designated_bridge_mac == "30:52:5a:f7:e6:e0"


@pytest.mark.asyncio
async def test_get_interfaces_resolves_stp_designated_bridge_from_vendor_ascii_format(monkeypatch):
    """[KOS20260922] 회귀 재현 - RFC 1493은 dot1dStpPortDesignatedBridge를 raw
    binary 8바이트(BridgeId)로 정의하지만, 실사용 장비(이 벤더 스위치 라인)는
    "priority-XX.XX.XX.XX.XX.XX" 형태의 ASCII 문자열로 응답한다(snmpwalk가
    Hex-STRING이 아니라 STRING으로 표시하는 것으로 실제 확인). raw-binary만
    가정한 최초 구현은 이 문자열을 그대로 바이트로 취급해 텍스트 일부("2e:30:
    30:2e:30:30" 등)를 MAC인 척 잘못 뽑아내는 버그가 있었다 - 라이브 재스캔
    으로 실제 발견됨."""
    collector = SnmpCollector("192.0.2.13")
    designated_bridge = b"32768-30.52.5A.F7.E6.E0"
    rows = {
        OID_IF_DESCR: [(f"{OID_IF_DESCR}.23", "GigabitEthernet23")],
        OID_DOT1D_BASE_PORT_IFINDEX: [(f"{OID_DOT1D_BASE_PORT_IFINDEX}.23", 23)],
        OID_DOT1D_STP_PORT_DESIGNATED_BRIDGE: [
            (f"{OID_DOT1D_STP_PORT_DESIGNATED_BRIDGE}.23", designated_bridge),
        ],
    }

    async def fake_walk(oid: str, max_rows: int = 20000):
        return rows.get(oid, [])

    monkeypatch.setattr(collector, "_walk", fake_walk)

    result = await collector.get_interfaces()

    assert len(result) == 1
    assert result[0].stp_designated_bridge_mac == "30:52:5a:f7:e6:e0"


@pytest.mark.asyncio
async def test_get_interfaces_treats_vendor_ascii_all_zero_designated_bridge_as_unknown(monkeypatch):
    """벤더 ASCII 형식에서도 전부 0인 MAC("0-00.00.00.00.00.00")은 raw-binary와
    동일하게 '아직 알아낸 이웃 없음'으로 취급해 None이어야 한다."""
    collector = SnmpCollector("192.0.2.14")
    rows = {
        OID_IF_DESCR: [(f"{OID_IF_DESCR}.1", "Gi0/1")],
        OID_DOT1D_BASE_PORT_IFINDEX: [(f"{OID_DOT1D_BASE_PORT_IFINDEX}.1", 1)],
        OID_DOT1D_STP_PORT_DESIGNATED_BRIDGE: [
            (f"{OID_DOT1D_STP_PORT_DESIGNATED_BRIDGE}.1", b"0-00.00.00.00.00.00"),
        ],
    }

    async def fake_walk(oid: str, max_rows: int = 20000):
        return rows.get(oid, [])

    monkeypatch.setattr(collector, "_walk", fake_walk)

    result = await collector.get_interfaces()

    assert result[0].stp_designated_bridge_mac is None


@pytest.mark.asyncio
async def test_get_interfaces_treats_all_zero_designated_bridge_as_unknown(monkeypatch):
    """Designated Bridge가 전부 0(00:00:...:00)이면 STP가 아직 이 세그먼트의
    이웃을 알아내지 못했다는 뜻이라 None으로 남아야 한다(0으로 된 MAC을 실제
    이웃으로 착각해 가짜 링크를 만들지 않기 위함)."""
    collector = SnmpCollector("192.0.2.12")
    rows = {
        OID_IF_DESCR: [(f"{OID_IF_DESCR}.1", "Gi0/1")],
        OID_DOT1D_BASE_PORT_IFINDEX: [(f"{OID_DOT1D_BASE_PORT_IFINDEX}.1", 1)],
        OID_DOT1D_STP_PORT_DESIGNATED_BRIDGE: [(f"{OID_DOT1D_STP_PORT_DESIGNATED_BRIDGE}.1", bytes(8))],
    }

    async def fake_walk(oid: str, max_rows: int = 20000):
        return rows.get(oid, [])

    monkeypatch.setattr(collector, "_walk", fake_walk)

    result = await collector.get_interfaces()

    assert result[0].stp_designated_bridge_mac is None


@pytest.mark.asyncio
async def test_set_port_pvid_translates_ifindex_to_bridge_port(monkeypatch):
    """[KOS20260923] dot1qPvid는 ifIndex가 아니라 dot1dBasePort로 색인되므로
    (get_port_pvids()와 동일), SET 전에 dot1dBasePortIfIndex로 역매핑해야 한다."""
    collector = SnmpCollector("192.0.2.20")
    rows = {
        OID_DOT1D_BASE_PORT_IFINDEX: [
            (f"{OID_DOT1D_BASE_PORT_IFINDEX}.5", 1000005),
        ],
    }

    async def fake_walk(oid: str, max_rows: int = 20000):
        return rows.get(oid, [])

    set_calls = []

    async def fake_set(oid: str, value):
        set_calls.append((oid, value))

    monkeypatch.setattr(collector, "_walk", fake_walk)
    monkeypatch.setattr(collector, "_set", fake_set)

    await collector.set_port_pvid(1000005, 20)

    assert set_calls == [(f"{OID_DOT1Q_PVID}.5", 20)]


@pytest.mark.asyncio
async def test_set_port_pvid_raises_when_ifindex_has_no_bridge_port(monkeypatch):
    collector = SnmpCollector("192.0.2.21")

    async def fake_walk(oid: str, max_rows: int = 20000):
        return []

    monkeypatch.setattr(collector, "_walk", fake_walk)

    with pytest.raises(CollectorError):
        await collector.set_port_pvid(999, 20)


@pytest.mark.asyncio
async def test_set_if_alias_sets_expected_oid(monkeypatch):
    collector = SnmpCollector("192.0.2.22")
    set_calls = []

    async def fake_set(oid: str, value):
        set_calls.append((oid, value))

    monkeypatch.setattr(collector, "_set", fake_set)

    await collector.set_if_alias(3, "uplink to core")

    assert len(set_calls) == 1
    oid, value = set_calls[0]
    assert oid == f"{OID_IF_ALIAS}.3"
    assert str(value) == "uplink to core"


@pytest.mark.asyncio
async def test_get_if_alias_returns_none_when_unsupported(monkeypatch):
    collector = SnmpCollector("192.0.2.23")

    async def fake_get(oid: str):
        raise CollectorError("no such object")

    monkeypatch.setattr(collector, "_get", fake_get)

    assert await collector.get_if_alias(1) is None
