"""6장 + 부록 B: 자동 Discovery 및 재귀 탐색 로직 (BFS).

Server -> 발견 장비 -> 이웃 -> 이웃의 이웃으로 확장하는 재귀 Discovery를 구현한다.
Infrastructure 장비(Switch/Router)에서만 LLDP/ARP 기반으로 이웃을 확장하며
(6.4절 표), PC/Camera 등 Endpoint는 재귀 확장의 시작점이 되지 않는다.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.classification.device_type import (
    ClassificationEvidence,
    classify_device_type,
)
from app.classification.device_role import INFRASTRUCTURE_TYPES
from app.classification.oui import classify_oui
from app.collectors import icmp
from app.collectors.base import CollectorError, InterfaceInfo, PoeInfo, StpInfo
from app.collectors.icmp import classify_ttl_os_hint
from app.collectors.hostname_resolve import resolve_hostname
from app.collectors.probe import (
    classify_ssh_banner,
    probe_http_title_hint,
    probe_onvif,
    probe_rtsp,
    probe_smb_or_rdp,
    probe_ssh_banner,
)
from app.collectors.snmp_collector import SnmpCollector
from app.config_history import record_config_change
from app.credentials import get_or_create_snmp_credential_profile
from app.identity import DeviceObservation, resolve_or_create_device
from app.models import (
    ArpEntry,
    DeviceInterface,
    DiscoveryRun,
    LldpNeighbor,
    MacFdb,
    NetworkDevice,
    PoePort,
    RouteEntry,
    utcnow,
)
from app.topology.engine import SWITCH_TYPES, recompute_topology_and_roles

logger = logging.getLogger("nms.discovery")


def _map_poe_entries_to_interfaces(
    poe_entries: list[PoeInfo], interfaces: list[InterfaceInfo]
) -> list[tuple[PoeInfo, InterfaceInfo | None]]:
    """POWER-ETHERNET-MIB 포트 번호를 가장 적절한 IF-MIB 인터페이스에 연결한다."""
    virtual_prefixes = ("vlan", "loopback", "lo", "null", "tunnel", "port-channel", "po", "lag", "cpu", "stack")

    def is_physical(interface: InterfaceInfo) -> bool:
        name = (interface.name or "").strip().lower()
        return bool(name) and not name.startswith(virtual_prefixes)

    def natural_key(interface: InterfaceInfo) -> tuple:
        return tuple(int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", interface.name or ""))

    candidates = sorted((interface for interface in interfaces if is_physical(interface)), key=natural_key)
    unused = {interface.if_index for interface in candidates}
    mapped: list[tuple[PoeInfo, InterfaceInfo | None]] = []

    for poe in poe_entries:
        # [KOS20260920] pethPsePortIndex는 ifIndex가 아니다. 먼저 포트명의 마지막
        # 숫자(Gi1/0/7 -> 7)로 대응하고, 불가능하면 동일 ifIndex와 표시 순서를 사용한다.
        name_matches = [
            interface
            for interface in candidates
            if interface.if_index in unused
            and (numbers := re.findall(r"\d+", interface.name or ""))
            and int(numbers[-1]) == poe.if_index
        ]
        interface = name_matches[0] if name_matches else None
        if interface is None:
            interface = next(
                (candidate for candidate in candidates if candidate.if_index in unused and candidate.if_index == poe.if_index),
                None,
            )
        if interface is None:
            interface = next((candidate for candidate in candidates if candidate.if_index in unused), None)
        if interface is not None:
            unused.remove(interface.if_index)
        mapped.append((poe, interface))

    return mapped


class DiscoveryEngine:
    def __init__(
        self,
        session_factory,
        community: str = config.DEFAULT_SNMP_COMMUNITY,
        profile: str = "STANDARD",
        max_depth: int | None = None,
        max_hosts: int | None = None,
        allowed_cidrs: list[str] | None = None,
        concurrency: int | None = None,
        hostname_rules: list[str] | None = None,
    ):
        from app.discovery.profiles import get_profile_scope

        self.session_factory = session_factory
        self.community = community
        self.profile = profile
        self.scope = get_profile_scope(profile)
        # [KOS20260920] `max_depth or config.MAX_DISCOVERY_DEPTH`는 0을 falsy로 취급해
        # "Seed만 처리하고 확장하지 않는다"는 의도적인 0 값을 무시하고 기본값(12)으로
        # 되돌리는 버그였다 (max_depth=0을 명시한 테스트에서 실제로 재현됨).
        self.max_depth = max_depth if max_depth is not None else config.MAX_DISCOVERY_DEPTH
        self.max_hosts = max_hosts or config.MAX_SEED_HOSTS
        self.allowed_networks = (
            [ipaddress.ip_network(n, strict=False) for n in allowed_cidrs] if allowed_cidrs else None
        )
        # [KOS20260921] 동시에 처리할 호스트 수. Worker마다 독립된 Session(=SQLite
        # 연결)을 쓰므로 늘려도 안전하지만, 과도하면 SNMP/ICMP 부하와 WAL 쓰기
        # 경합이 늘어나므로 상한을 둔다 (run()의 Worker 구조 설명 참고).
        _concurrency = concurrency if concurrency is not None else config.DEFAULT_DISCOVERY_CONCURRENCY
        self.concurrency = max(1, min(_concurrency, config.MAX_DISCOVERY_CONCURRENCY))
        # SNMP sysName이 없을 때(주로 SNMP 미응답 PC) 시도할 hostname 식별 보조 규칙.
        self.hostname_rules = hostname_rules or []
        self._credential_profile_id: int | None = None

    def _in_scope(self, ip: str) -> bool:
        if not self.allowed_networks:
            return True
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(addr in net for net in self.allowed_networks)

    async def run(self, run_id: int, seed_targets: list[str]) -> None:
        session: Session = self.session_factory()
        try:
            # 18.1절: SNMP community는 평문으로 device row에 남기지 않고, 이번 실행 전체가
            # 공유하는 CredentialProfile(암호화 저장) 하나를 만들어 참조 ID만 부여한다.
            credential_profile = get_or_create_snmp_credential_profile(session, self.community)
            session.commit()
            self._credential_profile_id = credential_profile.id
        except Exception:
            session.rollback()
            run = session.get(DiscoveryRun, run_id)
            if run is not None:
                run.status = "FAILED"
                run.current_ip = None
                run.current_ips = None
                run.ended_at = utcnow()
                run.error_count += 1
                session.commit()
            logger.exception("Discovery run %s 실패(초기화)", run_id)
            session.close()
            return
        session.close()

        errors, error_count = await self._run_workers(run_id, seed_targets)

        session = self.session_factory()
        try:
            run = session.get(DiscoveryRun, run_id)
            run.error_count += error_count
            run.status = "COMPLETED"
            run.current_ip = None
            run.current_ips = None
            run.ended_at = utcnow()
            run.errors = json.dumps(errors[-200:], ensure_ascii=False)
            session.commit()

            recompute_topology_and_roles(session)
        except Exception:
            session.rollback()
            run = session.get(DiscoveryRun, run_id)
            if run is not None:
                run.status = "FAILED"
                run.current_ip = None
                run.current_ips = None
                run.ended_at = utcnow()
                run.error_count += 1
                session.commit()
            logger.exception("Discovery run %s 실패", run_id)
        finally:
            session.close()

    async def _run_workers(self, run_id: int, seed_targets: list[str]) -> tuple[list[dict], int]:
        """[KOS20260921] 순차 처리(호스트 1개씩)는 응답 없는 호스트가 많은 넓은
        대역에서 총 소요시간이 호스트 수 x 개별 Timeout에 비례해 늘어난다.
        asyncio.Queue + Worker Pool로 self.concurrency개까지 동시에 처리하되,
        Worker마다 독립된 Session(=SQLite 연결)을 사용해 SQLAlchemy Session을
        여러 코루틴이 동시에 건드리는 문제를 피한다. 짧은 WAL 쓰기 잠금 경합은
        PRAGMA busy_timeout(db.py)이 재시도로 흡수한다. asyncio.Queue의
        task_done()/join()으로, 재귀적으로 늘어나는 BFS 큐가 완전히 소진될
        때까지 대기하는 표준 패턴을 사용한다.
        """
        queue: asyncio.Queue[tuple[str, int]] = asyncio.Queue()
        for ip in dict.fromkeys(seed_targets):
            queue.put_nowait((ip, 0))

        visited_ips: set[str] = set()
        in_flight: set[str] = set()
        errors: list[dict] = []
        error_count_holder = {"count": 0}
        state_lock = asyncio.Lock()
        state = {"stop": False}

        def _sync_in_flight(run_row: DiscoveryRun) -> None:
            """[KOS20260921] concurrency>1이면 여러 IP가 동시에 처리 중인데
            current_ip 하나만으로는 "마지막으로 시작한 IP"밖에 못 보여줬다.
            호출 시점의 in_flight 전체를 JSON으로 반영한다. state_lock을 이미
            들고 있는 지점에서만 호출해야 한다(asyncio.Lock은 재진입 불가)."""
            run_row.current_ips = json.dumps(sorted(in_flight), ensure_ascii=False)
            run_row.current_ip = next(iter(sorted(in_flight)), None)

        def _safe_commit(worker_session: Session, ip: str) -> bool:
            """[KOS20260921] concurrency가 높을 때 다른 Worker의 미완료 트랜잭션과
            겹치면 SQLite busy_timeout(15초) 만료 후 OperationalError가 날 수 있다.
            session.commit()은 동기/블로킹 호출이라 실패해도 asyncio 예외 전파에
            맡기면 해당 Worker 태스크가 통째로 조용히 죽는다 - 개별 호스트 오류로
            기록하고 이 호스트만 건너뛰도록 처리한다."""
            try:
                worker_session.commit()
                return True
            except Exception as exc:  # noqa: BLE001
                worker_session.rollback()
                error_count_holder["count"] += 1
                errors.append({"ip": ip, "error": f"commit 실패: {exc}"})
                return False

        async def worker() -> None:
            worker_session: Session = self.session_factory()
            try:
                while True:
                    ip, depth = await queue.get()
                    try:
                        async with state_lock:
                            if state["stop"] or ip in visited_ips or depth > self.max_depth:
                                continue
                            visited_ips.add(ip)
                            if ip in config.TOPOLOGY_EXCLUDE_IPS:
                                # [KOS20260922] CIDR 스캔 대상에 이 NMS 서버 자신의 IP가
                                # 포함되면, 자신을 스캔해 만든 장비 Row가 Topology에서
                                # 아무 데도 연결 안 된 채 홀로 뜬다(운영 환경에서 실제로
                                # 발견된 사례) - 스캔 대상에서 아예 제외한다.
                                continue
                            run_row = worker_session.get(DiscoveryRun, run_id)
                            if run_row.scanned_count >= self.max_hosts:
                                state["stop"] = True
                                continue
                            run_row.scanned_count += 1
                            # 17.5절: 현재 스캔 중인 IP를 즉시 커밋해 폴링 화면에 진행
                            # 상황이 보이게 한다. concurrency>1이면 여러 IP가 동시에
                            # in_flight에 들어가므로 current_ips에 전체 목록을 반영한다.
                            in_flight.add(ip)
                            _sync_in_flight(run_row)
                            if not _safe_commit(worker_session, ip):
                                in_flight.discard(ip)
                                continue

                        try:
                            device, is_infra = await self._process_host(worker_session, ip, depth)
                        except Exception as exc:  # noqa: BLE001 - 개별 호스트 오류는 기록하고 계속 진행
                            worker_session.rollback()
                            async with state_lock:
                                error_count_holder["count"] += 1
                                errors.append({"ip": ip, "error": str(exc)})
                                in_flight.discard(ip)
                                run_row = worker_session.get(DiscoveryRun, run_id)
                                _sync_in_flight(run_row)
                                _safe_commit(worker_session, ip)
                            continue

                        if device is None:
                            async with state_lock:
                                in_flight.discard(ip)
                                run_row = worker_session.get(DiscoveryRun, run_id)
                                _sync_in_flight(run_row)
                                _safe_commit(worker_session, ip)
                            continue

                        neighbor_ips: set[str] = set()
                        if is_infra and self.scope.expand_neighbors and depth < self.max_depth:
                            neighbor_ips = self._collect_neighbor_ips(worker_session, device)

                        device_snmp_enabled = device.snmp_enabled
                        device_type = device.device_type

                        async with state_lock:
                            run_row = worker_session.get(DiscoveryRun, run_id)
                            run_row.alive_count += 1
                            if device_snmp_enabled:
                                run_row.snmp_count += 1
                            self._tally_device_type(run_row, device_type)
                            in_flight.discard(ip)
                            _sync_in_flight(run_row)
                            if not _safe_commit(worker_session, ip):
                                continue
                            if not state["stop"]:
                                for neighbor_ip in neighbor_ips:
                                    if neighbor_ip not in visited_ips and self._in_scope(neighbor_ip):
                                        queue.put_nowait((neighbor_ip, depth + 1))
                    finally:
                        queue.task_done()
            finally:
                worker_session.close()

        workers = [asyncio.create_task(worker()) for _ in range(self.concurrency)]
        await queue.join()
        for w in workers:
            w.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        return errors, error_count_holder["count"]

    async def _process_host(self, session: Session, ip: str, depth: int) -> tuple[NetworkDevice | None, bool]:
        probe = icmp.ping(ip)
        snmp = SnmpCollector(ip, community=self.community, timeout=config.SNMP_TIMEOUT_SECONDS, retries=config.SNMP_RETRIES)
        reachable = await snmp.is_reachable()

        if not reachable:
            if not probe.alive:
                return None, False
            observation = DeviceObservation(management_ip=ip, discovery_depth=depth)
            device, _created = resolve_or_create_device(session, observation)
            if device.discovery_depth == 0 or depth < device.discovery_depth:
                device.discovery_depth = depth
            # [KOS20260921] 이 뒤로 hostname 식별(DNS/NETBIOS/mDNS, 각각 최대
            # ~1초) + RTSP/ONVIF/HTTP/SMB/SSH 프로브가 이어져 총 몇 초씩 걸릴 수
            # 있다. 커밋 없이 이 구간을 지나면 그 사이 트랜잭션이 열린 채로
            # 남아 다른 Worker의 commit을 막는다(엔터프라이즈 SNMP 장비보다 이
            # 분기(SNMP 미응답 PC/엔드포인트)를 타는 호스트가 훨씬 많아 실제로는
            # 이 구간이 concurrency 관련 서버 응답불가의 주된 경로였다).
            session.commit()

            if not device.hostname and self.hostname_rules:
                resolved_hostname, _source = await resolve_hostname(ip, self.hostname_rules)
                if resolved_hostname:
                    device.hostname = resolved_hostname
                    session.commit()

            # [KOS20260921] 이전에는 SNMP 미응답 호스트가 여기서 바로 반환되어
            # 8장 분류 로직 자체를 타지 않았다. Windows/Linux/macOS PC는 보통
            # SNMP Agent를 켜두지 않으므로, 이 분기를 타는 장비가 대부분인데도
            # device_type이 항상 UNKNOWN으로 남는 문제가 있었다. SNMP 없이도
            # 얻을 수 있는 무자격 프로브만으로 최선의 분류를 시도한다.
            try:
                has_rtsp = await probe_rtsp(ip)
                onvif_ok = await probe_onvif(ip)
                http_hint = await probe_http_title_hint(ip)
                smb_rdp_ok = await probe_smb_or_rdp(ip)
                ssh_signals = classify_ssh_banner(await probe_ssh_banner(ip))
            except CollectorError:
                has_rtsp = onvif_ok = http_hint = smb_rdp_ok = False
                ssh_signals = classify_ssh_banner(None)

            evidence = ClassificationEvidence(
                has_onvif=onvif_ok,
                onvif_device_service_ok=onvif_ok,
                has_rtsp=has_rtsp,
                oui_category=classify_oui(device.primary_mac),
                http_title_hint=http_hint,
                hostname=device.hostname or "",
                has_smb_or_rdp=smb_rdp_ok,
                has_ssh_banner=ssh_signals["has_ssh_banner"],
                linux_service_fingerprint=ssh_signals["linux_distro_hint"],
                macos_fingerprint=ssh_signals["macos_hint"],
                os_ttl_hint=classify_ttl_os_hint(probe.ttl),
                has_management_response=True,
            )
            result = classify_device_type(evidence)
            device.device_type = result.device_type
            device.classification_score = result.score
            device.classification_method = result.method
            device.classification_detail = json.dumps(result.detail, ensure_ascii=False)
            session.flush()
            return device, False

        sysinfo = await snmp.get_system_info()
        interfaces = await snmp.get_interfaces() if self.scope.collect_interfaces else []
        primary_mac = min((i.mac for i in interfaces if i.mac), default=None)
        lldp_chassis_id = await snmp.get_local_chassis_id() if self.scope.collect_lldp else None

        # [KOS20260921] sysName은 관리자가 설정해 두지 않으면 비어 있는 경우가
        # 흔하다(특히 Switch가 아닌 SNMP-enabled 서버/장비). NSH/NHM 명명 규칙
        # 기반 분류가 sysName에 의존하므로, sysName이 실제로 있을 때는 절대
        # 덮어쓰지 않고 "비어 있을 때만" 사용자가 선택한 보조 규칙으로 보완한다.
        hostname = sysinfo.sys_name or None
        if not hostname and self.hostname_rules:
            hostname, _source = await resolve_hostname(ip, self.hostname_rules)

        observation = DeviceObservation(
            management_ip=ip,
            primary_mac=primary_mac,
            sys_object_id=sysinfo.sys_object_id,
            sys_descr=sysinfo.sys_descr or None,
            vendor=sysinfo.vendor,
            # [KOS20260921] 이 벤더(NST) 장비는 sysName 자체가 "NSH-2128P"처럼
            # 모델 번호를 담은 명명 규칙을 쓴다 - model을 별도로 파싱하는 대신
            # sysName을 그대로 model에도 채운다.
            model=sysinfo.sys_name or None,
            lldp_chassis_id=lldp_chassis_id,
            hostname=hostname,
            sys_location=sysinfo.sys_location or None,
            snmp_enabled=True,
            credential_profile_id=self._credential_profile_id,
            discovery_depth=depth,
        )
        device, _created = resolve_or_create_device(session, observation)
        # [KOS20260921] 이 지점부터 아래 각 collector 호출까지 여러 SNMP await가
        # 이어지는데, 그 사이에 트랜잭션을 열어 둔 채 두면(session.flush()는
        # commit하지 않으므로) 다른 Worker의 짧은 commit이 이 트랜잭션이 끝날
        # 때까지 SQLite Write Lock을 기다리게 된다 - concurrency>1에서 서버
        # 전체가 멈추는 실제 장애의 근본 원인이었다. 각 쓰기 묶음 뒤, 다음
        # await 직전에 즉시 commit해 트랜잭션 보유 시간을 최소화한다.
        session.commit()

        vlans = await snmp.get_vlans() if self.scope.collect_vlans else []
        port_pvids = await snmp.get_port_pvids() if self.scope.collect_vlans else {}

        iface_id_by_index: dict[int, int] = {}
        for info in interfaces:
            row = session.scalar(
                select(DeviceInterface).where(
                    DeviceInterface.device_id == device.id, DeviceInterface.if_index == info.if_index
                )
            )
            # [KOS20260923] "구성 변경 이력" 요청 - 신규 발견 포트는 비교할 이전
            # 값이 없어 "None → UP" 같은 가짜 변경으로 남기지 않도록 기존 행이
            # 있을 때만(is_new_row=False) 아래에서 diff를 기록한다. oper_status/
            # stp_state는 링크 플랩이나 STP 재수렴으로 정상적으로 자주 바뀌어
            # 구성 변경이라기보다 Fault 영역 이벤트에 가까우므로 제외하고,
            # admin_status(관리자가 의도적으로 올리고 내리는 값)와 vlan(PVID)만
            # 구성 변경 이력 대상으로 삼는다.
            is_new_row = row is None
            old_admin_status = None if is_new_row else row.admin_status
            old_vlan = None if is_new_row else row.vlan
            if row is None:
                row = DeviceInterface(device_id=device.id, if_index=info.if_index)
                session.add(row)
            row.name = info.name
            row.mac = info.mac
            row.admin_status = info.admin_status
            row.oper_status = info.oper_status
            row.speed_mbps = info.speed_mbps
            row.in_octets = info.in_octets
            row.out_octets = info.out_octets
            row.stp_state = info.stp_state
            row.stp_designated_bridge_mac = info.stp_designated_bridge_mac
            if info.if_index in port_pvids:
                row.vlan = port_pvids[info.if_index]
            row.last_seen_at = utcnow()
            session.flush()
            iface_id_by_index[info.if_index] = row.id
            if not is_new_row:
                record_config_change(
                    session,
                    device_id=device.id,
                    interface_id=row.id,
                    field_name="admin_status",
                    old_value=old_admin_status,
                    new_value=row.admin_status,
                    source="DISCOVERY",
                )
                record_config_change(
                    session,
                    device_id=device.id,
                    interface_id=row.id,
                    field_name="vlan",
                    old_value=None if old_vlan is None else str(old_vlan),
                    new_value=None if row.vlan is None else str(row.vlan),
                    source="DISCOVERY",
                )
        session.commit()

        has_ip_forwarding = await snmp.is_ip_forwarding()
        has_bridge = await snmp.get_bridge_capable()

        # [KOS20260922] STP는 인터페이스 정보와 함께일 때만 의미가 있어
        # collect_interfaces로 같이 묶는다(Discovery 화면의 Profile 비교 표에서
        # "STP 수집 여부"를 IF-MIB 인터페이스 행과 같은 조건으로 보여주기 위함이기도
        # 하다 - profiles.py의 PROTOCOL_LABELS 참고). STP 미지원 장비(L3 라우터 등)는
        # supported=False가 돌아오는데, 그런 경우 이전에 저장된 is_stp_root 값을
        # 섣불리 False로 덮어쓰지 않는다.
        stp_info = await snmp.get_stp_info() if self.scope.collect_interfaces else StpInfo(is_root=False, supported=False)
        if stp_info.supported:
            old_is_stp_root = device.is_stp_root
            device.is_stp_root = stp_info.is_root
            record_config_change(
                session,
                device_id=device.id,
                field_name="is_stp_root",
                old_value=str(old_is_stp_root),
                new_value=str(device.is_stp_root),
                source="DISCOVERY",
            )

        lldp_neighbors = await snmp.get_lldp_neighbors() if self.scope.collect_lldp else []
        for n in lldp_neighbors:
            local_if_id = iface_id_by_index.get(n.local_if_index) if n.local_if_index else None
            existing = session.scalar(
                select(LldpNeighbor).where(
                    LldpNeighbor.local_device_id == device.id,
                    LldpNeighbor.remote_chassis_id == n.remote_chassis_id,
                    LldpNeighbor.remote_port_id == n.remote_port_id,
                )
            )
            if existing is None:
                session.add(
                    LldpNeighbor(
                        local_device_id=device.id,
                        local_interface_id=local_if_id,
                        remote_chassis_id=n.remote_chassis_id,
                        remote_port_id=n.remote_port_id,
                        remote_sys_name=n.remote_sys_name,
                        remote_mgmt_ip=n.remote_mgmt_ip,
                        remote_capabilities=n.remote_capabilities,
                        protocol=n.protocol,
                    )
                )
            else:
                # [KOS20260921] local_interface_id를 갱신하지 않아, get_lldp_neighbors()의
                # dot1dBasePortIfIndex 매핑 수정이 이미 존재하는(재스캔 전에 만들어진)
                # LldpNeighbor 행에는 영원히 반영되지 않는 문제가 있었다 - 재스캔해도
                # Topology 링크의 포트가 계속 "?"로 남는 원인이었다.
                existing.local_interface_id = local_if_id
                existing.remote_sys_name = n.remote_sys_name
                existing.remote_mgmt_ip = n.remote_mgmt_ip
                existing.last_seen_at = utcnow()
        session.commit()

        fdb_entries = await snmp.get_fdb() if self.scope.collect_fdb else []
        for f in fdb_entries:
            if_id = iface_id_by_index.get(f.if_index) if f.if_index else None
            existing = session.scalar(
                select(MacFdb).where(MacFdb.device_id == device.id, MacFdb.mac == f.mac, MacFdb.vlan == f.vlan)
            )
            if existing is None:
                session.add(MacFdb(device_id=device.id, interface_id=if_id, vlan=f.vlan, mac=f.mac, entry_type=f.entry_type))
            else:
                existing.interface_id = if_id
                existing.last_seen_at = utcnow()
        session.commit()

        arp_entries = await snmp.get_arp_table() if self.scope.collect_arp else []
        for a in arp_entries:
            if_id = iface_id_by_index.get(a.if_index) if a.if_index else None
            existing = session.scalar(
                select(ArpEntry).where(ArpEntry.device_id == device.id, ArpEntry.ip == a.ip, ArpEntry.mac == a.mac)
            )
            if existing is None:
                session.add(ArpEntry(device_id=device.id, interface_id=if_id, ip=a.ip, mac=a.mac, state=a.state))
            else:
                existing.last_seen_at = utcnow()
        session.commit()

        routes = await snmp.get_routes() if self.scope.collect_routes else []
        ip_interface_count = await snmp.get_ip_interface_count() if self.scope.collect_routes else 0
        if self.scope.collect_routes:
            session.query(RouteEntry).filter(RouteEntry.device_id == device.id).delete()
            for r in routes:
                if_id = iface_id_by_index.get(r.if_index) if r.if_index else None
                session.add(
                    RouteEntry(
                        device_id=device.id,
                        destination=r.destination,
                        prefix_len=r.prefix_len,
                        next_hop=r.next_hop,
                        interface_id=if_id,
                        route_type=r.route_type,
                    )
                )
        session.commit()

        poe_entries = await snmp.get_poe_status() if self.scope.collect_poe else []
        if self.scope.collect_poe:
            session.query(PoePort).filter(PoePort.device_id == device.id).delete()
            session.query(DeviceInterface).filter(DeviceInterface.device_id == device.id).update(
                {DeviceInterface.poe_capable: False}, synchronize_session=False
            )
            for p, interface_info in _map_poe_entries_to_interfaces(poe_entries, interfaces):
                if interface_info is None:
                    continue
                if_id = iface_id_by_index[interface_info.if_index]
                interface_row = session.get(DeviceInterface, if_id)
                interface_row.poe_capable = True
                session.add(
                    PoePort(
                        device_id=device.id,
                        interface_id=if_id,
                        enabled=p.enabled,
                        status=p.status,
                        power_mw=p.power_mw,
                        voltage_v=p.voltage_v,
                        current_ma=p.current_ma,
                        poe_class=p.poe_class,
                    )
                )
        session.commit()

        has_rtsp = False
        onvif_ok = False
        http_hint = False
        smb_rdp_ok = False
        ssh_signals = classify_ssh_banner(None)
        if not has_bridge:
            # Switch/Router로 이미 확정될 강한 근거(Bridge-MIB)가 없을 때만 Endpoint용
            # Application 프로브를 수행해 불필요한 지연을 줄인다.
            try:
                has_rtsp = await probe_rtsp(ip)
                onvif_ok = await probe_onvif(ip)
                http_hint = await probe_http_title_hint(ip)
                smb_rdp_ok = await probe_smb_or_rdp(ip)
                ssh_signals = classify_ssh_banner(await probe_ssh_banner(ip))
            except CollectorError:
                pass

        sys_descr_lower = sysinfo.sys_descr.lower()
        # [KOS20260922] "STANDARD로 할 때와 분류가 완전 달라진다" 리포트를 조사하다
        # 발견한 버그: route_entry의 if_index 다양성으로 SVI 여러 개(=L3 스위치)를
        # 추정하려 했는데, 루프백 인터페이스(destination이 127.0.0.0/8인 DIRECT
        # 라우트)가 항상 별도 if_index를 차지해서 "루프백 1개 + 관리 VLAN
        # 인터페이스 1개"만 있는 순수 L2 스위치도 항상 unique if_index 2개로
        # 잡혔다(실사용 데이터로 NSH-2128/2128PT/2128P 3대 모두 재현 확인 -
        # STANDARD는 Route를 수집하지 않아 이 버그를 안 타다가, DETAILED로 재스캔
        # 하니 전부 DISTRIBUTION_SWITCH로 잘못 승격됨). 루프백 라우트를 먼저
        # 제외하고 남은 라우트의 if_index 다양성만 본다.
        non_loopback_routes = [r for r in routes if not r.destination.startswith("127.")]
        # ipRouteTable은 deprecated라 직접연결 SVI만 있는 L3 스위치에서 비어
        # 있는 경우가 많다. ipAddrTable의 IP-인터페이스 개수(관리 IP 1개를
        # 제외한 나머지)를 함께 보아 SVI 존재를 더 안정적으로 잡아낸다.
        svi_from_ip_addr_table = max(ip_interface_count - 1, 0)
        svi_or_l3_if_count = max(
            len({r.if_index for r in non_loopback_routes if r.if_index is not None}), svi_from_ip_addr_table
        )
        evidence = ClassificationEvidence(
            has_bridge_mib=has_bridge,
            has_fdb=len(fdb_entries) > 0,
            has_lldp=len(lldp_neighbors) > 0,
            physical_port_count=len(interfaces),
            has_ip_forwarding=has_ip_forwarding,
            route_table_size=len(routes),
            svi_or_l3_if_count=svi_or_l3_if_count,
            vlan_count=len(vlans),
            has_wan_or_default_route=any(r.destination == "0.0.0.0" and r.prefix_len == 0 for r in routes),
            bridge_capability_weak=not has_bridge,
            has_poe_mib=len(poe_entries) > 0 if self.scope.collect_poe else device.poe_capable,
            hostname=device.hostname or "",
            has_onvif=onvif_ok,
            onvif_device_service_ok=onvif_ok,
            has_rtsp=has_rtsp,
            oui_category=classify_oui(primary_mac),
            http_title_hint=http_hint,
            sys_descr=sysinfo.sys_descr,
            windows_sysdescr="windows" in sys_descr_lower,
            linux_sysdescr="linux" in sys_descr_lower,
            macos_sysdescr=("darwin" in sys_descr_lower or "mac os" in sys_descr_lower),
            has_smb_or_rdp=smb_rdp_ok,
            has_ssh_banner=ssh_signals["has_ssh_banner"],
            linux_service_fingerprint=ssh_signals["linux_distro_hint"],
            macos_fingerprint=ssh_signals["macos_hint"],
            os_ttl_hint=classify_ttl_os_hint(probe.ttl),
            has_management_response=True,
        )
        result = classify_device_type(evidence)
        device.device_type = result.device_type
        device.classification_score = result.score
        device.classification_method = result.method
        device.classification_detail = json.dumps(result.detail, ensure_ascii=False)
        device.layer2_capable = has_bridge
        # [KOS20260921] ipForwarding OID 하나만으로는 SVI만 있고 전역 라우팅
        # 스위치가 꺼져 있는 L3 스위치를 놓친다. SVI/Route 근거나 명명 규칙
        # 기반 확정 결과(L3_SWITCH/L3_POE_SWITCH)도 함께 반영한다.
        device.layer3_capable = (
            has_ip_forwarding
            or svi_or_l3_if_count >= 2
            or result.device_type in ("L3_SWITCH", "L3_POE_SWITCH", "ROUTER")
        )
        if self.scope.collect_poe:
            # [KOS20260921] poe_entries(SNMP 원시 근거)가 아니라 classify_device_type()이
            # 명명 규칙까지 반영해 확정한 최종 device_type을 기준으로 저장해야, PoE 미지원
            # 모델(NST 사설 MIB 오탐)이 poe_capable=True로 남는 불일치가 없다.
            device.poe_capable = result.device_type in ("L2_POE_SWITCH", "L3_POE_SWITCH")
            if not device.poe_capable:
                # [KOS20260921] 위 poe_entries 저장 블록은 명명 규칙 확정 전(원시 SNMP 근거)
                # 기준으로 DeviceInterface.poe_capable=True와 PoePort 행을 먼저 만들어 둔다.
                # 최종적으로 PoE 미지원 모델로 확정되면(명명 규칙 오탐 등) 그 임시 표시를
                # 되돌려야, Devices 상세 Interfaces 탭/PoE 탭에 PoE On/Off 제어가 잘못
                # 노출되지 않는다.
                session.query(DeviceInterface).filter(DeviceInterface.device_id == device.id).update(
                    {DeviceInterface.poe_capable: False}, synchronize_session=False
                )
                session.query(PoePort).filter(PoePort.device_id == device.id).delete()
        if self.scope.collect_vlans:
            # STANDARD 등 VLAN을 수집하지 않는 Profile 재탐색 시 이전 DETAILED
            # 결과를 0으로 덮어쓰지 않도록, 실제로 수집한 경우에만 갱신한다.
            device.vlan_count = len(vlans)

        is_infra = device.device_type in INFRASTRUCTURE_TYPES
        # autoflush=False 세션이므로, run()의 _collect_neighbor_ips가 방금 추가한
        # LldpNeighbor/ArpEntry 행을 조회할 수 있도록 명시적으로 flush한다.
        session.flush()
        return device, is_infra

    def _collect_neighbor_ips(self, session: Session, device: NetworkDevice) -> set[str]:
        ips: set[str] = set()
        for n in session.scalars(select(LldpNeighbor).where(LldpNeighbor.local_device_id == device.id)):
            if n.remote_mgmt_ip:
                ips.add(n.remote_mgmt_ip)
        for a in session.scalars(select(ArpEntry).where(ArpEntry.device_id == device.id)):
            ips.add(a.ip)
        return ips

    def _tally_device_type(self, run: DiscoveryRun, device_type: str) -> None:
        if device_type in SWITCH_TYPES or device_type == "ROUTER":
            run.switch_count += 1
        elif device_type == "IP_CAMERA":
            run.camera_count += 1
        elif device_type in ("WINDOWS_PC", "LINUX_PC", "MAC_PC"):
            run.pc_count += 1
        elif device_type == "UNKNOWN":
            run.unknown_count += 1
