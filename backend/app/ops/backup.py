"""VS8 authoritative backup and restore (Workstream E).

The authoritative set is declared once, here, and classified:

* **authoritative** — required to reconstruct the deployment: the application
  metadata database, uploaded/local documents, connector-owned cached material,
  the credential-vault key material, and a configuration snapshot;
* **reproducible** — can be rebuilt: the vector/Knowledge index (rebuilt from
  documents) is backed up opportunistically but is not required for recovery;
* **ephemeral** — should not be backed up: process state, leases, in-flight
  synchronisation cursors.

Backups are application-consistent for SQLite via the online backup API (a
transactionally consistent snapshot while the app keeps serving) and via
``pg_dump`` for PostgreSQL. Every backup carries a manifest with a schema
revision, product/release identity, per-file SHA-256 and byte counts, and the
restore verifies every checksum before writing anything.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import Settings, get_settings
from app.version import PRODUCT_VERSION

MANIFEST_NAME = "manifest.json"
MANIFEST_VERSION = 1

# Data-class taxonomy, declared so a reviewer sees the decision.
AUTHORITATIVE = "authoritative"
REPRODUCIBLE = "reproducible"
EPHEMERAL = "ephemeral"

DATA_CLASSES = {
    "application_metadata": AUTHORITATIVE,
    "uploads": AUTHORITATIVE,
    "credential_key": AUTHORITATIVE,
    "configuration_snapshot": AUTHORITATIVE,
    "connector_cached_material": AUTHORITATIVE,
    "vector_index": REPRODUCIBLE,
    "leases_and_cursors": EPHEMERAL,
}


class BackupError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sqlite_online_backup(source: Path, destination: Path) -> None:
    """Transactionally consistent copy of a live SQLite database."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_conn = sqlite3.connect(str(source))
    try:
        dest_conn = sqlite3.connect(str(destination))
        try:
            source_conn.backup(dest_conn)
        finally:
            dest_conn.close()
    finally:
        source_conn.close()


def _database_is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def _sqlite_path(url: str) -> Path:
    # sqlite+aiosqlite:///abs/path  ->  /abs/path
    _, _, rest = url.partition(":///")
    return Path(rest)


@dataclass
class BackupManifest:
    created_at: str
    product: str
    version: str
    release_id: str | None
    profile: str
    schema_revision: str | None
    database_engine: str
    files: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "manifest_version": MANIFEST_VERSION,
            "created_at": self.created_at,
            "product": self.product,
            "version": self.version,
            "release_id": self.release_id,
            "profile": self.profile,
            "schema_revision": self.schema_revision,
            "database_engine": self.database_engine,
            "classes": DATA_CLASSES,
            "files": self.files,
        }


def _record(manifest: BackupManifest, root: Path, path: Path, data_class: str) -> None:
    manifest.files.append(
        {
            "path": str(path.relative_to(root)),
            "sha256": _sha256(path),
            "bytes": path.stat().st_size,
            "class": data_class,
        }
    )


def create_backup(
    destination: Path | None = None,
    *,
    settings: Settings | None = None,
    include_vector: bool = True,
) -> Path:
    """Create a backup and return its directory.

    The destination defaults to ``<backup_dir>/<UTC timestamp>``.
    """
    cfg = settings or get_settings()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = Path(destination) if destination else (cfg.backup_dir / stamp)
    if target.exists() and any(target.iterdir()):
        raise BackupError(f"backup destination {target} is not empty")
    target.mkdir(parents=True, exist_ok=True)

    from app.migrations_runner import current_revision

    database_url = cfg.database_url
    engine_name = "sqlite" if _database_is_sqlite(database_url) else "postgresql"
    manifest = BackupManifest(
        created_at=datetime.now(timezone.utc).isoformat(),
        product=cfg.product_name,
        version=PRODUCT_VERSION,
        release_id=cfg.release_id or None,
        profile=cfg.deployment_profile,
        schema_revision=current_revision(database_url),
        database_engine=engine_name,
    )

    # 1. Application metadata database (application-consistent).
    if engine_name == "sqlite":
        source = _sqlite_path(database_url)
        if not source.exists():
            raise BackupError(f"application database {source} does not exist")
        db_target = target / "db" / "openjm.db"
        _sqlite_online_backup(source, db_target)
        _record(manifest, target, db_target, DATA_CLASSES["application_metadata"])
    else:
        dump = target / "db" / "openjm.pg.sql"
        dump.parent.mkdir(parents=True, exist_ok=True)
        try:
            with dump.open("wb") as handle:
                subprocess.run(
                    ["pg_dump", "--format=plain", "--no-owner", database_url],
                    check=True,
                    stdout=handle,
                )
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            raise BackupError(f"pg_dump failed: {exc}") from exc
        _record(manifest, target, dump, DATA_CLASSES["application_metadata"])

    # 2. Uploaded / local documents (augments connector-cached material).
    if cfg.upload_dir.exists():
        archive = target / "uploads.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(cfg.upload_dir, arcname="uploads")
        _record(manifest, target, archive, DATA_CLASSES["uploads"])

    # 3. Credential-vault key material: required to decrypt connector/infra
    #    secrets after restore. Backed up as a file, never inlined in JSON.
    if cfg.credential_key_file.exists():
        key_target = target / "keys" / cfg.credential_key_file.name
        key_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cfg.credential_key_file, key_target)
        _record(manifest, target, key_target, DATA_CLASSES["credential_key"])

    # 4. Configuration snapshot: non-secret effective configuration, so a
    #    deployment can be reconstructed without the original instance.
    snapshot = target / "config" / "effective-config.json"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_text(json.dumps(_safe_config_snapshot(cfg), indent=2), encoding="utf-8")
    _record(manifest, target, snapshot, DATA_CLASSES["configuration_snapshot"])

    # 5. Vector/Knowledge index: reproducible, backed up opportunistically.
    if include_vector and cfg.vector_path.exists() and any(cfg.vector_path.iterdir()):
        archive = target / "vector.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(cfg.vector_path, arcname="vector")
        _record(manifest, target, archive, DATA_CLASSES["vector_index"])

    (target / MANIFEST_NAME).write_text(
        json.dumps(manifest.to_dict(), indent=2), encoding="utf-8"
    )
    return target


# Configuration keys that are safe to persist in a backup snapshot. Secret
# values are recorded as a presence flag, never as a value.
_SECRET_KEYS = {
    "model_api_key",
    "credential_encryption_key",
    "oidc_client_secret",
}
_SNAPSHOT_KEYS = (
    "deployment_profile",
    "database_url",
    "upload_dir",
    "vector_path",
    "vector_collection",
    "embedding_model",
    "knowledge_enabled",
    "model_base_url",
    "model_name",
    "model_provider_mode",
    "model_provider_fallback",
    "auth_mode",
    "oidc_issuer",
    "oidc_client_id",
    "oidc_redirect_uri",
    "session_ttl_seconds",
    "cors_origins",
    "trusted_hosts",
    "trust_proxy_headers",
    "rate_limit_enabled",
    "retention_enabled",
    "product_name",
    "organization_name",
    "release_id",
)


def _safe_config_snapshot(cfg: Settings) -> dict:
    snapshot: dict[str, object] = {}
    for key in _SNAPSHOT_KEYS:
        value = getattr(cfg, key, None)
        snapshot[key] = str(value) if isinstance(value, Path) else value
    for secret in _SECRET_KEYS:
        raw = getattr(cfg, secret, "") or ""
        snapshot[f"{secret}_present"] = bool(str(raw).strip())
    return snapshot


def verify_backup(backup: Path) -> dict:
    """Verify a backup's manifest and every recorded checksum."""
    backup = Path(backup)
    manifest_path = backup / MANIFEST_NAME
    if not manifest_path.exists():
        raise BackupError(f"no manifest at {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    problems: list[str] = []
    for entry in manifest.get("files", []):
        path = backup / entry["path"]
        if not path.exists():
            problems.append(f"missing file: {entry['path']}")
            continue
        if _sha256(path) != entry["sha256"]:
            problems.append(f"checksum mismatch: {entry['path']}")
        elif path.stat().st_size != entry["bytes"]:
            problems.append(f"size mismatch: {entry['path']}")
    return {
        "ok": not problems,
        "manifest_version": manifest.get("manifest_version"),
        "schema_revision": manifest.get("schema_revision"),
        "file_count": len(manifest.get("files", [])),
        "problems": problems,
    }


def restore_backup(
    backup: Path,
    *,
    target_database_url: str,
    target_data_dir: Path,
    force: bool = False,
) -> dict:
    """Restore a verified backup into a clean, isolated target.

    Refuses to run when verification fails, and refuses to write into a
    non-empty data directory unless *force* is given.
    """
    backup = Path(backup)
    verification = verify_backup(backup)
    if not verification["ok"]:
        raise BackupError(f"backup failed verification: {verification['problems']}")

    target_data_dir = Path(target_data_dir)
    if target_data_dir.exists() and any(target_data_dir.iterdir()) and not force:
        raise BackupError(f"restore target {target_data_dir} is not empty; pass force=True")

    manifest = json.loads((backup / MANIFEST_NAME).read_text(encoding="utf-8"))
    restored: list[str] = []

    if not _database_is_sqlite(target_database_url):
        raise BackupError("restore currently supports a SQLite target in this toolkit")

    db_source = backup / "db" / "openjm.db"
    db_target = _sqlite_path(target_database_url)
    db_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(db_source, db_target)
    restored.append(str(db_target))

    uploads_archive = backup / "uploads.tar.gz"
    if uploads_archive.exists():
        with tarfile.open(uploads_archive, "r:gz") as tar:
            tar.extractall(target_data_dir.parent, filter="data")
        restored.append(str(target_data_dir.parent / "uploads"))

    key_dir = backup / "keys"
    if key_dir.exists():
        target_key_root = target_data_dir.parent / "keys"
        target_key_root.mkdir(parents=True, exist_ok=True)
        for key_file in key_dir.iterdir():
            shutil.copy2(key_file, target_key_root / key_file.name)
            restored.append(str(target_key_root / key_file.name))

    vector_archive = backup / "vector.tar.gz"
    if vector_archive.exists():
        with tarfile.open(vector_archive, "r:gz") as tar:
            tar.extractall(target_data_dir.parent, filter="data")
        restored.append(str(target_data_dir.parent / "vector"))

    return {
        "restored": restored,
        "schema_revision": manifest.get("schema_revision"),
        "verification": verification,
    }


__all__ = [
    "AUTHORITATIVE",
    "DATA_CLASSES",
    "EPHEMERAL",
    "REPRODUCIBLE",
    "BackupError",
    "BackupManifest",
    "create_backup",
    "restore_backup",
    "verify_backup",
]
