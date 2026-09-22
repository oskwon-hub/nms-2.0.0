"""자동 Discovery의 설정 재사용, Seed 생성, 중복 실행 방지 검증."""
from __future__ import annotations

import json
import asyncio

import pytest
from sqlalchemy import select

from app.discovery.scheduler import run_auto_discovery_once
from app.identity import DeviceObservation, resolve_or_create_device
from app.models import DiscoveryRun


@pytest.mark.asyncio
async def test_auto_discovery_reuses_latest_settings_and_inventory_seeds(session_factory):
    with session_factory() as session:
        first, _ = resolve_or_create_device(session, DeviceObservation(management_ip="192.0.2.10"))
        second, _ = resolve_or_create_device(session, DeviceObservation(management_ip="192.0.2.20"))
        session.add(
            DiscoveryRun(
                profile="DETAILED",
                status="COMPLETED",
                concurrency=3,
                hostname_rules=json.dumps(["DNS"]),
            )
        )
        session.commit()

    calls = []

    class FakeEngine:
        def __init__(self, **kwargs):
            calls.append(("init", kwargs))

        async def run(self, run_id, seeds):
            calls.append(("run", run_id, seeds))
            with session_factory() as session:
                run = session.get(DiscoveryRun, run_id)
                run.status = "COMPLETED"
                session.commit()

    run_id = await run_auto_discovery_once(session_factory, FakeEngine)

    assert run_id is not None
    assert calls[0][1]["profile"] == "DETAILED"
    assert calls[0][1]["concurrency"] == 3
    assert calls[0][1]["hostname_rules"] == ["DNS"]
    assert calls[1][2] == ["192.0.2.10", "192.0.2.20"]


@pytest.mark.asyncio
async def test_auto_discovery_skips_while_another_run_is_running(session_factory):
    with session_factory() as session:
        resolve_or_create_device(session, DeviceObservation(management_ip="192.0.2.10"))
        session.add(DiscoveryRun(profile="STANDARD", status="RUNNING", concurrency=1))
        session.commit()

    assert await run_auto_discovery_once(session_factory) is None
    with session_factory() as session:
        assert len(session.scalars(select(DiscoveryRun)).all()) == 1


@pytest.mark.asyncio
async def test_auto_discovery_marks_cancelled_on_server_shutdown(session_factory):
    with session_factory() as session:
        resolve_or_create_device(session, DeviceObservation(management_ip="192.0.2.10"))
        session.commit()

    class CancelledEngine:
        def __init__(self, **kwargs):
            pass

        async def run(self, run_id, seeds):
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await run_auto_discovery_once(session_factory, CancelledEngine)

    with session_factory() as session:
        run = session.scalar(select(DiscoveryRun))
        assert run.status == "CANCELLED"
        assert run.current_ip is None and run.current_ips is None
        assert "재시작" in run.errors
