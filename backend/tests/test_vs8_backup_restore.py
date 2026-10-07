"""VS8 Workstream E: application-consistent backup, verification and restore."""

from __future__ import annotations

import json
import sqlite3

import pytest

from app.core.config import Settings
from app.migrations_runner import adopt_and_upgrade
from app.ops.backup import (
    BackupError,
    create_backup,
    restore_backup,
    verify_backup,
)


def _settings(tmp_path, name="src") -> Settings:
    root = tmp_path / name
    (root / "uploads").mkdir(parents=True)
    return Settings(
        deployment_profile="development",
        database_url=f"sqlite+aiosqlite:///{root / 'openjm.db'}",
        upload_dir=root / "uploads",
        vector_path=root / "vector",
        credential_key_file=root / "credentials.key",
        backup_dir=tmp_path / "backups",
    )


def _make_populated_db(url: str, upload_dir, key_file) -> None:
    db_path = url.split(":///", 1)[1]
    adopt_and_upgrade(url)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO conversations (id, user_id, title, created_at, updated_at) "
        "VALUES ('c1','local-admin','Seeded', '2026-01-01 00:00:00.000000', "
        "'2026-01-01 00:00:00.000000')"
    )
    conn.commit()
    conn.close()
    (upload_dir / "policy.txt").write_text("threshold USD 300", encoding="utf-8")
    key_file.write_bytes(b"a" * 44 + b"\n")


def test_backup_verify_restore_roundtrip(tmp_path) -> None:
    cfg = _settings(tmp_path)
    _make_populated_db(cfg.database_url, cfg.upload_dir, cfg.credential_key_file)

    backup = create_backup(settings=cfg)
    verification = verify_backup(backup)
    assert verification["ok"], verification["problems"]
    assert verification["file_count"] >= 3
    manifest = json.loads((backup / "manifest.json").read_text())
    assert manifest["database_engine"] == "sqlite"
    assert manifest["schema_revision"]

    target = tmp_path / "restored"
    target_db = f"sqlite+aiosqlite:///{target / 'openjm.db'}"
    result = restore_backup(
        backup,
        target_database_url=target_db,
        target_data_dir=target / "uploads",
    )
    assert result["verification"]["ok"]

    restored_db = target / "openjm.db"
    conn = sqlite3.connect(restored_db)
    rows = conn.execute("SELECT id FROM conversations").fetchall()
    conn.close()
    assert ("c1",) in rows
    assert (target / "uploads" / "policy.txt").exists()


def test_verify_detects_tampering(tmp_path) -> None:
    cfg = _settings(tmp_path)
    _make_populated_db(cfg.database_url, cfg.upload_dir, cfg.credential_key_file)
    backup = create_backup(settings=cfg)

    db_copy = backup / "db" / "openjm.db"
    with sqlite3.connect(db_copy) as conn:
        conn.execute("INSERT INTO conversations (id, user_id, title, created_at, updated_at) "
                     "VALUES ('x','u','t','2026-01-01 00:00:00.000000','2026-01-01 00:00:00.000000')")
        conn.commit()

    verification = verify_backup(backup)
    assert not verification["ok"]
    assert any("checksum mismatch" in p for p in verification["problems"])


def test_restore_refuses_nonempty_target(tmp_path) -> None:
    cfg = _settings(tmp_path)
    _make_populated_db(cfg.database_url, cfg.upload_dir, cfg.credential_key_file)
    backup = create_backup(settings=cfg)

    target = tmp_path / "restored"
    (target / "uploads").mkdir(parents=True)
    (target / "uploads" / "existing.txt").write_text("do not clobber", encoding="utf-8")
    with pytest.raises(BackupError):
        restore_backup(
            backup,
            target_database_url=f"sqlite+aiosqlite:///{target / 'openjm.db'}",
            target_data_dir=target / "uploads",
        )


def test_restore_refuses_unverified_backup(tmp_path) -> None:
    cfg = _settings(tmp_path)
    _make_populated_db(cfg.database_url, cfg.upload_dir, cfg.credential_key_file)
    backup = create_backup(settings=cfg)
    (backup / "db" / "openjm.db").write_bytes(b"corrupt")
    with pytest.raises(BackupError):
        restore_backup(
            backup,
            target_database_url=f"sqlite+aiosqlite:///{tmp_path / 'r' / 'openjm.db'}",
            target_data_dir=tmp_path / "r" / "uploads",
        )


def test_config_snapshot_never_inlines_secret_values(tmp_path) -> None:
    cfg = _settings(tmp_path).model_copy(update={"model_api_key": "top-secret-key"})
    _make_populated_db(cfg.database_url, cfg.upload_dir, cfg.credential_key_file)
    backup = create_backup(settings=cfg)
    snapshot = (backup / "config" / "effective-config.json").read_text()
    assert "top-secret-key" not in snapshot
    assert "model_api_key_present" in snapshot


def test_libpq_url_strips_sqlalchemy_driver_suffix() -> None:
    from app.ops.backup import _libpq_url

    assert _libpq_url("postgresql+asyncpg://u@/db?host=/x") == "postgresql://u@/db?host=/x"
    assert _libpq_url("postgresql+psycopg://u@h/db") == "postgresql://u@h/db"
    assert _libpq_url("postgresql://u@h/db") == "postgresql://u@h/db"
