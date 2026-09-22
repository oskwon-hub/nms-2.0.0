"""[KOS20260921] /api/alarms/dismiss, /api/alarms/dismiss-all 통합 테스트.

Alarms는 파생 데이터라 dismiss가 실제로 목록에서 걸러지는지는 HTTP 계층까지
왕복해 봐야 확인할 수 있으므로(list_alarms() 순수 단위 테스트는 test_alarms.py),
FastAPI TestClient로 검증한다.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def client(monkeypatch, tmp_path):
    import app.api.routes_discovery as routes_discovery_mod
    from app import config, db

    monkeypatch.setattr(config, "AUTO_DISCOVERY_ENABLED", False)

    db_path = tmp_path / "nms_test.db"
    test_engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False}, future=True)
    db.configure_sqlite_pragmas(test_engine)

    TestSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False, future=True)
    monkeypatch.setattr(db, "engine", test_engine)
    monkeypatch.setattr(db, "SessionLocal", TestSessionLocal)
    monkeypatch.setattr(routes_discovery_mod, "SessionLocal", TestSessionLocal)

    db.init_db()

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def _make_offline_device(session_local):
    from app.identity import DeviceObservation, resolve_or_create_device

    session = session_local()
    device, _ = resolve_or_create_device(session, DeviceObservation(management_ip="10.0.0.1"))
    device.status = "OFFLINE"
    session.commit()
    device_id = device.id
    session.close()
    return device_id


def test_dismiss_hides_alarm_until_it_recurs(client):
    from app.db import SessionLocal

    _make_offline_device(SessionLocal)

    alarms = client.get("/api/alarms").json()
    assert len(alarms) == 1
    target = alarms[0]

    dismiss = client.post("/api/alarms/dismiss", json={"alarms": [{"id": target["id"], "occurred_at": target["occurred_at"]}]})
    assert dismiss.status_code == 200
    assert dismiss.json()["dismissed_count"] == 1

    assert client.get("/api/alarms").json() == []
    # include_dismissed=true로 조회하면 여전히 존재를 확인할 수 있다.
    still_there = client.get("/api/alarms", params={"include_dismissed": True}).json()
    assert len(still_there) == 1


def test_dismissing_same_alarm_twice_is_idempotent(client):
    from app.db import SessionLocal

    _make_offline_device(SessionLocal)
    target = client.get("/api/alarms").json()[0]
    entry = {"id": target["id"], "occurred_at": target["occurred_at"]}

    first = client.post("/api/alarms/dismiss", json={"alarms": [entry]})
    second = client.post("/api/alarms/dismiss", json={"alarms": [entry]})
    assert first.json()["dismissed_count"] == 1
    assert second.json()["dismissed_count"] == 0


def test_dismiss_all_clears_current_list(client):
    from app.db import SessionLocal
    from app.models import NetworkLink, utcnow

    _make_offline_device(SessionLocal)
    session = SessionLocal()
    session.add(NetworkLink(src_device_id=1, dst_device_id=1, source="MANUAL", confidence=100, status="DOWN", last_seen_at=utcnow()))
    session.commit()
    session.close()

    assert len(client.get("/api/alarms").json()) == 2

    response = client.post("/api/alarms/dismiss-all")
    assert response.status_code == 200
    assert response.json()["dismissed_count"] == 2
    assert client.get("/api/alarms").json() == []
