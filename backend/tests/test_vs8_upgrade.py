"""VS8 Workstream G: upgrade preflight/postflight and unknown-future refusal."""

from __future__ import annotations

import sqlite3

import pytest

from app.core.config import Settings
from app.migrations_runner import (
    UnknownSchemaRevisionError,
    assert_known_schema_revision,
    current_revision,
    script_heads,
)
from app.ops.upgrade import backup_recommendation, preflight_upgrade, run_upgrade


def _settings(tmp_path) -> Settings:
    return Settings(
        deployment_profile="development",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'openjm.db'}",
        upload_dir=tmp_path / "uploads",
        vector_path=tmp_path / "vector",
        credential_key_file=tmp_path / "credentials.key",
        backup_dir=tmp_path / "backups",
    )


def test_fresh_database_preflight_ok(tmp_path) -> None:
    cfg = _settings(tmp_path)
    report = preflight_upgrade(cfg)
    assert report.ok
    assert report.schema_before is None
    assert report.schema_head == script_heads(cfg.database_url)[0]


def test_upgrade_from_empty_reaches_head(tmp_path) -> None:
    cfg = _settings(tmp_path)
    report = run_upgrade(cfg)
    assert report.ok, report.render()
    assert report.postflight_revision in script_heads(cfg.database_url)
    assert current_revision(cfg.database_url) == report.schema_head


def test_unknown_future_revision_refuses_startup(tmp_path) -> None:
    cfg = _settings(tmp_path)
    run_upgrade(cfg)  # build a real schema first
    db_path = cfg.database_url.split(":///", 1)[1]
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE alembic_version SET version_num = '9999_from_the_future'")
        conn.commit()

    with pytest.raises(UnknownSchemaRevisionError):
        assert_known_schema_revision(cfg.database_url)

    report = preflight_upgrade(cfg)
    assert not report.ok
    assert any("unknown schema revision" in e for e in report.errors)


def test_upgrade_is_idempotent(tmp_path) -> None:
    cfg = _settings(tmp_path)
    first = run_upgrade(cfg)
    second = run_upgrade(cfg)
    assert first.ok and second.ok
    assert second.postflight_revision == second.schema_head


def test_backup_recommendation_names_a_command() -> None:
    text = backup_recommendation()
    assert "openjm_ops.py backup" in text or "openjm_backup" in text
    assert "restore" in text.lower()
