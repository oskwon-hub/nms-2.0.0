"""16장: POST /api/discovery/start, GET /api/discovery, GET /api/discovery/{run_id}.

6.1절 Seed Discovery: seed_targets/cidrs를 명시하지 않으면 서버의 로컬 ARP/Neighbor
캐시를 기본 Seed로 사용한다. cidrs 지정 시 NMS_MAX_SEED_HOSTS 상한을 넘지 않는지
먼저 검증해 과도한 Scan을 막는다(6.1절 bullet).

17.5절 Discovery 전용 화면은 실행 단위(Discovery Run)의 이력 조회가 전제이므로,
discovery_run 테이블에 이미 쌓이는 이력을 목록으로 노출하는 GET /api/discovery와,
CIDR을 직접 몰라도 Seed를 잡을 수 있도록 로컬 NIC 서브넷을 제안하는
GET /api/discovery/seed-suggestions를 16장 표에 추가로 제공한다.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.collectors.arp import local_active_subnets, local_neighbor_table
from app.collectors.hostname_resolve import HOSTNAME_RULES
from app.db import SessionLocal, db_session_dependency
from app.discovery.engine import DiscoveryEngine
from app.discovery.profiles import PROFILE_SCOPES, PROFILES, PROTOCOL_LABELS
from app.models import DiscoveryRun
from app.schemas import BulkDeleteIn, BulkDeleteOut, DiscoveryRunOut, DiscoveryStartIn, ProfileScopeOut
from app.topology.engine import clear_all_devices, clear_all_links

router = APIRouter(tags=["discovery"])
logger = logging.getLogger("nms.discovery.api")

_background_tasks: set[asyncio.Task] = set()


def _build_seed_targets(payload: DiscoveryStartIn) -> list[str]:
    if payload.seed_targets:
        return list(dict.fromkeys(payload.seed_targets))

    if payload.cidrs:
        targets: list[str] = []
        for cidr in payload.cidrs:
            try:
                network = ipaddress.ip_network(cidr, strict=False)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"잘못된 CIDR: {cidr}") from exc
            if network.num_addresses > config.MAX_SEED_HOSTS:
                raise HTTPException(
                    status_code=400,
                    detail=f"{cidr} 대역({network.num_addresses}개)이 NMS_MAX_SEED_HOSTS({config.MAX_SEED_HOSTS})를 초과합니다.",
                )
            targets.extend(str(host) for host in network.hosts())
        return list(dict.fromkeys(targets))

    # 6.1절: ARP/Neighbor cache를 사용해 최초 후보를 만든다.
    return list(dict.fromkeys(entry.ip for entry in local_neighbor_table()))


@router.post("/discovery/start", response_model=DiscoveryRunOut)
async def start_discovery(payload: DiscoveryStartIn, session: Session = Depends(db_session_dependency)):
    if payload.profile not in PROFILES:
        raise HTTPException(status_code=400, detail=f"알 수 없는 Profile: {payload.profile}")

    # 동시에 여러 Discovery Run을 실행하지 않는다: 실사용 관점에서도 대상 네트워크에
    # 중복 부하를 주지 않기 위함이지만, 더 근본적으로는 각 Run이 자신의 SQLAlchemy
    # 세션으로 network I/O(await) 중간에 쓰기 트랜잭션을 연 채 대기하는 구조라 여러
    # Run이 겹치면 단일 asyncio 이벤트 루프 위에서 SQLite 쓰기 잠금이 서로를 막아
    # "database is locked"로 실패할 수 있음을 실제로 확인했다(전체 재작성 대신
    # 단일 실행 제약으로 근본 원인을 회피).
    already_running = session.scalar(select(DiscoveryRun).where(DiscoveryRun.status == "RUNNING"))
    if already_running is not None:
        raise HTTPException(
            status_code=409,
            detail=f"이미 실행 중인 Discovery Run(#{already_running.id})이 있습니다. 완료 후 다시 시도하세요.",
        )

    seed_targets = _build_seed_targets(payload)
    if not seed_targets:
        raise HTTPException(
            status_code=400,
            detail="Seed 대상이 없습니다. seed_targets 또는 cidrs를 지정하거나 로컬 ARP 캐시를 확인하세요.",
        )

    concurrency = payload.concurrency if payload.concurrency is not None else config.DEFAULT_DISCOVERY_CONCURRENCY
    if not (1 <= concurrency <= config.MAX_DISCOVERY_CONCURRENCY):
        raise HTTPException(
            status_code=400,
            detail=f"concurrency는 1~{config.MAX_DISCOVERY_CONCURRENCY} 범위여야 합니다.",
        )
    hostname_rules = [r for r in (payload.hostname_rules or []) if r in HOSTNAME_RULES]

    if payload.reset_devices:
        # [KOS20260922] 장비 삭제는 FK CASCADE로 링크까지 함께 지우므로
        # reset_links를 별도로 또 처리할 필요가 없다.
        removed_devices = clear_all_devices(session)
        session.commit()
        logger.info(
            "Discovery 시작 전 기존 장비 %d개 삭제(reset_devices=True) - 연관 링크/인터페이스/ARP/FDB도 함께 삭제됨",
            removed_devices,
        )
    elif payload.reset_links:
        removed = clear_all_links(session)
        session.commit()
        logger.info("Discovery 시작 전 기존 링크 %d개 삭제(reset_links=True)", removed)

    run = DiscoveryRun(
        profile=payload.profile,
        cidr=",".join(payload.cidrs) if payload.cidrs else None,
        status="RUNNING",
        concurrency=concurrency,
        hostname_rules=json.dumps(hostname_rules) if hostname_rules else None,
    )
    session.add(run)
    session.commit()
    session.refresh(run)

    engine = DiscoveryEngine(
        session_factory=SessionLocal,
        community=payload.community or config.DEFAULT_SNMP_COMMUNITY,
        profile=payload.profile,
        allowed_cidrs=payload.cidrs,
        concurrency=concurrency,
        hostname_rules=hostname_rules,
    )
    task = asyncio.create_task(engine.run(run.id, seed_targets))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return run


@router.get("/discovery", response_model=list[DiscoveryRunOut])
def list_discovery_runs(
    limit: int = Query(default=20, ge=1, le=1000),
    session: Session = Depends(db_session_dependency),
):
    """17.5절: Discovery Run 이력 목록 (최근 실행 순)."""
    stmt = select(DiscoveryRun).order_by(DiscoveryRun.started_at.desc()).limit(limit)
    return session.scalars(stmt).all()


@router.get("/discovery/seed-suggestions")
def get_seed_suggestions():
    """6.1절: '자동 계산한 Local Subnet'을 Seed 후보로 제안해 CIDR을 직접 몰라도
    Discovery를 시작할 수 있게 한다."""
    return {"subnets": local_active_subnets()}


@router.get("/discovery/profiles", response_model=list[ProfileScopeOut])
def get_profile_scopes():
    """6.4절: Profile별로 실제 수집되는 프로토콜/MIB을 그대로 노출한다.

    Discovery 시작 화면에서 "이 Profile을 고르면 무엇이 수집되는지" 사용자가
    직접 확인할 수 있어야 한다는 요청에 따라, PROFILE_SCOPES를 그대로 직렬화한다.
    """
    return [
        ProfileScopeOut(
            profile=profile,
            expand_neighbors=scope.expand_neighbors,
            protocols=[
                {"key": key, "label": label, "enabled": getattr(scope, key)} for key, label in PROTOCOL_LABELS
            ],
        )
        for profile, scope in PROFILE_SCOPES.items()
    ]


def _delete_discovery_runs(session: Session, runs: list[DiscoveryRun]) -> BulkDeleteOut:
    """RUNNING인 Run은 삭제하면 백그라운드 태스크가 존재하지 않는 Row를 계속
    갱신하려 해 예외가 반복되므로, 실행 중인 Run은 건너뛴다(먼저 완료/중단 필요)."""
    deleted, skipped = [], []
    for run in runs:
        if run.status == "RUNNING":
            skipped.append(run.id)
            continue
        deleted.append(run.id)
        session.delete(run)
    session.commit()
    return BulkDeleteOut(deleted=deleted, skipped=skipped)


@router.post("/discovery/bulk-delete", response_model=BulkDeleteOut)
def bulk_delete_discovery_runs(payload: BulkDeleteIn, session: Session = Depends(db_session_dependency)):
    runs = session.scalars(select(DiscoveryRun).where(DiscoveryRun.id.in_(payload.ids))).all()
    return _delete_discovery_runs(session, list(runs))


@router.post("/discovery/delete-all", response_model=BulkDeleteOut)
def delete_all_discovery_runs(session: Session = Depends(db_session_dependency)):
    runs = session.scalars(select(DiscoveryRun)).all()
    return _delete_discovery_runs(session, list(runs))


@router.delete("/discovery/{run_id}", status_code=204)
def delete_discovery_run(run_id: int, session: Session = Depends(db_session_dependency)):
    run = session.get(DiscoveryRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Discovery Run을 찾을 수 없습니다: {run_id}")
    if run.status == "RUNNING":
        raise HTTPException(status_code=409, detail="실행 중인 Discovery Run은 삭제할 수 없습니다.")
    session.delete(run)
    session.commit()


@router.get("/discovery/{run_id}", response_model=DiscoveryRunOut)
def get_discovery_run(run_id: int, session: Session = Depends(db_session_dependency)):
    run = session.get(DiscoveryRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Discovery Run을 찾을 수 없습니다: {run_id}")
    return run
