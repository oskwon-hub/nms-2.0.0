from __future__ import annotations

from app import config


def test_describe_database_shows_sqlite_path_by_default(monkeypatch):
    monkeypatch.setattr(config, "DB_BACKEND", "sqlite")
    monkeypatch.setattr(config, "DB_PATH", "/data/nms.db")
    assert config.describe_database() == "/data/nms.db"


def test_describe_database_hides_password_for_postgresql(monkeypatch):
    monkeypatch.setattr(config, "DB_BACKEND", "postgresql")
    monkeypatch.setattr(config, "PG_USER", "nms")
    monkeypatch.setattr(config, "PG_PASSWORD", "super-secret-password")
    monkeypatch.setattr(config, "PG_HOST", "localhost")
    monkeypatch.setattr(config, "PG_PORT", "5432")
    monkeypatch.setattr(config, "PG_DB", "nms2db")

    result = config.describe_database()

    assert result == "postgresql://nms@localhost:5432/nms2db"
    assert "super-secret-password" not in result
