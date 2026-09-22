"""6장/부록 B BFS Discovery 통합 테스트.

19장 T1 시나리오(Server -> Core L3 -> Floor PoE -> Windows/Linux/Camera)를
모사한 Fake SNMP Collector로 재귀 탐색, 장비 분류, 토폴로지/역할 산출까지
end-to-end로 검증한다. 실제 네트워크 장비 없이도 BFS 로직 자체를 검증하기 위해
SnmpCollector를 모킹한다.
"""
from __future__ import annotations

import asyncio

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.collectors.base import (
    ArpEntryInfo,
    FdbEntryInfo,
    InterfaceInfo,
    LldpNeighborInfo,
    PoeInfo,
    RouteInfo,
    StpInfo,
    SystemInfo,
)
from app.discovery.engine import DiscoveryEngine, _map_poe_entries_to_interfaces
from app.discovery.profiles import get_profile_scope
from app.models import NetworkDevice

CORE_IP = "10.0.0.1"
FLOOR_IP = "10.0.0.2"
PC_IP = "10.0.0.10"

# Core: L3 지원, Floor로 LLDP + FDB로 PC MAC을 학습
FAKE_TOPOLOGY = {
    CORE_IP: dict(
        sysinfo=SystemInfo(sys_descr="Cisco IOS Core Switch", sys_object_id="1.3.6.1.4.1.9.1.1", sys_name="CORE-SW-01"),
        interfaces=[
            InterfaceInfo(if_index=1, name="Gi0/1", mac="aa:aa:aa:aa:aa:01"),
            InterfaceInfo(if_index=2, name="Vlan10", mac="aa:aa:aa:aa:aa:02"),
        ],
        ip_forwarding=True,
        bridge=True,
        lldp=[
            LldpNeighborInfo(
                local_if_index=1,
                remote_chassis_id="floor-chassis",
                remote_port_id="Gi0/24",
                remote_sys_name="FLOOR-SW-01",
                remote_mgmt_ip=FLOOR_IP,
            )
        ],
        fdb=[],
        arp=[],
        # 내부 SVI 서브넷 라우트 2개 (WAN Default Route 없음 -> Router보다 L3_SWITCH 근거 우위,
        # 8장 채점 규칙: L3_SWITCH가 Bridge/FDB+LLDP 근거를 추가로 얻는 반면 ROUTER는 얻지 못함)
        routes=[
            RouteInfo(destination="10.0.1.0", prefix_len=24, next_hop=None, if_index=1),
            RouteInfo(destination="10.0.2.0", prefix_len=24, next_hop=None, if_index=2),
        ],
        poe=[],
        vlans=[10, 20, 30],
        pvids={2: 10},
        chassis_id="core-chassis",
    ),
    FLOOR_IP: dict(
        sysinfo=SystemInfo(sys_descr="Cisco IOS Floor Switch", sys_object_id="1.3.6.1.4.1.9.1.2", sys_name="FLOOR-SW-01"),
        interfaces=[
            InterfaceInfo(if_index=1, name="Gi0/24", mac="bb:bb:bb:bb:bb:01"),
            InterfaceInfo(if_index=5, name="Gi0/5", mac="bb:bb:bb:bb:bb:02"),
        ],
        ip_forwarding=False,
        bridge=True,
        lldp=[
            LldpNeighborInfo(
                local_if_index=1,
                remote_chassis_id="core-chassis",
                remote_port_id="Gi0/1",
                remote_sys_name="CORE-SW-01",
                remote_mgmt_ip=CORE_IP,
            )
        ],
        fdb=[FdbEntryInfo(if_index=5, vlan=20, mac="00:11:22:33:44:55")],
        arp=[ArpEntryInfo(if_index=5, ip=PC_IP, mac="00:11:22:33:44:55")],
        routes=[],
        poe=[],
        vlans=[20],
        pvids={5: 20},
        chassis_id="floor-chassis",
    ),
}


class _FakeSnmpCollector:
    def __init__(self, host, community="public", **kwargs):
        self.host = host
        self._data = FAKE_TOPOLOGY.get(host)

    async def is_reachable(self):
        return self._data is not None

    async def get_system_info(self):
        return self._data["sysinfo"]

    async def get_interfaces(self):
        return self._data["interfaces"]

    async def is_ip_forwarding(self):
        return self._data["ip_forwarding"]

    async def get_bridge_capable(self):
        return self._data["bridge"]

    async def get_stp_info(self):
        return self._data.get("stp_info", StpInfo(is_root=False, supported=False))

    async def get_local_chassis_id(self):
        return self._data["chassis_id"]

    async def get_lldp_neighbors(self):
        return self._data["lldp"]

    async def get_fdb(self):
        return self._data["fdb"]

    async def get_arp_table(self):
        return self._data["arp"]

    async def get_routes(self):
        return self._data["routes"]

    async def get_ip_interface_count(self):
        return self._data.get("ip_interface_count", 0)

    async def get_poe_status(self):
        return self._data["poe"]

    async def get_vlans(self):
        return self._data["vlans"]

    async def get_port_pvids(self):
        return self._data["pvids"]


async def _no_probe(*args, **kwargs):
    return False


async def _no_ssh_banner(*args, **kwargs):
    return None


def _fake_ping(ip, **kw):
    return type("P", (), {"alive": False, "rtt_ms": None, "ttl": None})()


async def test_bfs_discovers_core_and_floor_and_builds_topology(db_session, session_factory, monkeypatch):
    monkeypatch.setattr("app.discovery.engine.SnmpCollector", _FakeSnmpCollector)
    monkeypatch.setattr("app.discovery.engine.probe_rtsp", _no_probe)
    monkeypatch.setattr("app.discovery.engine.probe_onvif", _no_probe)
    monkeypatch.setattr("app.discovery.engine.probe_http_title_hint", _no_probe)
    monkeypatch.setattr("app.discovery.engine.probe_smb_or_rdp", _no_probe)
    monkeypatch.setattr("app.discovery.engine.probe_ssh_banner", _no_ssh_banner)
    monkeypatch.setattr("app.collectors.icmp.ping", _fake_ping)

    from app.models import DiscoveryRun

    # DETAILED Profile을 사용해 Route까지 수집해야 L3_SWITCH 판정에 필요한
    # Route table 근거(8/9장)를 얻는다 (STANDARD는 6.4절 표에 따라 Route 미수집).
    run = DiscoveryRun(profile="DETAILED", status="RUNNING")
    session = session_factory()
    session.add(run)
    session.commit()
    run_id = run.id
    session.close()

    engine = DiscoveryEngine(session_factory=session_factory, profile="DETAILED")
    await engine.run(run_id, [CORE_IP])

    verify = session_factory()
    finished_run = verify.get(DiscoveryRun, run_id)
    assert finished_run.status == "COMPLETED"
    assert finished_run.scanned_count >= 2  # Core + Floor (재귀 확장 확인)

    devices = {d.management_ip: d for d in verify.scalars(select(NetworkDevice))}
    assert CORE_IP in devices
    assert FLOOR_IP in devices, "Core의 LLDP remote_mgmt_ip를 통해 Floor가 재귀적으로 발견되어야 한다"

    core = devices[CORE_IP]
    floor = devices[FLOOR_IP]
    assert core.device_type in ("L3_SWITCH", "L3_POE_SWITCH")
    assert floor.device_type in ("L2_SWITCH", "L2_POE_SWITCH")

    from app.models import NetworkLink

    links = verify.scalars(select(NetworkLink)).all()
    assert len(links) >= 1
    core_floor_link = next(
        l for l in links if {l.src_device_id, l.dst_device_id} == {core.id, floor.id}
    )
    assert core_floor_link.confidence == 100  # LLDP 양방향

    verify.close()


async def test_loopback_route_does_not_falsely_mark_plain_l2_switch_as_layer3(session_factory, monkeypatch):
    """[KOS20260922] "STANDARD로 할 때와 분류가 완전 달라진다" 리포트를 조사하다
    발견한 회귀 재현 - DETAILED 프로파일로 Route를 수집하면, 순수 L2 스위치도
    자기 자신의 IP 스택이 갖는 루프백(127.0.0.0/8) DIRECT 라우트 때문에
    "unique if_index 2개(루프백 + 관리 인터페이스)"로 잡혀 layer3_capable=True로
    잘못 승격됐다(실사용 데이터 NSH-2128/2128PT/2128P 3대에서 재현). STANDARD는
    Route를 수집하지 않아(collect_routes=False) 이 버그를 타지 않고, DETAILED로
    재스캔하면 갑자기 DISTRIBUTION_SWITCH로 바뀌어 보이는 원인이었다."""
    L2_IP = "10.0.0.50"
    monkeypatch.setitem(
        FAKE_TOPOLOGY,
        L2_IP,
        dict(
            sysinfo=SystemInfo(sys_descr="Generic L2 Switch", sys_object_id="1.3.6.1.4.1.9.99", sys_name="L2-SW-99"),
            interfaces=[
                InterfaceInfo(if_index=1, name="Gi0/1", mac="cc:cc:cc:cc:cc:01"),
                InterfaceInfo(if_index=2, name="Gi0/2", mac="cc:cc:cc:cc:cc:02"),
                InterfaceInfo(if_index=3, name="Gi0/3", mac="cc:cc:cc:cc:cc:03"),
                InterfaceInfo(if_index=4, name="Gi0/4", mac="cc:cc:cc:cc:cc:04"),
            ],
            ip_forwarding=False,
            bridge=True,
            lldp=[],
            fdb=[],
            arp=[],
            # 루프백(if_index=1) DIRECT 라우트 + 관리 인터페이스(if_index=2) 라우트
            # 2개 - 실사용 데이터에서 그대로 관찰된 패턴을 재현한다.
            routes=[
                RouteInfo(destination="127.0.0.0", prefix_len=8, next_hop="0.0.0.0", if_index=1),
                RouteInfo(destination="0.0.0.0", prefix_len=0, next_hop="10.0.0.254", if_index=2),
                RouteInfo(destination="10.0.0.0", prefix_len=24, next_hop=None, if_index=2),
            ],
            poe=[],
            vlans=[10],
            pvids={1: 10},
            chassis_id="l2-noise-chassis",
        ),
    )
    monkeypatch.setattr("app.discovery.engine.SnmpCollector", _FakeSnmpCollector)
    monkeypatch.setattr("app.discovery.engine.probe_rtsp", _no_probe)
    monkeypatch.setattr("app.discovery.engine.probe_onvif", _no_probe)
    monkeypatch.setattr("app.discovery.engine.probe_http_title_hint", _no_probe)
    monkeypatch.setattr("app.discovery.engine.probe_smb_or_rdp", _no_probe)
    monkeypatch.setattr("app.discovery.engine.probe_ssh_banner", _no_ssh_banner)
    monkeypatch.setattr("app.collectors.icmp.ping", _fake_ping)

    from app.models import DiscoveryRun

    run = DiscoveryRun(profile="DETAILED", status="RUNNING")
    session = session_factory()
    session.add(run)
    session.commit()
    run_id = run.id
    session.close()

    engine = DiscoveryEngine(session_factory=session_factory, profile="DETAILED")
    await engine.run(run_id, [L2_IP])

    verify = session_factory()
    device = verify.scalar(select(NetworkDevice).where(NetworkDevice.management_ip == L2_IP))
    assert device is not None
    assert device.device_type not in ("L3_SWITCH", "L3_POE_SWITCH", "ROUTER")
    assert device.layer3_capable is False, "루프백 라우트만으로 L3 스위치로 오판되면 안 된다"
    verify.close()


async def test_excluded_management_ip_is_skipped_as_scan_target(session_factory, monkeypatch):
    """[KOS20260922] NMS 서버 자신의 IP가 CIDR 스캔 대상에 포함되면, 자신을
    스캔해 만든 장비가 Topology에서 아무 데도 연결 안 된 채 홀로 뜬다
    ("172.16.1.99가 연결 안 된 채 혼자 있다" 리포트) - 스캔 대상에서
    아예 제외돼 장비 자체가 만들어지지 않아야 한다."""
    from app import config

    monkeypatch.setattr("app.discovery.engine.SnmpCollector", _FakeSnmpCollector)
    monkeypatch.setattr("app.discovery.engine.probe_rtsp", _no_probe)
    monkeypatch.setattr("app.discovery.engine.probe_onvif", _no_probe)
    monkeypatch.setattr("app.discovery.engine.probe_http_title_hint", _no_probe)
    monkeypatch.setattr("app.discovery.engine.probe_smb_or_rdp", _no_probe)
    monkeypatch.setattr("app.discovery.engine.probe_ssh_banner", _no_ssh_banner)
    monkeypatch.setattr("app.collectors.icmp.ping", _fake_ping)
    monkeypatch.setattr(config, "TOPOLOGY_EXCLUDE_IPS", {CORE_IP})

    from app.models import DiscoveryRun

    run = DiscoveryRun(profile="DETAILED", status="RUNNING")
    session = session_factory()
    session.add(run)
    session.commit()
    run_id = run.id
    session.close()

    engine = DiscoveryEngine(session_factory=session_factory, profile="DETAILED")
    await engine.run(run_id, [CORE_IP])

    verify = session_factory()
    finished_run = verify.get(DiscoveryRun, run_id)
    assert finished_run.status == "COMPLETED"
    assert finished_run.scanned_count == 0

    devices = {d.management_ip for d in verify.scalars(select(NetworkDevice))}
    assert CORE_IP not in devices
    assert FLOOR_IP not in devices, "제외된 IP를 통해서만 도달 가능한 이웃은 확장되지 않아야 한다"
    verify.close()


async def test_lldp_local_interface_id_is_refreshed_on_rescan(session_factory, monkeypatch):
    """[재현 시나리오] 1차 스캔 시점엔 local_if_index가 어떤 이유로 로컬 인터페이스와
    매칭되지 않아(예: 스택형 스위치 dot1dBasePortIfIndex 매핑 이전 버전) local_interface_id
    =None으로 저장됐다가, 이후 재스캔에서는 올바르게 매칭되는 경우를 재현한다.
    discovery/engine.py의 기존 LldpNeighbor 갱신 분기(else)가 local_interface_id를
    빼먹고 remote_sys_name/remote_mgmt_ip/last_seen_at만 갱신하고 있어서, 매핑 로직을
    개선해도 이미 저장된 행에는 재스캔해도 영원히 반영되지 않는 문제가 있었다."""
    monkeypatch.setattr("app.discovery.engine.SnmpCollector", _FakeSnmpCollector)
    monkeypatch.setattr("app.discovery.engine.probe_rtsp", _no_probe)
    monkeypatch.setattr("app.discovery.engine.probe_onvif", _no_probe)
    monkeypatch.setattr("app.discovery.engine.probe_http_title_hint", _no_probe)
    monkeypatch.setattr("app.discovery.engine.probe_smb_or_rdp", _no_probe)
    monkeypatch.setattr("app.discovery.engine.probe_ssh_banner", _no_ssh_banner)
    monkeypatch.setattr("app.collectors.icmp.ping", _fake_ping)

    from app.models import DeviceInterface, DiscoveryRun, LldpNeighbor

    RESCAN_IP = "10.0.0.201"
    topo_entry = dict(
        sysinfo=SystemInfo(sys_descr="Test Switch", sys_object_id="1.3.6.1.4.1.9.1.1", sys_name="RESCAN-SW"),
        interfaces=[InterfaceInfo(if_index=1, name="Gi0/1", mac="aa:aa:aa:aa:aa:99")],
        ip_forwarding=False,
        bridge=True,
        lldp=[
            LldpNeighborInfo(
                local_if_index=None,  # 1차 스캔: 매칭 실패 상황 재현
                remote_chassis_id="remote-chassis-99",
                remote_port_id="Gi0/2",
                remote_sys_name="REMOTE-SW",
                remote_mgmt_ip="10.0.0.202",
            )
        ],
        fdb=[],
        arp=[],
        routes=[],
        poe=[],
        vlans=[],
        pvids={},
        chassis_id="rescan-chassis",
    )
    monkeypatch.setitem(FAKE_TOPOLOGY, RESCAN_IP, topo_entry)

    engine = DiscoveryEngine(session_factory=session_factory, profile="STANDARD")

    run1 = DiscoveryRun(profile="STANDARD", status="RUNNING")
    session = session_factory()
    session.add(run1)
    session.commit()
    run1_id = run1.id
    session.close()
    await engine.run(run1_id, [RESCAN_IP])

    verify1 = session_factory()
    neighbor1 = verify1.scalars(select(LldpNeighbor).where(LldpNeighbor.remote_chassis_id == "remote-chassis-99")).one()
    assert neighbor1.local_interface_id is None  # 1차 스캔은 매칭 실패 그대로
    local_device_id = neighbor1.local_device_id
    verify1.close()

    # 2차 스캔(재스캔): 이번엔 local_if_index가 실제 인터페이스(if_index=1)와 정확히 매칭된다.
    topo_entry["lldp"] = [
        LldpNeighborInfo(
            local_if_index=1,
            remote_chassis_id="remote-chassis-99",
            remote_port_id="Gi0/2",
            remote_sys_name="REMOTE-SW",
            remote_mgmt_ip="10.0.0.202",
        )
    ]

    run2 = DiscoveryRun(profile="STANDARD", status="RUNNING")
    session = session_factory()
    session.add(run2)
    session.commit()
    run2_id = run2.id
    session.close()
    await engine.run(run2_id, [RESCAN_IP])

    verify2 = session_factory()
    neighbor2 = verify2.scalars(select(LldpNeighbor).where(LldpNeighbor.remote_chassis_id == "remote-chassis-99")).one()
    iface = verify2.scalars(
        select(DeviceInterface).where(DeviceInterface.device_id == local_device_id, DeviceInterface.if_index == 1)
    ).one()
    assert neighbor2.local_interface_id == iface.id
    verify2.close()


async def test_is_stp_root_is_set_and_survives_a_later_unsupported_rescan(session_factory, monkeypatch):
    """STP Root Bridge 여부를 저장하고, 이후 재스캔에서 이 장비가 STP를 지원하지
    않는다고 나와도(예: 일시적 오류) 이전에 확인된 is_stp_root=True를 섣불리
    False로 지우지 않아야 한다(get_stp_info().supported=False일 때만 무시)."""
    monkeypatch.setattr("app.discovery.engine.SnmpCollector", _FakeSnmpCollector)
    monkeypatch.setattr("app.discovery.engine.probe_rtsp", _no_probe)
    monkeypatch.setattr("app.discovery.engine.probe_onvif", _no_probe)
    monkeypatch.setattr("app.discovery.engine.probe_http_title_hint", _no_probe)
    monkeypatch.setattr("app.discovery.engine.probe_smb_or_rdp", _no_probe)
    monkeypatch.setattr("app.discovery.engine.probe_ssh_banner", _no_ssh_banner)
    monkeypatch.setattr("app.collectors.icmp.ping", _fake_ping)

    from app.models import DiscoveryRun, NetworkDevice

    STP_IP = "10.0.0.211"
    topo_entry = dict(
        sysinfo=SystemInfo(sys_descr="Root Switch", sys_object_id="1.3.6.1.4.1.9.1.1", sys_name="ROOT-SW"),
        interfaces=[InterfaceInfo(if_index=1, name="Gi0/1")],
        ip_forwarding=False,
        bridge=True,
        lldp=[],
        fdb=[],
        arp=[],
        routes=[],
        poe=[],
        vlans=[],
        pvids={},
        chassis_id="root-chassis",
        stp_info=StpInfo(is_root=True, supported=True),
    )
    monkeypatch.setitem(FAKE_TOPOLOGY, STP_IP, topo_entry)

    engine = DiscoveryEngine(session_factory=session_factory, profile="STANDARD")

    run1 = DiscoveryRun(profile="STANDARD", status="RUNNING")
    session = session_factory()
    session.add(run1)
    session.commit()
    run1_id = run1.id
    session.close()
    await engine.run(run1_id, [STP_IP])

    verify1 = session_factory()
    device1 = verify1.scalars(select(NetworkDevice).where(NetworkDevice.management_ip == STP_IP)).one()
    assert device1.is_stp_root is True
    device_id = device1.id
    verify1.close()

    # 재스캔: 이번엔 STP 조회 자체가 실패했다고 가정(supported=False) - 이전에
    # 확인된 is_stp_root=True를 지우면 안 된다.
    topo_entry["stp_info"] = StpInfo(is_root=False, supported=False)

    run2 = DiscoveryRun(profile="STANDARD", status="RUNNING")
    session = session_factory()
    session.add(run2)
    session.commit()
    run2_id = run2.id
    session.close()
    await engine.run(run2_id, [STP_IP])

    verify2 = session_factory()
    device2 = verify2.get(NetworkDevice, device_id)
    assert device2.is_stp_root is True  # 그대로 유지돼야 한다
    verify2.close()


async def test_snmp_unreachable_host_is_still_classified_via_probes(session_factory, monkeypatch):
    """[KOS20260921] 이전에는 SNMP 미응답 호스트(대부분의 Windows/Linux/macOS PC)가
    _process_host의 조기 반환 경로에서 분류 로직 자체를 타지 않아 항상 UNKNOWN으로
    남았다. TTL+SMB 근거만으로도 최소한 REVIEW_REQUIRED 이상은 나와야 한다."""

    class _UnreachableSnmpCollector:
        def __init__(self, host, **kwargs):
            self.host = host

        async def is_reachable(self):
            return False

    async def _fake_smb_rdp(ip, **kw):
        return True

    def _windows_ping(ip, **kw):
        return type("P", (), {"alive": True, "rtt_ms": 1.0, "ttl": 128})()

    monkeypatch.setattr("app.discovery.engine.SnmpCollector", _UnreachableSnmpCollector)
    monkeypatch.setattr("app.discovery.engine.probe_rtsp", _no_probe)
    monkeypatch.setattr("app.discovery.engine.probe_onvif", _no_probe)
    monkeypatch.setattr("app.discovery.engine.probe_http_title_hint", _no_probe)
    monkeypatch.setattr("app.discovery.engine.probe_smb_or_rdp", _fake_smb_rdp)
    monkeypatch.setattr("app.discovery.engine.probe_ssh_banner", _no_ssh_banner)
    monkeypatch.setattr("app.collectors.icmp.ping", _windows_ping)

    from app.models import DiscoveryRun

    run = DiscoveryRun(profile="STANDARD", status="RUNNING")
    session = session_factory()
    session.add(run)
    session.commit()
    run_id = run.id
    session.close()

    engine = DiscoveryEngine(session_factory=session_factory, profile="STANDARD")
    await engine.run(run_id, [PC_IP])

    verify = session_factory()
    device = verify.scalar(select(NetworkDevice).where(NetworkDevice.management_ip == PC_IP))
    assert device is not None
    assert device.device_type != "UNKNOWN"
    assert device.classification_method in ("CONFIRMED", "REVIEW_REQUIRED", "STRONG_SIGNATURE")
    verify.close()


def test_concurrency_is_clamped_to_configured_range(session_factory):
    """[KOS20260921] 동시 스캔 개수는 1~NMS_MAX_DISCOVERY_CONCURRENCY 범위로 강제된다."""
    from app import config

    assert DiscoveryEngine(session_factory=session_factory, concurrency=0).concurrency == 1
    assert (
        DiscoveryEngine(session_factory=session_factory, concurrency=10_000).concurrency
        == config.MAX_DISCOVERY_CONCURRENCY
    )
    assert DiscoveryEngine(session_factory=session_factory, concurrency=3).concurrency == 3


async def test_current_ips_shows_multiple_in_flight_hosts_then_clears(session_factory, monkeypatch):
    """[KOS20260921] concurrency>1이면 여러 IP가 동시에 처리 중인데, 기존
    current_ip 하나만으로는 "마지막으로 시작한 IP"밖에 못 보여줬다. 진행 중인
    IP 전체가 current_ips(JSON 배열)에 반영되고, 종료 후에는 비워져야 한다."""
    ips = [f"10.0.7.{i}" for i in range(1, 5)]
    release = asyncio.Event()

    class _StuckSnmpCollector:
        def __init__(self, host, **kwargs):
            self.host = host

        async def is_reachable(self):
            await release.wait()
            return False

    def _alive_ping(ip, **kw):
        return type("P", (), {"alive": True, "rtt_ms": 1.0, "ttl": 64})()

    monkeypatch.setattr("app.discovery.engine.SnmpCollector", _StuckSnmpCollector)
    monkeypatch.setattr("app.discovery.engine.probe_rtsp", _no_probe)
    monkeypatch.setattr("app.discovery.engine.probe_onvif", _no_probe)
    monkeypatch.setattr("app.discovery.engine.probe_http_title_hint", _no_probe)
    monkeypatch.setattr("app.discovery.engine.probe_smb_or_rdp", _no_probe)
    monkeypatch.setattr("app.discovery.engine.probe_ssh_banner", _no_ssh_banner)
    monkeypatch.setattr("app.collectors.icmp.ping", _alive_ping)

    from app.models import DiscoveryRun
    import json

    run = DiscoveryRun(profile="LIGHT", status="RUNNING")
    session = session_factory()
    session.add(run)
    session.commit()
    run_id = run.id
    session.close()

    engine = DiscoveryEngine(session_factory=session_factory, profile="LIGHT", concurrency=4)
    run_task = asyncio.create_task(engine.run(run_id, ips))

    # 모든 Worker가 is_reachable()에서 release.wait()에 걸려 있을 때까지 잠깐 대기.
    for _ in range(50):
        await asyncio.sleep(0.02)
        verify = session_factory()
        mid_run = verify.get(DiscoveryRun, run_id)
        in_flight = json.loads(mid_run.current_ips) if mid_run.current_ips else []
        verify.close()
        if len(in_flight) == len(ips):
            break
    assert len(in_flight) == len(ips), f"기대한 동시 진행 IP 수({len(ips)})에 도달하지 못함: {in_flight}"
    assert set(in_flight) == set(ips)

    release.set()
    await asyncio.wait_for(run_task, timeout=10.0)

    verify = session_factory()
    finished = verify.get(DiscoveryRun, run_id)
    assert finished.current_ip is None
    assert finished.current_ips is None
    verify.close()


async def test_concurrent_workers_process_multiple_independent_hosts(session_factory, monkeypatch):
    """[KOS20260921] 동시 스캔(concurrency>1)으로 서로 무관한 여러 Seed를 병렬
    처리해도 순차 처리와 동일하게 전부 스캔/등록되어야 한다(Worker Pool 검증)."""

    ips = [f"10.0.5.{i}" for i in range(1, 6)]

    class _AllUnreachableSnmpCollector:
        def __init__(self, host, **kwargs):
            self.host = host

        async def is_reachable(self):
            return False

    def _alive_ping(ip, **kw):
        return type("P", (), {"alive": True, "rtt_ms": 1.0, "ttl": 64})()

    monkeypatch.setattr("app.discovery.engine.SnmpCollector", _AllUnreachableSnmpCollector)
    monkeypatch.setattr("app.discovery.engine.probe_rtsp", _no_probe)
    monkeypatch.setattr("app.discovery.engine.probe_onvif", _no_probe)
    monkeypatch.setattr("app.discovery.engine.probe_http_title_hint", _no_probe)
    monkeypatch.setattr("app.discovery.engine.probe_smb_or_rdp", _no_probe)
    monkeypatch.setattr("app.discovery.engine.probe_ssh_banner", _no_ssh_banner)
    monkeypatch.setattr("app.collectors.icmp.ping", _alive_ping)

    from app.models import DiscoveryRun

    run = DiscoveryRun(profile="LIGHT", status="RUNNING")
    session = session_factory()
    session.add(run)
    session.commit()
    run_id = run.id
    session.close()

    engine = DiscoveryEngine(session_factory=session_factory, profile="LIGHT", concurrency=3)
    await engine.run(run_id, ips)

    verify = session_factory()
    finished_run = verify.get(DiscoveryRun, run_id)
    assert finished_run.status == "COMPLETED"
    assert finished_run.scanned_count == len(ips)
    assert finished_run.alive_count == len(ips)
    devices = verify.scalars(select(NetworkDevice)).all()
    assert {d.management_ip for d in devices} == set(ips)
    verify.close()


async def test_concurrent_workers_respect_max_hosts(session_factory, monkeypatch):
    ips = [f"10.0.6.{i}" for i in range(1, 11)]

    class _AllUnreachableSnmpCollector:
        def __init__(self, host, **kwargs):
            self.host = host

        async def is_reachable(self):
            return False

    def _alive_ping(ip, **kw):
        return type("P", (), {"alive": True, "rtt_ms": 1.0, "ttl": 64})()

    monkeypatch.setattr("app.discovery.engine.SnmpCollector", _AllUnreachableSnmpCollector)
    monkeypatch.setattr("app.discovery.engine.probe_rtsp", _no_probe)
    monkeypatch.setattr("app.discovery.engine.probe_onvif", _no_probe)
    monkeypatch.setattr("app.discovery.engine.probe_http_title_hint", _no_probe)
    monkeypatch.setattr("app.discovery.engine.probe_smb_or_rdp", _no_probe)
    monkeypatch.setattr("app.discovery.engine.probe_ssh_banner", _no_ssh_banner)
    monkeypatch.setattr("app.collectors.icmp.ping", _alive_ping)

    from app.models import DiscoveryRun

    run = DiscoveryRun(profile="LIGHT", status="RUNNING")
    session = session_factory()
    session.add(run)
    session.commit()
    run_id = run.id
    session.close()

    engine = DiscoveryEngine(session_factory=session_factory, profile="LIGHT", concurrency=4, max_hosts=3)
    await engine.run(run_id, ips)

    verify = session_factory()
    finished_run = verify.get(DiscoveryRun, run_id)
    assert finished_run.status == "COMPLETED"
    assert finished_run.scanned_count == 3
    verify.close()


async def test_hostname_rules_fill_in_hostname_when_snmp_unreachable(session_factory, monkeypatch):
    """[KOS20260921] SNMP sysName이 없을 때(대부분의 PC), 사용자가 선택한
    hostname 식별 규칙(DNS/NETBIOS/MDNS)으로 hostname을 보완해야 한다."""

    class _UnreachableSnmpCollector:
        def __init__(self, host, **kwargs):
            self.host = host

        async def is_reachable(self):
            return False

    def _alive_ping(ip, **kw):
        return type("P", (), {"alive": True, "rtt_ms": 1.0, "ttl": 64})()

    async def _fake_resolve_hostname(ip, rules, timeout=1.0):
        assert rules == ["DNS"]
        return "RESOLVED-HOST", "DNS"

    monkeypatch.setattr("app.discovery.engine.SnmpCollector", _UnreachableSnmpCollector)
    monkeypatch.setattr("app.discovery.engine.probe_rtsp", _no_probe)
    monkeypatch.setattr("app.discovery.engine.probe_onvif", _no_probe)
    monkeypatch.setattr("app.discovery.engine.probe_http_title_hint", _no_probe)
    monkeypatch.setattr("app.discovery.engine.probe_smb_or_rdp", _no_probe)
    monkeypatch.setattr("app.discovery.engine.probe_ssh_banner", _no_ssh_banner)
    monkeypatch.setattr("app.collectors.icmp.ping", _alive_ping)
    monkeypatch.setattr("app.discovery.engine.resolve_hostname", _fake_resolve_hostname)

    from app.models import DiscoveryRun

    run = DiscoveryRun(profile="LIGHT", status="RUNNING")
    session = session_factory()
    session.add(run)
    session.commit()
    run_id = run.id
    session.close()

    engine = DiscoveryEngine(session_factory=session_factory, profile="LIGHT", hostname_rules=["DNS"])
    await engine.run(run_id, [PC_IP])

    verify = session_factory()
    device = verify.scalar(select(NetworkDevice).where(NetworkDevice.management_ip == PC_IP))
    assert device is not None
    assert device.hostname == "RESOLVED-HOST"
    verify.close()


async def test_bfs_respects_max_depth_zero(session_factory, monkeypatch):
    """6.1절: 과도한 확장을 막는 안전장치 - max_depth=0이면 Seed만 처리하고 확장하지 않는다."""
    monkeypatch.setattr("app.discovery.engine.SnmpCollector", _FakeSnmpCollector)
    monkeypatch.setattr("app.discovery.engine.probe_rtsp", _no_probe)
    monkeypatch.setattr("app.discovery.engine.probe_onvif", _no_probe)
    monkeypatch.setattr("app.discovery.engine.probe_http_title_hint", _no_probe)
    monkeypatch.setattr("app.discovery.engine.probe_smb_or_rdp", _no_probe)
    monkeypatch.setattr("app.discovery.engine.probe_ssh_banner", _no_ssh_banner)
    monkeypatch.setattr("app.collectors.icmp.ping", _fake_ping)

    from app.models import DiscoveryRun

    session = session_factory()
    run = DiscoveryRun(profile="STANDARD", status="RUNNING")
    session.add(run)
    session.commit()
    run_id = run.id
    session.close()

    engine = DiscoveryEngine(session_factory=session_factory, profile="STANDARD", max_depth=0)
    await engine.run(run_id, [CORE_IP])

    verify = session_factory()
    devices = verify.scalars(select(NetworkDevice)).all()
    assert len(devices) == 1
    assert devices[0].management_ip == CORE_IP
    verify.close()


def test_standard_profile_collects_poe():
    assert get_profile_scope("STANDARD").collect_poe is True


def test_poe_port_index_maps_to_physical_interface_name():
    interfaces = [
        InterfaceInfo(if_index=1, name="Vlan1"),
        InterfaceInfo(if_index=10101, name="GigabitEthernet1/0/1"),
        InterfaceInfo(if_index=10102, name="GigabitEthernet1/0/2"),
    ]
    poe_entries = [
        PoeInfo(if_index=1, enabled=True, status="DELIVERING"),
        PoeInfo(if_index=2, enabled=True, status="SEARCHING"),
    ]

    mapped = _map_poe_entries_to_interfaces(poe_entries, interfaces)

    assert [interface.if_index for _, interface in mapped] == [10101, 10102]


async def test_concurrent_workers_do_not_freeze_on_realistic_sqlite_lock_contention(tmp_path, monkeypatch):
    """[KOS20260921] 실사용 중 concurrency>1에서 백엔드 전체가 응답 불가 상태에
    빠지는 것을 재현했다: _process_host()가 여러 SNMP await 구간에 걸쳐 하나의
    쓰기 트랜잭션을 오래 열어 두면, 한 Worker의 session.commit()(동기/블로킹)이
    다른 Worker의 미완료 트랜잭션 때문에 SQLite Write Lock을 기다리는 동안
    이벤트 루프 전체가 멈춘다. 이전 테스트들은 in-memory StaticPool + 지연 없는
    Fake라 이 경합을 재현하지 못했으므로, 실제 파일 기반 SQLite(운영과 동일한
    WAL/busy_timeout)와 인위적 네트워크 지연(asyncio.sleep)을 함께 써서
    재현한다. asyncio.wait_for로 감싸 회귀 시 테스트 스위트 전체가 멈추는 대신
    실패로 끝나게 한다."""
    from app import db as db_module

    db_path = tmp_path / "stress.db"
    test_engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False}, future=True)
    db_module.configure_sqlite_pragmas(test_engine, busy_timeout_ms=2000)
    TestSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False, future=True)

    from app.models import Base, DiscoveryRun

    Base.metadata.create_all(test_engine)

    ips = [f"10.0.9.{i}" for i in range(1, 9)]

    class _SlowSnmpCollector:
        def __init__(self, host, **kwargs):
            self.host = host

        async def is_reachable(self):
            await asyncio.sleep(0.05)
            return True

        async def get_system_info(self):
            await asyncio.sleep(0.05)
            return SystemInfo(sys_descr="Generic Linux Server", sys_object_id="1.3.6.1.4.1.1", sys_name=f"host-{self.host}")

        async def get_interfaces(self):
            await asyncio.sleep(0.05)
            return [InterfaceInfo(if_index=1, name="eth0", mac=f"aa:bb:cc:dd:ee:{self.host[-2:]}")]

        async def get_local_chassis_id(self):
            await asyncio.sleep(0.05)
            return None

        async def is_ip_forwarding(self):
            await asyncio.sleep(0.05)
            return False

        async def get_bridge_capable(self):
            await asyncio.sleep(0.05)
            return False

        async def get_stp_info(self):
            await asyncio.sleep(0.05)
            return StpInfo(is_root=False, supported=False)

        async def get_lldp_neighbors(self):
            await asyncio.sleep(0.05)
            return []

        async def get_fdb(self):
            await asyncio.sleep(0.05)
            return []

        async def get_arp_table(self):
            await asyncio.sleep(0.05)
            return []

        async def get_routes(self):
            await asyncio.sleep(0.05)
            return []

        async def get_ip_interface_count(self):
            return 0

        async def get_poe_status(self):
            await asyncio.sleep(0.05)
            return []

        async def get_vlans(self):
            return []

        async def get_port_pvids(self):
            return {}

    def _alive_ping(ip, **kw):
        return type("P", (), {"alive": True, "rtt_ms": 1.0, "ttl": 64})()

    monkeypatch.setattr("app.discovery.engine.SnmpCollector", _SlowSnmpCollector)
    monkeypatch.setattr("app.discovery.engine.probe_rtsp", _no_probe)
    monkeypatch.setattr("app.discovery.engine.probe_onvif", _no_probe)
    monkeypatch.setattr("app.discovery.engine.probe_http_title_hint", _no_probe)
    monkeypatch.setattr("app.discovery.engine.probe_smb_or_rdp", _no_probe)
    monkeypatch.setattr("app.discovery.engine.probe_ssh_banner", _no_ssh_banner)
    monkeypatch.setattr("app.collectors.icmp.ping", _alive_ping)

    session = TestSessionLocal()
    run = DiscoveryRun(profile="STANDARD", status="RUNNING")
    session.add(run)
    session.commit()
    run_id = run.id
    session.close()

    engine = DiscoveryEngine(session_factory=TestSessionLocal, profile="STANDARD", concurrency=8)

    await asyncio.wait_for(engine.run(run_id, ips), timeout=15.0)

    verify = TestSessionLocal()
    finished_run = verify.get(DiscoveryRun, run_id)
    assert finished_run.status == "COMPLETED"
    assert finished_run.scanned_count == len(ips)
    assert finished_run.alive_count == len(ips)
    verify.close()


async def test_concurrent_endpoint_hosts_do_not_freeze_on_realistic_lock_contention(tmp_path, monkeypatch):
    """[KOS20260921] 위 테스트는 SNMP 응답 분기만 다뤘는데, 실제로는 SNMP
    미응답 분기(대부분의 PC/엔드포인트가 여기로 옴)에서 hostname 식별
    (DNS/NETBIOS/mDNS) + RTSP/ONVIF/HTTP/SMB/SSH 프로브가 수 초씩 걸리는 동안
    커밋 없이 트랜잭션을 열어 둔 채 지나가는 지점을 놓쳐서, concurrency=2
    수준에서도 서버 전체가 다시 멈추는 것이 실사용 중 재현됐다(엔터프라이즈
    SNMP 장비보다 이 분기를 타는 호스트가 훨씬 많아 실제로는 이 경로가 더
    치명적이었다). 동일한 방식(파일 기반 SQLite + 인위적 지연)으로 이 분기를
    검증한다."""
    from app import db as db_module

    db_path = tmp_path / "stress_endpoint.db"
    test_engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False}, future=True)
    db_module.configure_sqlite_pragmas(test_engine, busy_timeout_ms=2000)
    TestSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False, future=True)

    from app.models import Base, DiscoveryRun

    Base.metadata.create_all(test_engine)

    ips = [f"10.0.10.{i}" for i in range(1, 9)]

    class _UnreachableSnmpCollector:
        def __init__(self, host, **kwargs):
            self.host = host

        async def is_reachable(self):
            await asyncio.sleep(0.02)
            return False

    async def _slow_resolve_hostname(ip, rules, timeout=1.0):
        await asyncio.sleep(0.1)
        return f"host-{ip.split('.')[-1]}", "DNS"

    async def _slow_probe_true(ip, **kw):
        await asyncio.sleep(0.05)
        return True

    async def _slow_probe_false(ip, **kw):
        await asyncio.sleep(0.05)
        return False

    async def _slow_ssh_banner(ip, **kw):
        await asyncio.sleep(0.05)
        return None

    def _alive_ping(ip, **kw):
        return type("P", (), {"alive": True, "rtt_ms": 1.0, "ttl": 64})()

    monkeypatch.setattr("app.discovery.engine.SnmpCollector", _UnreachableSnmpCollector)
    monkeypatch.setattr("app.discovery.engine.resolve_hostname", _slow_resolve_hostname)
    monkeypatch.setattr("app.discovery.engine.probe_rtsp", _slow_probe_false)
    monkeypatch.setattr("app.discovery.engine.probe_onvif", _slow_probe_false)
    monkeypatch.setattr("app.discovery.engine.probe_http_title_hint", _slow_probe_false)
    monkeypatch.setattr("app.discovery.engine.probe_smb_or_rdp", _slow_probe_true)
    monkeypatch.setattr("app.discovery.engine.probe_ssh_banner", _slow_ssh_banner)
    monkeypatch.setattr("app.collectors.icmp.ping", _alive_ping)

    session = TestSessionLocal()
    run = DiscoveryRun(profile="STANDARD", status="RUNNING")
    session.add(run)
    session.commit()
    run_id = run.id
    session.close()

    engine = DiscoveryEngine(
        session_factory=TestSessionLocal, profile="STANDARD", concurrency=2, hostname_rules=["DNS"]
    )

    await asyncio.wait_for(engine.run(run_id, ips), timeout=15.0)

    verify = TestSessionLocal()
    finished_run = verify.get(DiscoveryRun, run_id)
    assert finished_run.status == "COMPLETED"
    assert finished_run.scanned_count == len(ips)
    assert finished_run.alive_count == len(ips)
    devices = verify.scalars(select(NetworkDevice)).all()
    assert {d.hostname for d in devices} == {f"host-{ip.split('.')[-1]}" for ip in ips}
    verify.close()
