"""16장 REST API 통합 테스트 (FastAPI TestClient, 임시 SQLite 파일 사용).

주의: sys.modules에서 app.* 항목을 지우고 재-import하는 방식은 이미 import된
다른 테스트 모듈(app.discovery.engine 등)이 들고 있는 클래스/함수 객체가 참조하는
모듈 전역과, monkeypatch가 patch하는 "새로 재-import된" 모듈 객체가 어긋나는
테스트 간 오염을 일으킨다(실제로 전체 스위트 실행 시 재현됨). 대신 이미 import된
app.db/app.api.routes_discovery의 엔진/세션 팩토리 속성만 정밀하게 monkeypatch한다.
"""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _run_to_completion(client, run_id: int, timeout_seconds: float = 10.0) -> dict:
    """Discovery Run은 동시 실행이 허용되지 않으므로(routes_discovery.py), 다음
    Run을 시작하기 전 현재 Run이 끝날 때까지 폴링한다."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        run = client.get(f"/api/discovery/{run_id}").json()
        if run["status"] != "RUNNING":
            return run
        time.sleep(0.1)
    raise TimeoutError(f"Discovery Run #{run_id}이 {timeout_seconds}초 내에 끝나지 않았습니다.")


@pytest.fixture()
def client(monkeypatch, tmp_path):
    import app.api.routes_discovery as routes_discovery_mod
    from app import config, db

    monkeypatch.setattr(config, "AUTO_DISCOVERY_ENABLED", False)

    db_path = tmp_path / "nms_test.db"
    test_engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False}, future=True)
    # 실제 운영 엔진과 동일하게 WAL+busy_timeout을 적용해야 한다. 그렇지 않으면
    # Discovery 백그라운드 태스크 여러 개가 동시에 commit할 때 기본 rollback-journal
    # 모드에서 "database is locked"가 즉시 발생한다(실제로 재현된 문제).
    db.configure_sqlite_pragmas(test_engine)

    TestSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False, future=True)

    monkeypatch.setattr(db, "engine", test_engine)
    monkeypatch.setattr(db, "SessionLocal", TestSessionLocal)
    # routes_discovery는 `from app.db import SessionLocal`로 값을 직접 복사해두었으므로 별도 patch 필요.
    monkeypatch.setattr(routes_discovery_mod, "SessionLocal", TestSessionLocal)

    db.init_db()

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_list_devices_empty_initially(client):
    response = client.get("/api/devices")
    assert response.status_code == 200
    assert response.json() == []


def test_reclassify_endpoint_fixes_stale_classification(client):
    from app.db import SessionLocal
    from app.identity import DeviceObservation, resolve_or_create_device

    session = SessionLocal()
    device, _ = resolve_or_create_device(session, DeviceObservation(management_ip="172.16.1.220", hostname="NSH2228CF"))
    device.device_type = "UNKNOWN"
    device.layer2_capable = True
    device.layer3_capable = False
    device_id = device.id
    session.commit()
    session.close()

    response = client.post("/api/devices/reclassify")
    assert response.status_code == 200
    assert response.json()["changed"] == 1

    updated = client.get(f"/api/devices/{device_id}").json()
    assert updated["device_type"] == "L2_SWITCH"


def test_device_not_found_returns_404(client):
    response = client.get("/api/devices/999")
    assert response.status_code == 404


def test_discovery_start_and_poll(client):
    response = client.post("/api/discovery/start", json={"seed_targets": ["127.0.0.1"], "profile": "LIGHT"})
    assert response.status_code == 200
    run = response.json()
    assert run["status"] == "RUNNING"
    assert run["profile"] == "LIGHT"

    poll = client.get(f"/api/discovery/{run['id']}")
    assert poll.status_code == 200


def test_discovery_list_returns_history_most_recent_first(client):
    first = client.post("/api/discovery/start", json={"seed_targets": ["127.0.0.1"], "profile": "LIGHT"}).json()
    _run_to_completion(client, first["id"])
    second = client.post("/api/discovery/start", json={"seed_targets": ["127.0.0.1"], "profile": "LIGHT"}).json()
    _run_to_completion(client, second["id"])

    listing = client.get("/api/discovery")
    assert listing.status_code == 200
    ids = [row["id"] for row in listing.json()]
    assert ids.index(second["id"]) < ids.index(first["id"])


def test_discovery_start_rejects_when_already_running(client, monkeypatch):
    """동시 Discovery Run은 SQLite 쓰기 트랜잭션 경합("database is locked")을
    유발할 수 있어 허용하지 않는다 - 단일 asyncio 이벤트 루프 위에서 여러 Run이
    await 경계에 걸쳐 트랜잭션을 연 채 대기하면 서로의 커밋을 막기 때문이다."""
    import asyncio

    hold = asyncio.Event()

    async def _never_completes(self, run_id, seed_targets):
        await hold.wait()

    monkeypatch.setattr("app.discovery.engine.DiscoveryEngine.run", _never_completes)

    first = client.post("/api/discovery/start", json={"seed_targets": ["127.0.0.1"], "profile": "LIGHT"})
    assert first.status_code == 200

    second = client.post("/api/discovery/start", json={"seed_targets": ["127.0.0.1"], "profile": "LIGHT"})
    assert second.status_code == 409

    hold.set()


def test_discovery_list_respects_limit(client):
    for _ in range(3):
        run = client.post("/api/discovery/start", json={"seed_targets": ["127.0.0.1"], "profile": "LIGHT"}).json()
        _run_to_completion(client, run["id"])
    listing = client.get("/api/discovery", params={"limit": 2})
    assert listing.status_code == 200
    assert len(listing.json()) == 2


def test_discovery_list_accepts_limit_500(client):
    """[KOS20260921] 프론트엔드 페이지네이션이 500(최대 페이지 크기)만큼 한번에
    요청하므로, 이전 상한(le=200)이면 422가 나 목록 자체가 깨진다."""
    listing = client.get("/api/discovery", params={"limit": 500})
    assert listing.status_code == 200


def test_discovery_start_accepts_concurrency_and_hostname_rules(client):
    response = client.post(
        "/api/discovery/start",
        json={
            "seed_targets": ["127.0.0.1"],
            "profile": "LIGHT",
            "concurrency": 8,
            "hostname_rules": ["DNS", "NETBIOS", "BOGUS_RULE"],
        },
    )
    assert response.status_code == 200
    run = response.json()
    assert run["concurrency"] == 8
    assert json.loads(run["hostname_rules"]) == ["DNS", "NETBIOS"]  # 알 수 없는 규칙은 무시


def test_discovery_start_rejects_out_of_range_concurrency(client):
    response = client.post(
        "/api/discovery/start",
        json={"seed_targets": ["127.0.0.1"], "profile": "LIGHT", "concurrency": 0},
    )
    assert response.status_code == 400


def test_discovery_start_with_reset_links_clears_existing_links_first(client):
    """"기존 링크 정보를 모두 지우고 새로 하기" - reset_links=True면 Run이 시작되기
    전에(동기적으로) 기존 network_link가 전부 삭제돼야 한다."""
    from app.db import get_session
    from app.identity import DeviceObservation, resolve_or_create_device
    from app.models import NetworkLink

    session = get_session()
    a, _ = resolve_or_create_device(session, DeviceObservation(management_ip="10.9.9.1"))
    b, _ = resolve_or_create_device(session, DeviceObservation(management_ip="10.9.9.2"))
    session.flush()
    session.add(NetworkLink(src_device_id=a.id, dst_device_id=b.id, source="ARP", confidence=40, status="UP"))
    session.commit()
    session.close()

    verify = get_session()
    assert verify.query(NetworkLink).count() == 1
    verify.close()

    response = client.post(
        "/api/discovery/start",
        json={"seed_targets": ["127.0.0.1"], "profile": "LIGHT", "reset_links": True},
    )
    assert response.status_code == 200

    verify2 = get_session()
    assert verify2.query(NetworkLink).count() == 0
    verify2.close()


def test_discovery_start_without_reset_links_keeps_existing_links(client):
    from app.db import get_session
    from app.identity import DeviceObservation, resolve_or_create_device
    from app.models import NetworkLink

    session = get_session()
    a, _ = resolve_or_create_device(session, DeviceObservation(management_ip="10.9.9.3"))
    b, _ = resolve_or_create_device(session, DeviceObservation(management_ip="10.9.9.4"))
    session.flush()
    session.add(NetworkLink(src_device_id=a.id, dst_device_id=b.id, source="ARP", confidence=40, status="UP"))
    session.commit()
    session.close()

    response = client.post(
        "/api/discovery/start",
        json={"seed_targets": ["127.0.0.1"], "profile": "LIGHT"},
    )
    assert response.status_code == 200

    verify = get_session()
    assert verify.query(NetworkLink).count() == 1
    verify.close()


def test_discovery_start_with_reset_devices_clears_existing_devices_and_links_first(client):
    """"장치 정보도 모두 지우고 새로 하기" - reset_devices=True면 Run이 시작되기
    전에(동기적으로) 기존 network_device가 전부 삭제돼야 하고, FK CASCADE로
    연관된 network_link/device_interface도 함께 삭제돼야 한다."""
    from app.db import get_session
    from app.identity import DeviceObservation, resolve_or_create_device
    from app.models import DeviceInterface, NetworkDevice, NetworkLink

    session = get_session()
    a, _ = resolve_or_create_device(session, DeviceObservation(management_ip="10.9.9.5"))
    b, _ = resolve_or_create_device(session, DeviceObservation(management_ip="10.9.9.6"))
    session.flush()
    session.add(DeviceInterface(device_id=a.id, if_index=1, name="Gi0/1"))
    session.add(NetworkLink(src_device_id=a.id, dst_device_id=b.id, source="ARP", confidence=40, status="UP"))
    session.commit()
    session.close()

    verify = get_session()
    assert verify.query(NetworkDevice).count() == 2
    verify.close()

    response = client.post(
        "/api/discovery/start",
        json={"seed_targets": ["127.0.0.1"], "profile": "LIGHT", "reset_devices": True},
    )
    assert response.status_code == 200

    verify2 = get_session()
    assert verify2.query(NetworkDevice).count() == 0
    assert verify2.query(NetworkLink).count() == 0
    assert verify2.query(DeviceInterface).count() == 0
    verify2.close()


def test_discovery_start_without_reset_devices_keeps_existing_devices(client):
    from app.db import get_session
    from app.identity import DeviceObservation, resolve_or_create_device
    from app.models import NetworkDevice

    session = get_session()
    resolve_or_create_device(session, DeviceObservation(management_ip="10.9.9.7"))
    session.commit()
    session.close()

    response = client.post(
        "/api/discovery/start",
        json={"seed_targets": ["127.0.0.1"], "profile": "LIGHT"},
    )
    assert response.status_code == 200

    verify = get_session()
    assert verify.query(NetworkDevice).count() >= 1
    verify.close()


def test_discovery_delete_single_run(client):
    run = client.post("/api/discovery/start", json={"seed_targets": ["127.0.0.1"], "profile": "LIGHT"}).json()
    _run_to_completion(client, run["id"])

    response = client.delete(f"/api/discovery/{run['id']}")
    assert response.status_code == 204
    assert client.get(f"/api/discovery/{run['id']}").status_code == 404


def test_discovery_delete_running_run_rejected(client, monkeypatch):
    import asyncio

    hold = asyncio.Event()

    async def _never_completes(self, run_id, seed_targets):
        await hold.wait()

    monkeypatch.setattr("app.discovery.engine.DiscoveryEngine.run", _never_completes)
    run = client.post("/api/discovery/start", json={"seed_targets": ["127.0.0.1"], "profile": "LIGHT"}).json()

    response = client.delete(f"/api/discovery/{run['id']}")
    assert response.status_code == 409
    hold.set()


def test_discovery_bulk_delete_skips_running(client, monkeypatch):
    import asyncio

    hold = asyncio.Event()

    async def _never_completes(self, run_id, seed_targets):
        await hold.wait()

    monkeypatch.setattr("app.discovery.engine.DiscoveryEngine.run", _never_completes)
    running = client.post("/api/discovery/start", json={"seed_targets": ["127.0.0.1"], "profile": "LIGHT"}).json()

    response = client.post("/api/discovery/bulk-delete", json={"ids": [running["id"]]})
    assert response.status_code == 200
    assert response.json() == {"deleted": [], "skipped": [running["id"]]}
    assert client.get(f"/api/discovery/{running['id']}").status_code == 200
    hold.set()


def test_discovery_delete_all_removes_completed_runs(client):
    first = client.post("/api/discovery/start", json={"seed_targets": ["127.0.0.1"], "profile": "LIGHT"}).json()
    _run_to_completion(client, first["id"])
    second = client.post("/api/discovery/start", json={"seed_targets": ["127.0.0.1"], "profile": "LIGHT"}).json()
    _run_to_completion(client, second["id"])

    response = client.post("/api/discovery/delete-all")
    assert response.status_code == 200
    assert set(response.json()["deleted"]) == {first["id"], second["id"]}
    assert client.get("/api/discovery").json() == []


def test_discovery_seed_suggestions_returns_subnet_list(client):
    response = client.get("/api/discovery/seed-suggestions")
    assert response.status_code == 200
    body = response.json()
    assert "subnets" in body
    assert isinstance(body["subnets"], list)


def test_discovery_profiles_exposes_protocol_scope_per_profile(client):
    """[KOS20260921] Discovery 시작 화면에서 Profile별 수집 프로토콜을 보여주기
    위한 엔드포인트 - /discovery/{run_id} 라우트에 가로채이지 않고 응답해야 한다."""
    response = client.get("/api/discovery/profiles")
    assert response.status_code == 200
    body = response.json()
    profiles = {row["profile"]: row for row in body}
    assert set(profiles) == {"LIGHT", "STANDARD", "DETAILED", "TEMPLATE"}

    light_protocols = {p["key"]: p["enabled"] for p in profiles["LIGHT"]["protocols"]}
    assert all(enabled is False for enabled in light_protocols.values())
    assert profiles["LIGHT"]["expand_neighbors"] is False

    detailed_protocols = {p["key"]: p["enabled"] for p in profiles["DETAILED"]["protocols"]}
    assert all(enabled is True for enabled in detailed_protocols.values())
    assert profiles["DETAILED"]["expand_neighbors"] is True

    standard_protocols = {p["key"]: p["enabled"] for p in profiles["STANDARD"]["protocols"]}
    assert standard_protocols["collect_routes"] is False
    assert standard_protocols["collect_lldp"] is True


def test_discovery_start_rejects_oversized_cidr(client, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "MAX_SEED_HOSTS", 10)
    response = client.post("/api/discovery/start", json={"cidrs": ["10.0.0.0/24"], "profile": "LIGHT"})
    assert response.status_code == 400


def test_discovery_start_rejects_unknown_profile(client):
    response = client.post("/api/discovery/start", json={"seed_targets": ["127.0.0.1"], "profile": "BOGUS"})
    assert response.status_code == 400


def test_role_patch_requires_valid_role(client):
    from app.db import SessionLocal
    from app.identity import DeviceObservation, resolve_or_create_device

    session = SessionLocal()
    device, _ = resolve_or_create_device(session, DeviceObservation(management_ip="10.0.0.50"))
    session.commit()
    device_id = device.id
    session.close()

    bad = client.patch(f"/api/devices/{device_id}/role", json={"device_role": "NOT_A_ROLE"})
    assert bad.status_code == 400

    good = client.patch(f"/api/devices/{device_id}/role", json={"device_role": "CORE_SWITCH", "performed_by": "alice"})
    assert good.status_code == 200
    body = good.json()
    assert body["device_role"] == "CORE_SWITCH"
    assert body["role_source"] == "MANUAL"

    # [KOS20260923] 구성 변경 이력 - 수동 Role 변경은 ROLE_CHANGE로 문서화만
    # 돼 있고 실제로는 기록되지 않던 감사 공백이었다. ConfigChangeLog(source=
    # MANUAL, performed_by=요청자)로 남는지 확인한다.
    changes = client.get("/api/config-changes", params={"device_id": device_id}).json()
    role_changes = [c for c in changes if c["field_name"] == "device_role"]
    assert len(role_changes) == 1
    assert role_changes[0]["new_value"] == "CORE_SWITCH"
    assert role_changes[0]["source"] == "MANUAL"
    assert role_changes[0]["performed_by"] == "alice"


def test_port_control_returns_404_for_unknown_interface(client):
    response = client.post("/api/devices/1/interfaces/1/enable", json={"performed_by": "tester"})
    assert response.status_code == 404


def test_topology_endpoint_returns_nodes_and_links_shape(client):
    response = client.get("/api/topology")
    assert response.status_code == 200
    body = response.json()
    assert "nodes" in body and "links" in body


def test_create_and_delete_manual_topology_link(client):
    from app.db import SessionLocal
    from app.identity import DeviceObservation, resolve_or_create_device
    from app.models import NetworkLink, NetworkLinkEvidence

    session = SessionLocal()
    first, _ = resolve_or_create_device(session, DeviceObservation(management_ip="10.20.0.1"))
    second, _ = resolve_or_create_device(session, DeviceObservation(management_ip="10.20.0.2"))
    session.commit()
    first_id, second_id = first.id, second.id
    session.close()

    created = client.post(
        "/api/topology/links",
        json={"src_device_id": first_id, "dst_device_id": second_id, "label": "  수동 백업 경로  "},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["label"] == "수동 백업 경로"
    assert body["source"] == "MANUAL"

    deleted = client.delete(f"/api/topology/links/{body['id']}")
    assert deleted.status_code == 204

    verify = SessionLocal()
    assert verify.get(NetworkLink, body["id"]) is None
    assert verify.query(NetworkLinkEvidence).filter(NetworkLinkEvidence.link_id == body["id"]).count() == 0
    verify.close()
    assert client.delete(f"/api/topology/links/{body['id']}").status_code == 404


def test_topology_link_ping_runs_from_source_device_over_ssh(client, monkeypatch):
    from app.db import SessionLocal
    from app.identity import DeviceObservation, resolve_or_create_device
    from app.models import NetworkLink

    profile = client.post("/api/credential-profiles", json={"snmp_community": "public", "name": "ping-source"}).json()
    client.patch(
        f"/api/credential-profiles/{profile['id']}/ssh",
        json={"username": "admin", "password": "secret", "port": 22},
    )
    session = SessionLocal()
    source, _ = resolve_or_create_device(session, DeviceObservation(management_ip="192.0.2.1"))
    target, _ = resolve_or_create_device(session, DeviceObservation(management_ip="192.0.2.2"))
    source.credential_profile_id = profile["id"]
    session.flush()
    link = NetworkLink(src_device_id=source.id, dst_device_id=target.id, source="MANUAL", confidence=100, status="UP")
    session.add(link)
    session.commit()
    link_id = link.id
    session.close()

    calls = []
    monkeypatch.setattr(
        "app.api.routes_topology.run_remote_ping",
        lambda *args: calls.append(args) or ("ping 192.0.2.2", True, "Success rate is 100 percent", 0.0, 1.0, 2.0, 3.0),
    )
    response = client.post(f"/api/topology/links/{link_id}/ping")
    assert response.status_code == 200
    body = response.json()
    assert body["supported"] is True and body["success"] is True
    assert body["from_ip"] == "192.0.2.1" and body["to_ip"] == "192.0.2.2"
    assert body["packet_loss_percent"] == 0.0 and body["rtt_avg_ms"] == 2.0
    assert calls[0][:5] == ("192.0.2.1", "192.0.2.2", "admin", "secret", 22)
    assert calls[0][6] == "SSH"


def test_topology_link_ping_accepts_transient_telnet_credentials(client, monkeypatch):
    from app.db import SessionLocal
    from app.identity import DeviceObservation, resolve_or_create_device
    from app.models import NetworkLink

    session = SessionLocal()
    source, _ = resolve_or_create_device(session, DeviceObservation(management_ip="192.0.2.11"))
    target, _ = resolve_or_create_device(session, DeviceObservation(management_ip="192.0.2.12"))
    session.flush()
    link = NetworkLink(src_device_id=source.id, dst_device_id=target.id, source="MANUAL", confidence=100, status="UP")
    session.add(link)
    session.commit()
    link_id = link.id
    session.close()

    calls = []
    monkeypatch.setattr(
        "app.api.routes_topology.run_remote_ping",
        lambda *args: calls.append(args) or ("ping 192.0.2.12", True, "0% packet loss", 0.0, 1.0, 1.5, 2.0),
    )
    response = client.post(
        f"/api/topology/links/{link_id}/ping",
        json={"protocol": "TELNET", "username": "operator", "password": "temporary", "port": 23},
    )

    assert response.status_code == 200
    assert response.json()["protocol"] == "TELNET"
    assert calls[0][:5] == ("192.0.2.11", "192.0.2.12", "operator", "temporary", 23)
    assert calls[0][6] == "TELNET"


def test_topology_link_ping_can_reverse_from_and_to(client, monkeypatch):
    from app.credentials import set_cli_credential
    from app.db import SessionLocal
    from app.identity import DeviceObservation, resolve_or_create_device
    from app.models import CredentialProfile, NetworkLink

    session = SessionLocal()
    profile = CredentialProfile(name="reverse-target")
    session.add(profile)
    session.flush()
    set_cli_credential(profile, "admin", "stored-password", 23, "TELNET")
    first, _ = resolve_or_create_device(session, DeviceObservation(management_ip="192.0.2.21"))
    second, _ = resolve_or_create_device(session, DeviceObservation(management_ip="192.0.2.22"))
    second.credential_profile_id = profile.id
    session.flush()
    link = NetworkLink(src_device_id=first.id, dst_device_id=second.id, source="MANUAL", confidence=100, status="UP")
    session.add(link)
    session.commit()
    link_id = link.id
    session.close()

    calls = []
    monkeypatch.setattr(
        "app.api.routes_topology.run_remote_ping",
        lambda *args: calls.append(args) or ("ping 192.0.2.21", True, "0% packet loss", 0.0, 1.0, 1.5, 2.0),
    )
    response = client.post(f"/api/topology/links/{link_id}/ping", json={"direction": "REVERSE"})

    assert response.status_code == 200
    body = response.json()
    assert body["from_ip"] == "192.0.2.22" and body["to_ip"] == "192.0.2.21"
    assert body["protocol"] == "TELNET"
    assert calls[0][:5] == ("192.0.2.22", "192.0.2.21", "admin", "stored-password", 23)


def test_topology_link_ping_can_override_protocol_while_using_stored_password(client, monkeypatch):
    from app.credentials import set_cli_credential
    from app.db import SessionLocal
    from app.identity import DeviceObservation, resolve_or_create_device
    from app.models import CredentialProfile, NetworkLink

    session = SessionLocal()
    profile = CredentialProfile(name="legacy-nsh")
    session.add(profile)
    session.flush()
    set_cli_credential(profile, "admin", "stored-password", 23, "TELNET")
    source, _ = resolve_or_create_device(session, DeviceObservation(management_ip="192.0.2.31"))
    target, _ = resolve_or_create_device(session, DeviceObservation(management_ip="192.0.2.32"))
    source.credential_profile_id = profile.id
    session.flush()
    link = NetworkLink(src_device_id=source.id, dst_device_id=target.id, source="MANUAL", confidence=100, status="UP")
    session.add(link)
    session.commit()
    link_id = link.id
    session.close()

    calls = []
    monkeypatch.setattr(
        "app.api.routes_topology.run_remote_ping",
        lambda *args: calls.append(args) or ("ping 192.0.2.32", True, "0% packet loss", 0.0, 1.0, 1.5, 2.0),
    )
    response = client.post(
        f"/api/topology/links/{link_id}/ping",
        json={"direction": "FORWARD", "protocol": "SSH", "username": "admin", "port": 22},
    )

    assert response.status_code == 200
    assert response.json()["protocol"] == "SSH"
    assert calls[0][:5] == ("192.0.2.31", "192.0.2.32", "admin", "stored-password", 22)


def test_topology_link_ping_reports_missing_source_ssh_credential(client):
    from app.db import SessionLocal
    from app.identity import DeviceObservation, resolve_or_create_device
    from app.models import NetworkLink

    session = SessionLocal()
    source, _ = resolve_or_create_device(session, DeviceObservation(management_ip="198.51.100.1"))
    target, _ = resolve_or_create_device(session, DeviceObservation(management_ip="198.51.100.2"))
    session.flush()
    link = NetworkLink(src_device_id=source.id, dst_device_id=target.id, source="MANUAL", confidence=100, status="UP")
    session.add(link)
    session.commit()
    link_id = link.id
    session.close()

    body = client.post(f"/api/topology/links/{link_id}/ping").json()
    assert body["supported"] is False and body["success"] is False
    assert "CLI" in body["output"]


def test_topology_link_role_distinguishes_hierarchical_from_peer(client):
    """[KOS20260921] 서로 다른 depth를 잇는 링크는 HIERARCHICAL(한쪽에선 Uplink,
    반대쪽에선 Downlink), 같은 depth끼리는 PEER로 표시되어야 한다."""
    from app.db import SessionLocal
    from app.identity import DeviceObservation, resolve_or_create_device
    from app.models import NetworkLink

    session = SessionLocal()
    core, _ = resolve_or_create_device(session, DeviceObservation(management_ip="10.9.0.1"))
    core2, _ = resolve_or_create_device(session, DeviceObservation(management_ip="10.9.0.2"))
    floor, _ = resolve_or_create_device(session, DeviceObservation(management_ip="10.9.0.3"))
    session.flush()
    core.discovery_depth = 0
    core2.discovery_depth = 0
    floor.discovery_depth = 1
    session.add(NetworkLink(src_device_id=core.id, dst_device_id=core2.id, source="MANUAL", confidence=100, status="UP"))
    session.add(NetworkLink(src_device_id=core.id, dst_device_id=floor.id, source="MANUAL", confidence=100, status="UP"))
    session.commit()
    core_id, core2_id, floor_id = core.id, core2.id, floor.id
    session.close()

    body = client.get("/api/topology").json()
    roles_by_pair = {frozenset({link["src_device_id"], link["dst_device_id"]}): link["link_role"] for link in body["links"]}
    assert roles_by_pair[frozenset({core_id, core2_id})] == "PEER"
    assert roles_by_pair[frozenset({core_id, floor_id})] == "HIERARCHICAL"


def test_topology_link_exposes_port_names_when_interfaces_known(client):
    """[KOS20260921] Link 레이블을 "근거+Confidence" 대신 "포트no -> 포트no"로
    보여 달라는 요청 - src/dst_interface_id가 있으면 이름을 채워야 한다."""
    from app.db import SessionLocal
    from app.identity import DeviceObservation, resolve_or_create_device
    from app.models import DeviceInterface, NetworkLink

    session = SessionLocal()
    core, _ = resolve_or_create_device(session, DeviceObservation(management_ip="10.9.1.1"))
    floor, _ = resolve_or_create_device(session, DeviceObservation(management_ip="10.9.1.2"))
    session.flush()
    core.discovery_depth = 0
    floor.discovery_depth = 1
    core_if = DeviceInterface(device_id=core.id, if_index=1, name="Gi0/1")
    floor_if = DeviceInterface(device_id=floor.id, if_index=24, name="Gi0/24")
    session.add_all([core_if, floor_if])
    session.flush()
    session.add(
        NetworkLink(
            src_device_id=core.id,
            src_interface_id=core_if.id,
            dst_device_id=floor.id,
            dst_interface_id=floor_if.id,
            source="LLDP",
            confidence=100,
            status="UP",
        )
    )
    # 포트 정보가 없는 ARP-only 링크도 함께 넣어 None으로 잘 나오는지 확인한다.
    other, _ = resolve_or_create_device(session, DeviceObservation(management_ip="10.9.1.3"))
    session.flush()
    session.add(NetworkLink(src_device_id=core.id, dst_device_id=other.id, source="ARP", confidence=40, status="UP"))
    session.commit()
    core_id, floor_id, other_id = core.id, floor.id, other.id
    session.close()

    body = client.get("/api/topology").json()
    by_pair = {frozenset({link["src_device_id"], link["dst_device_id"]}): link for link in body["links"]}

    named = by_pair[frozenset({core_id, floor_id})]
    assert named["src_port"] == "Gi0/1"
    assert named["dst_port"] == "Gi0/24"

    unnamed = by_pair[frozenset({core_id, other_id})]
    assert unnamed["src_port"] is None
    assert unnamed["dst_port"] is None
