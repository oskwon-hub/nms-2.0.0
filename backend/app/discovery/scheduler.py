"""현재 Inventory를 주기적으로 갱신하는 자동 Discovery 스케줄러."""
from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import logging
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config, db
from app.collectors.arp import local_neighbor_table
from app.credentials import resolve_snmp_community
from app.discovery.engine import DiscoveryEngine
from app.discovery.profiles import PROFILES
from app.models import DiscoveryRun, NetworkDevice, utcnow

logger = logging.getLogger("nms.discovery.scheduler")


def _latest_settings(session: Session) -> tuple[str, int, list[str], list[str] | None]:
    latest = session.scalar(
        select(DiscoveryRun)
        .where(DiscoveryRun.status == "COMPLETED")
        .order_by(DiscoveryRun.started_at.desc())
        .limit(1)
    )
    profile = latest.profile if latest is not None and latest.profile in PROFILES else "STANDARD"
    concurrency = latest.concurrency if latest is not None else config.DEFAULT_DISCOVERY_CONCURRENCY
    concurrency = max(1, min(concurrency, config.MAX_DISCOVERY_CONCURRENCY))
    try:
        hostname_rules = json.loads(latest.hostname_rules) if latest is not None and latest.hostname_rules else []
    except (TypeError, ValueError):
        hostname_rules = []
    cidrs = [value for value in (latest.cidr.split(",") if latest is not None and latest.cidr else []) if value]
    return profile, concurrency, hostname_rules, cidrs or None


def _inventory_seeds(session: Session) -> list[str]:
    seeds: set[str] = set()
    for value in session.scalars(select(NetworkDevice.management_ip).where(NetworkDevice.management_ip.isnot(None))):
        try:
            seeds.add(str(ipaddress.ip_address(value)))
        except ValueError:
            continue
    if not seeds:
        seeds.update(entry.ip for entry in local_neighbor_table())
    return sorted(seeds, key=ipaddress.ip_address)[: config.MAX_SEED_HOSTS]


def _discovery_community(session: Session) -> str:
    # 최근 응답한 SNMP 장비의 암호화 Credential을 우선 재사용한다. 연결된
    # Credential이 없으면 기존 전역 기본값을 사용한다.
    device = session.scalar(
        select(NetworkDevice)
        .where(NetworkDevice.credential_profile_id.isnot(None))
        .order_by(NetworkDevice.last_seen_at.desc())
        .limit(1)
    )
    return resolve_snmp_community(session, device) if device is not None else config.DEFAULT_SNMP_COMMUNITY


async def run_auto_discovery_once(
    session_factory: Callable[[], Session] | None = None,
    engine_class=DiscoveryEngine,
) -> int | None:
    """자동 Run 하나를 만들고 완료까지 기다린다. 다른 Run이 실행 중이면 건너뛴다."""
    session_factory = session_factory or db.SessionLocal
    with session_factory() as session:
        running = session.scalar(select(DiscoveryRun).where(DiscoveryRun.status == "RUNNING"))
        if running is not None:
            logger.info("자동 Discovery 생략: Run #%s 실행 중", running.id)
            return None

        seeds = _inventory_seeds(session)
        if not seeds:
            logger.warning("자동 Discovery 생략: Inventory 및 로컬 Neighbor에 Seed 대상이 없음")
            return None

        profile, concurrency, hostname_rules, cidrs = _latest_settings(session)
        community = _discovery_community(session)
        run = DiscoveryRun(
            profile=profile,
            cidr=",".join(cidrs) if cidrs else None,
            status="RUNNING",
            concurrency=concurrency,
            hostname_rules=json.dumps(hostname_rules) if hostname_rules else None,
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = run.id

    logger.info(
        "자동 Discovery Run #%s 시작: seed=%s profile=%s concurrency=%s",
        run_id, len(seeds), profile, concurrency,
    )
    engine = engine_class(
        session_factory=session_factory,
        community=community,
        profile=profile,
        allowed_cidrs=cidrs,
        concurrency=concurrency,
        hostname_rules=hostname_rules,
    )
    try:
        await engine.run(run_id, seeds)
    except asyncio.CancelledError:
        # 정상적인 서버 종료/reload가 실행 실패로 오인되지 않게 구분한다.
        with session_factory() as session:
            run = session.get(DiscoveryRun, run_id)
            if run is not None and run.status == "RUNNING":
                run.status = "CANCELLED"
                run.current_ip = None
                run.current_ips = None
                run.ended_at = utcnow()
                run.errors = json.dumps([{"message": "서버 종료 또는 재시작으로 자동 Discovery가 중단되었습니다."}], ensure_ascii=False)
                session.commit()
        raise
    logger.info("자동 Discovery Run #%s 종료", run_id)
    return run_id


class AutoDiscoveryScheduler:
    def __init__(self, interval_seconds: int, initial_delay_seconds: int = 0):
        self.interval_seconds = max(60, interval_seconds)
        self.initial_delay_seconds = max(0, initial_delay_seconds)
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="nms-auto-discovery")
            logger.info(
                "자동 Discovery 스케줄러 시작: 최초 %s초 후, 주기 %s초",
                self.initial_delay_seconds,
                self.interval_seconds,
            )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        logger.info("자동 Discovery 스케줄러 종료")

    async def _run(self) -> None:
        await asyncio.sleep(self.initial_delay_seconds)
        while True:
            try:
                await run_auto_discovery_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("자동 Discovery 실행 실패")
            # 실행 시간이 주기보다 길어도 완료 직후 다음 Run을 연속 시작하지 않는다.
            # 장비와 SQLite에 휴지 시간을 보장하기 위해 완료 시점부터 다음 주기를 센다.
            await asyncio.sleep(self.interval_seconds)
