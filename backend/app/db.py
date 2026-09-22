"""DB 엔진/세션 및 스키마 초기화 (SQLite3 또는 PostgreSQL - NMS_DB_BACKEND로 선택).

설계서 15.1 / 15.3절:
- (SQLite) WAL 모드, PRAGMA foreign_keys=ON, Busy Timeout 적용
- 애플리케이션 시작 시 schema_version을 확인하여 필요한 테이블/인덱스를 자동 생성한다.
"""
from __future__ import annotations

import logging

from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.exc import OperationalError, ProgrammingError

from app import config
from app.models import Base, SCHEMA_VERSION, DiscoveryRun, SchemaVersion, utcnow

logger = logging.getLogger("nms.db")

def configure_sqlite_pragmas(target_engine, busy_timeout_ms: int = config.SQLITE_BUSY_TIMEOUT_MS) -> None:
    """15.1절 WAL/FK/Busy Timeout PRAGMA를 엔진에 등록한다.

    테스트가 별도 SQLite 엔진(임시 파일/StaticPool)을 쓸 때도 반드시 이 함수로
    동일하게 설정해야 한다 - WAL/busy_timeout 없이 여러 Discovery 백그라운드
    태스크가 동시에 commit하면 기본 rollback-journal 모드에서 즉시
    "database is locked" 오류가 나는 것을 실제로 확인했다(테스트 스위트 동시
    실행 중 재현), busy_timeout이 있으면 짧게 대기 후 재시도해 오류를 피한다.
    """

    @event.listens_for(target_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        cursor.close()


def _ensure_postgres_database_exists() -> None:
    """[KOS20260921] SQLite는 파일이 없으면 연결 시점에 자동으로 생겨나지만,
    PostgreSQL은 대상 DATABASE가 미리 존재해야만 연결할 수 있다("DB 변경 시
    없으면 생성한다" 요구사항). 같은 접속 정보로 관리용 DB(postgres)에 먼저
    연결해 존재 여부를 확인하고 없으면 만든다. CREATE DATABASE는 트랜잭션
    안에서 실행할 수 없어(PostgreSQL 제약) AUTOCOMMIT 격리수준의 별도 연결을
    쓴다. 접속 계정에 CREATEDB 권한이 없으면 이 시도 자체가 실패할 수 있는데,
    그 경우 원인을 알 수 있도록 그대로 예외를 올린다(DBA가 미리 만들어 두거나
    권한을 부여해야 한다).
    """
    admin_url = f"postgresql+psycopg2://{config.PG_USER}:{config.PG_PASSWORD}@{config.PG_HOST}:{config.PG_PORT}/postgres"
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT", future=True)
    try:
        with admin_engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": config.PG_DB}
            ).scalar()
            if exists:
                return
            logger.info("PostgreSQL 데이터베이스 %s가 없어 새로 생성합니다", config.PG_DB)
            conn.execute(text(f'CREATE DATABASE "{config.PG_DB}"'))
    except (OperationalError, ProgrammingError) as exc:
        raise RuntimeError(
            f"PostgreSQL 데이터베이스 '{config.PG_DB}' 확인/생성에 실패했습니다 "
            f"({config.PG_USER}@{config.PG_HOST}:{config.PG_PORT}). 접속 계정에 CREATEDB 권한이 있는지, "
            "또는 DBA가 데이터베이스를 미리 만들어 뒀는지 확인하세요."
        ) from exc
    finally:
        admin_engine.dispose()


if config.DB_BACKEND == "postgresql":
    _ensure_postgres_database_exists()
    engine = create_engine(config.DATABASE_URL, future=True)
else:
    engine = create_engine(
        config.DATABASE_URL,
        connect_args={"check_same_thread": False},
        future=True,
    )
    configure_sqlite_pragmas(engine)


# [KOS20260921] expire_on_commit=False를 시도했다가 되돌렸다: Discovery
# Worker들이 discovery_run 한 행을 동시에 갱신(scanned_count/alive_count 등)
# 하는데, expire_on_commit=False면 각 Worker의 Session이 이전에 읽은 값을
# Identity Map에 캐시한 채 재조회하지 않아 다른 Worker의 동시 commit을 못 보고
# 서로의 갱신을 덮어쓰는 "Lost Update"가 실제로 재현됐다(alive_count가 8이어야
# 하는데 1로 끝남). 기본값(True)을 유지해 매 commit 후 재조회하도록 한다 -
# 추가 SELECT 비용보다 동시 갱신 정확성이 우선이다.
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def _column_exists(conn, table: str, column: str) -> bool:
    # [KOS20260921] PostgreSQL에는 PRAGMA가 없다 - information_schema로 조회한다.
    # (실제로는 새 PostgreSQL 배포는 schema_version이 없는 상태에서 시작해
    # Base.metadata.create_all()이 현재 모델 정의로 테이블을 통째로 만들기
    # 때문에 이 함수/_MIGRATIONS의 ALTER TABLE 문은 거치지 않는다 - 이 분기는
    # PostgreSQL을 이미 쓰던 배포가 이후 스키마 버전을 올릴 때를 대비한 것이다.)
    if conn.dialect.name == "postgresql":
        row = conn.execute(
            text("SELECT 1 FROM information_schema.columns WHERE table_name = :t AND column_name = :c"),
            {"t": table, "c": column},
        ).first()
        return row is not None
    rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def _migrate_v1_to_v2(conn) -> None:
    """v2: network_device.vlan_count 추가 (Q-BRIDGE-MIB VLAN 채점 정확도 개선)."""
    if not _column_exists(conn, "network_device", "vlan_count"):
        conn.exec_driver_sql("ALTER TABLE network_device ADD COLUMN vlan_count INTEGER NOT NULL DEFAULT 0")
        logger.info("migration v1->v2: network_device.vlan_count 컬럼 추가")


def _migrate_v2_to_v3(conn) -> None:
    """v3(18.1절): network_device.credential_profile_id 추가.

    credential_profile 테이블 자체는 신규 테이블이라 Base.metadata.create_all()이
    자동으로 만들어주므로, 여기서는 기존 테이블에 대한 컬럼 추가만 처리한다.
    """
    if not _column_exists(conn, "network_device", "credential_profile_id"):
        conn.exec_driver_sql("ALTER TABLE network_device ADD COLUMN credential_profile_id INTEGER REFERENCES credential_profile(id)")
        logger.info("migration v2->v3: network_device.credential_profile_id 컬럼 추가")


def _migrate_v3_to_v4(conn) -> None:
    """v4(17.5절): discovery_run.current_ip 추가 (진행 중 스캔 IP 실시간 표시)."""
    if not _column_exists(conn, "discovery_run", "current_ip"):
        conn.exec_driver_sql("ALTER TABLE discovery_run ADD COLUMN current_ip VARCHAR(64)")
        logger.info("migration v3->v4: discovery_run.current_ip 컬럼 추가")


def _migrate_v4_to_v5(conn) -> None:
    """v5: discovery_run.concurrency/hostname_rules 추가 (동시 스캔 개수, hostname 식별 규칙)."""
    if not _column_exists(conn, "discovery_run", "concurrency"):
        conn.exec_driver_sql("ALTER TABLE discovery_run ADD COLUMN concurrency INTEGER NOT NULL DEFAULT 1")
        logger.info("migration v4->v5: discovery_run.concurrency 컬럼 추가")
    if not _column_exists(conn, "discovery_run", "hostname_rules"):
        conn.exec_driver_sql("ALTER TABLE discovery_run ADD COLUMN hostname_rules TEXT")
        logger.info("migration v4->v5: discovery_run.hostname_rules 컬럼 추가")


def _migrate_v5_to_v6(conn) -> None:
    """v6: discovery_run.current_ips 추가 (동시 스캔 중인 IP 전체 목록, JSON)."""
    if not _column_exists(conn, "discovery_run", "current_ips"):
        conn.exec_driver_sql("ALTER TABLE discovery_run ADD COLUMN current_ips TEXT")
        logger.info("migration v5->v6: discovery_run.current_ips 컬럼 추가")


def _migrate_v6_to_v7(conn) -> None:
    """v7: device_interface.is_downlink 추가 (Uplink와 함께 링크 방향 표시)."""
    if not _column_exists(conn, "device_interface", "is_downlink"):
        conn.exec_driver_sql("ALTER TABLE device_interface ADD COLUMN is_downlink BOOLEAN NOT NULL DEFAULT 0")
        logger.info("migration v6->v7: device_interface.is_downlink 컬럼 추가")


def _migrate_v7_to_v8(conn) -> None:
    """v8: device_interface.in_octets/out_octets 추가 (수집 시점 누적 TX/RX 바이트)."""
    if not _column_exists(conn, "device_interface", "in_octets"):
        conn.exec_driver_sql("ALTER TABLE device_interface ADD COLUMN in_octets INTEGER")
        logger.info("migration v7->v8: device_interface.in_octets 컬럼 추가")
    if not _column_exists(conn, "device_interface", "out_octets"):
        conn.exec_driver_sql("ALTER TABLE device_interface ADD COLUMN out_octets INTEGER")
        logger.info("migration v7->v8: device_interface.out_octets 컬럼 추가")


def _migrate_v8_to_v9(conn) -> None:
    """v9: network_device.sys_descr 추가 (SNMP sysDescr 원문 - vendor/model 파악용)."""
    if not _column_exists(conn, "network_device", "sys_descr"):
        conn.exec_driver_sql("ALTER TABLE network_device ADD COLUMN sys_descr TEXT")
        logger.info("migration v8->v9: network_device.sys_descr 컬럼 추가")


def _migrate_v9_to_v10(conn) -> None:
    """v10: STP 지원 - network_device.is_stp_root, device_interface.stp_state 추가.

    [KOS20260922] PostgreSQL이 실제로 쓰이기 시작해(nms2db), 이 마이그레이션부터는
    BOOLEAN DEFAULT 리터럴을 dialect별로 분기한다(SQLite는 0/1, PostgreSQL은
    TRUE/FALSE만 허용) - v9 이전 마이그레이션들은 새 PostgreSQL 배포가 거치지
    않아 문제없지만, 이미 PostgreSQL로 운영 중인 배포가 v10으로 올라갈 때는
    이 함수를 실제로 거치므로 문법을 맞춰야 한다.
    """
    bool_default = "DEFAULT FALSE" if conn.dialect.name == "postgresql" else "DEFAULT 0"
    if not _column_exists(conn, "network_device", "is_stp_root"):
        conn.exec_driver_sql(f"ALTER TABLE network_device ADD COLUMN is_stp_root BOOLEAN NOT NULL {bool_default}")
        logger.info("migration v9->v10: network_device.is_stp_root 컬럼 추가")
    if not _column_exists(conn, "device_interface", "stp_state"):
        conn.exec_driver_sql("ALTER TABLE device_interface ADD COLUMN stp_state VARCHAR(16)")
        logger.info("migration v9->v10: device_interface.stp_state 컬럼 추가")


def _migrate_v10_to_v11(conn) -> None:
    """v11: device_interface.stp_designated_bridge_mac 추가.

    [KOS20260922] LLDP-MIB을 지원하지 않는 장비가 많아 LLDP만으로는 실제 이웃을
    놓치는 경우가 많다는 사용자 조사 요청에 따라, dot1dStpPortDesignatedBridge
    (LLDP보다 훨씬 보편적으로 지원되는 BRIDGE-MIB STP 그룹)로 링크를 보강한다."""
    if not _column_exists(conn, "device_interface", "stp_designated_bridge_mac"):
        conn.exec_driver_sql("ALTER TABLE device_interface ADD COLUMN stp_designated_bridge_mac VARCHAR(32)")
        logger.info("migration v10->v11: device_interface.stp_designated_bridge_mac 컬럼 추가")


def _migrate_v11_to_v12(conn) -> None:
    """v12: 운영자가 입력한 Topology 링크 라벨 추가."""
    if not _column_exists(conn, "network_link", "label"):
        conn.exec_driver_sql("ALTER TABLE network_link ADD COLUMN label VARCHAR(255)")
        logger.info("migration v11->v12: network_link.label 컬럼 추가")


def _migrate_v12_to_v13(conn) -> None:
    """v13: Credential Profile에 암호화된 SSH 인증정보와 포트 추가."""
    if not _column_exists(conn, "credential_profile", "ssh_username_encrypted"):
        conn.exec_driver_sql("ALTER TABLE credential_profile ADD COLUMN ssh_username_encrypted TEXT")
    if not _column_exists(conn, "credential_profile", "ssh_password_encrypted"):
        conn.exec_driver_sql("ALTER TABLE credential_profile ADD COLUMN ssh_password_encrypted TEXT")
    if not _column_exists(conn, "credential_profile", "ssh_port"):
        conn.exec_driver_sql("ALTER TABLE credential_profile ADD COLUMN ssh_port INTEGER NOT NULL DEFAULT 22")
    logger.info("migration v12->v13: credential_profile SSH 컬럼 추가")


def _migrate_v13_to_v14(conn) -> None:
    """v14: 장비 CLI Credential에 SSH/Telnet 프로토콜 구분 추가."""
    if not _column_exists(conn, "credential_profile", "cli_protocol"):
        conn.exec_driver_sql("ALTER TABLE credential_profile ADD COLUMN cli_protocol VARCHAR(8) NOT NULL DEFAULT 'SSH'")
        logger.info("migration v13->v14: credential_profile.cli_protocol 컬럼 추가")


# [KOS20260921] v9까지의 마이그레이션들의 ALTER TABLE 문(예: "BOOLEAN ... DEFAULT 0")은
# SQLite 기준으로 작성됐다. 새로 만드는 PostgreSQL 배포는 schema_version 행 자체가
# 없는 상태로 시작해 init_db()가 이 함수들을 거치지 않고 Base.metadata.create_all()
# (SQLAlchemy가 각 dialect에 맞게 DDL을 번역)로 테이블을 한 번에 만들기 때문에 영향이
# 없다. v10부터는 PostgreSQL로 이미 운영 중인 배포가 실제로 거칠 수 있으므로 dialect
# 분기를 반영했다(_migrate_v9_to_v10 참고) - 앞으로 추가하는 마이그레이션도 동일하게
# 다이얼렉트를 고려해야 한다.
_MIGRATIONS = {
    2: _migrate_v1_to_v2,
    3: _migrate_v2_to_v3,
    4: _migrate_v3_to_v4,
    5: _migrate_v4_to_v5,
    6: _migrate_v5_to_v6,
    7: _migrate_v6_to_v7,
    8: _migrate_v7_to_v8,
    9: _migrate_v8_to_v9,
    10: _migrate_v9_to_v10,
    11: _migrate_v10_to_v11,
    12: _migrate_v11_to_v12,
    13: _migrate_v12_to_v13,
    14: _migrate_v13_to_v14,
}


def _mark_orphaned_discovery_runs_failed() -> None:
    """프로세스가 재시작되면 이전에 떠 있던 백그라운드 Discovery 태스크는 모두
    사라지지만, DB에는 status=RUNNING인 행이 그대로 남아 '영원히 진행 중'인 것처럼
    보인다(실제로 동시 실행 중이던 Run이 뒤엉키는 상황에서 확인됨). 시작 시점에
    RUNNING인 행은 이번 프로세스가 이어받은 것이 아니므로 FAILED로 정리한다.
    """
    with SessionLocal() as session:
        orphaned = session.scalars(select(DiscoveryRun).where(DiscoveryRun.status == "RUNNING")).all()
        for run in orphaned:
            run.status = "FAILED"
            run.current_ip = None
            run.current_ips = None
            run.ended_at = utcnow()
            logger.warning("orphaned discovery_run #%s를 FAILED로 정리", run.id)
        if orphaned:
            session.commit()


def init_db() -> None:
    """DB 파일이 없거나 schema_version이 낮으면 테이블/인덱스를 생성/갱신한다."""
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as session:
        row = session.get(SchemaVersion, 1)
        if row is None:
            session.add(SchemaVersion(id=1, version=SCHEMA_VERSION))
            session.commit()
            logger.info("schema_version initialized to %s", SCHEMA_VERSION)
        elif row.version < SCHEMA_VERSION:
            logger.info("schema_version migrating %s -> %s", row.version, SCHEMA_VERSION)
            with engine.begin() as conn:
                for version in range(row.version + 1, SCHEMA_VERSION + 1):
                    migration = _MIGRATIONS.get(version)
                    if migration is not None:
                        migration(conn)
            row.version = SCHEMA_VERSION
            session.commit()
    _mark_orphaned_discovery_runs_failed()


def get_session() -> Session:
    return SessionLocal()


def db_session_dependency():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def vacuum_into(dest_path: str) -> None:
    """15.1절 '온라인 백업 또는 파일 Snapshot' 요구사항 구현 (SQLite 전용)."""
    if engine.dialect.name != "sqlite":
        raise NotImplementedError(
            "VACUUM INTO는 SQLite 전용 기능입니다. PostgreSQL 백엔드에서는 pg_dump 등 별도 백업 도구를 사용하세요."
        )
    with engine.connect() as conn:
        conn.execute(text("VACUUM INTO :dest"), {"dest": dest_path})
