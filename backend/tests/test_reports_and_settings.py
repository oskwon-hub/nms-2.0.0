"""Reports(Audit Log 조회)/Settings(Credential Profile 관리, 시스템 정보) 테스트."""
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


def test_control_logs_empty_initially(client):
    response = client.get("/api/control-logs")
    assert response.status_code == 200
    assert response.json() == []


def test_control_logs_returns_recorded_actions(client):
    from app.db import SessionLocal
    from app.identity import DeviceObservation, resolve_or_create_device
    from app.models import DeviceControlLog

    session = SessionLocal()
    device, _ = resolve_or_create_device(session, DeviceObservation(management_ip="10.0.0.1"))
    session.flush()
    session.add(
        DeviceControlLog(
            device_id=device.id,
            action="PORT_DISABLE",
            before_value="UP",
            requested_value="DOWN",
            after_value="DOWN",
            result="SUCCESS",
            performed_by="tester",
        )
    )
    session.commit()
    session.close()

    response = client.get("/api/control-logs")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["action"] == "PORT_DISABLE"


def _add_control_log(client, action="PORT_DISABLE"):
    from app.db import SessionLocal
    from app.identity import DeviceObservation, resolve_or_create_device
    from app.models import DeviceControlLog

    session = SessionLocal()
    device, _ = resolve_or_create_device(session, DeviceObservation(management_ip="10.0.0.1"))
    session.flush()
    log = DeviceControlLog(
        device_id=device.id,
        action=action,
        before_value="UP",
        requested_value="DOWN",
        after_value="DOWN",
        result="SUCCESS",
        performed_by="tester",
    )
    session.add(log)
    session.commit()
    log_id = log.id
    session.close()
    return log_id


def test_control_logs_bulk_delete_removes_selected(client):
    id1 = _add_control_log(client)
    id2 = _add_control_log(client)

    response = client.post("/api/control-logs/bulk-delete", json={"ids": [id1]})
    assert response.status_code == 200
    assert response.json() == {"deleted": [id1], "skipped": []}

    remaining_ids = {row["id"] for row in client.get("/api/control-logs").json()}
    assert remaining_ids == {id2}


def test_control_logs_delete_all_empties_the_list(client):
    _add_control_log(client)
    _add_control_log(client)

    response = client.post("/api/control-logs/delete-all")
    assert response.status_code == 200
    assert len(response.json()["deleted"]) == 2
    assert client.get("/api/control-logs").json() == []


def test_credential_profile_create_never_exposes_plaintext(client):
    response = client.post("/api/credential-profiles", json={"snmp_community": "supersecret", "name": "site-a"})
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "site-a"
    assert "snmp_community" not in body
    assert "supersecret" not in str(body)


def test_credential_profile_ssh_update_encrypts_and_never_exposes_password(client):
    from app.db import SessionLocal
    from app.models import CredentialProfile

    profile = client.post("/api/credential-profiles", json={"snmp_community": "public", "name": "ssh-site"}).json()
    response = client.patch(
        f"/api/credential-profiles/{profile['id']}/ssh",
        json={"username": "netadmin", "password": "ssh-secret", "port": 2222},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["has_ssh"] is True and body["ssh_port"] == 2222
    assert "netadmin" not in str(body) and "ssh-secret" not in str(body)

    session = SessionLocal()
    stored = session.get(CredentialProfile, profile["id"])
    assert stored.ssh_username_encrypted != "netadmin"
    assert stored.ssh_password_encrypted != "ssh-secret"
    session.close()


def test_credential_profile_duplicate_name_rejected(client):
    client.post("/api/credential-profiles", json={"snmp_community": "public", "name": "dup"})
    second = client.post("/api/credential-profiles", json={"snmp_community": "public2", "name": "dup"})
    assert second.status_code == 400


def test_credential_profile_list_and_delete(client):
    created = client.post("/api/credential-profiles", json={"snmp_community": "public", "name": "temp"}).json()
    listing = client.get("/api/credential-profiles").json()
    assert any(p["id"] == created["id"] for p in listing)

    delete_response = client.delete(f"/api/credential-profiles/{created['id']}")
    assert delete_response.status_code == 204
    listing_after = client.get("/api/credential-profiles").json()
    assert not any(p["id"] == created["id"] for p in listing_after)


def test_credential_profile_bulk_delete_removes_selected(client):
    a = client.post("/api/credential-profiles", json={"snmp_community": "public", "name": "a"}).json()
    b = client.post("/api/credential-profiles", json={"snmp_community": "public2", "name": "b"}).json()

    response = client.post("/api/credential-profiles/bulk-delete", json={"ids": [a["id"]]})
    assert response.status_code == 200
    assert response.json() == {"deleted": [a["id"]], "skipped": []}

    remaining_ids = {p["id"] for p in client.get("/api/credential-profiles").json()}
    assert remaining_ids == {b["id"]}


def test_credential_profile_delete_all_empties_the_list(client):
    client.post("/api/credential-profiles", json={"snmp_community": "public", "name": "a"})
    client.post("/api/credential-profiles", json={"snmp_community": "public2", "name": "b"})

    response = client.post("/api/credential-profiles/delete-all")
    assert response.status_code == 200
    assert len(response.json()["deleted"]) == 2
    assert client.get("/api/credential-profiles").json() == []


def test_system_info_returns_safe_config_values(client):
    response = client.get("/api/system-info")
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] >= 1
    assert "db_path" in body
    assert body["max_seed_hosts"] > 0
