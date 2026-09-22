"""15.3절: schema_version 기반 자동 Migration이 실제로 동작하는지 검증한다.

v1 스키마(모든 컬럼이 있지만 vlan_count는 없음) 상태의 DB에서 init_db()를
호출했을 때 ALTER TABLE로 컬럼이 추가되고 schema_version이 올라가는지 확인한다.
"""
from __future__ import annotations

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import db as db_module
from app.models import Base, SCHEMA_VERSION


def _table_columns(engine, table: str) -> set[str]:
    with engine.connect() as conn:
        rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}


def test_init_db_migrates_v1_schema_adds_vlan_count_column(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    # 최신 Base로 전체 스키마를 만든 뒤, vlan_count 컬럼만 제거해 "v1 상태"를 재현한다.
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql(
            """
            CREATE TABLE network_device_v1_copy AS SELECT
                id, hostname, management_ip, primary_mac, vendor, model, serial_number,
                os_name, os_version, firmware_version, snmp_engine_id, lldp_chassis_id,
                sys_object_id, device_type, device_role, role_source, classification_score,
                classification_method, classification_detail, role_score, role_detail,
                layer2_capable, layer3_capable, poe_capable, snmp_enabled, ssh_enabled,
                capabilities, physical_floor, floor_source, sys_location, discovery_depth,
                preferred_collector_id, status, snmp_community, first_seen_at, last_seen_at,
                created_at, updated_at
            FROM network_device
            """
        )
        conn.exec_driver_sql("DROP TABLE network_device")
        conn.exec_driver_sql("ALTER TABLE network_device_v1_copy RENAME TO network_device")
        conn.exec_driver_sql("DELETE FROM schema_version")
        conn.exec_driver_sql("INSERT INTO schema_version (id, version) VALUES (1, 1)")

    assert "vlan_count" not in _table_columns(engine, "network_device")

    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "SessionLocal", TestSessionLocal)

    db_module.init_db()

    assert "vlan_count" in _table_columns(engine, "network_device")
    assert "concurrency" in _table_columns(engine, "discovery_run")
    assert "hostname_rules" in _table_columns(engine, "discovery_run")
    assert "label" in _table_columns(engine, "network_link")
    with engine.connect() as conn:
        version = conn.execute(text("SELECT version FROM schema_version WHERE id=1")).scalar_one()
    assert version == SCHEMA_VERSION


def test_init_db_migrates_v11_schema_adds_network_link_label(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE network_link DROP COLUMN label")
        conn.exec_driver_sql("DELETE FROM schema_version")
        conn.exec_driver_sql("INSERT INTO schema_version (id, version) VALUES (1, 11)")

    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "SessionLocal", TestSessionLocal)

    db_module.init_db()

    assert "label" in _table_columns(engine, "network_link")
    with engine.connect() as conn:
        version = conn.execute(text("SELECT version FROM schema_version WHERE id=1")).scalar_one()
    assert version == SCHEMA_VERSION


def test_init_db_migrates_v12_schema_adds_credential_ssh_columns(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE credential_profile DROP COLUMN ssh_username_encrypted")
        conn.exec_driver_sql("ALTER TABLE credential_profile DROP COLUMN ssh_password_encrypted")
        conn.exec_driver_sql("ALTER TABLE credential_profile DROP COLUMN ssh_port")
        conn.exec_driver_sql("DELETE FROM schema_version")
        conn.exec_driver_sql("INSERT INTO schema_version (id, version) VALUES (1, 12)")

    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "SessionLocal", TestSessionLocal)
    db_module.init_db()

    columns = _table_columns(engine, "credential_profile")
    assert {"ssh_username_encrypted", "ssh_password_encrypted", "ssh_port"}.issubset(columns)


def test_init_db_migrates_v13_schema_adds_cli_protocol(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE credential_profile DROP COLUMN cli_protocol")
        conn.exec_driver_sql("DELETE FROM schema_version")
        conn.exec_driver_sql("INSERT INTO schema_version (id, version) VALUES (1, 13)")

    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "SessionLocal", TestSessionLocal)
    db_module.init_db()

    assert "cli_protocol" in _table_columns(engine, "credential_profile")
    with engine.connect() as conn:
        protocol = conn.execute(text("SELECT cli_protocol FROM credential_profile LIMIT 1")).scalar_one_or_none()
        version = conn.execute(text("SELECT version FROM schema_version WHERE id=1")).scalar_one()
    assert protocol is None or protocol == "SSH"
    assert version == SCHEMA_VERSION
