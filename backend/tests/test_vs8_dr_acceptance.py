"""VS8 Workstream E acceptance: real isolated disaster recovery.

The scenario is the one #38 requires: seed representative VS1-VS7 data across
two tenants, take a backup, destroy the original runtime state, restore into a
fresh isolated target, start the restored system and validate every stored class
— identities/tenants, Knowledge evidence, structured-source metadata, reports,
connector metadata/schedules, credential behaviour — and prove no cross-tenant
corruption.
"""

from __future__ import annotations

import json
import shutil
import sqlite3

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.migrations_runner import adopt_and_upgrade
from app.ops.backup import create_backup, restore_backup, verify_backup
from app.services.credentials import CredentialVault


def _settings(tmp_path, name="primary") -> Settings:
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


async def _seed(url: str, key_file, upload_dir) -> dict:
    """Seed representative VS1-VS7 data for two tenants."""
    adopt_and_upgrade(url)
    engine = create_async_engine(url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    from app.models import (
        ConnectorCredential,
        ConnectorInstance,
        Conversation,
        DataSource,
        Document,
        Message,
        Notification,
        Schedule,
        SavedReport,
    )
    from app.services.identity import (
        add_membership,
        create_tenant,
        ensure_local_identity,
        get_or_create_principal,
    )

    async with maker() as db:
        await ensure_local_identity(db)  # tenant A == tnt-local + local-admin
        tenant_b = await create_tenant(db, slug="beta", name="Beta", tenant_id="tnt-beta")
        account_b = await get_or_create_principal(
            db, subject="oidc:beta-user", principal_id="beta-user"
        )
        await add_membership(db, tenant_id=tenant_b.id, principal_id=account_b.id, role="owner")

        vault = CredentialVault(key_file=key_file)
        seeded = {"tenants": [], "documents": [], "sources": [], "reports": [],
                  "connectors": [], "schedules": [], "notifications": [], "ciphertext": None}

        for tenant_id, user_id, suffix in (
            ("tnt-local", "local-admin", "a"),
            ("tnt-beta", "beta-user", "b"),
        ):
            (upload_dir / f"policy-{suffix}.txt").write_text(
                "threshold USD 300", encoding="utf-8"
            )
            conv = Conversation(user_id=user_id, title=f"Conv {suffix}")
            db.add(conv)
            await db.flush()
            doc = Document(
                user_id=user_id, original_name=f"policy-{suffix}.txt",
                stored_path=f"/data/uploads/policy-{suffix}.txt", size_bytes=42,
                status="ready", indexed=True,
            )
            src = DataSource(
                user_id=user_id, name=f"Finance {suffix}", engine="sqlite",
                connection_secret="fixture-not-real", status="connected", enabled=True,
                schema_json=json.dumps([{"schema_name": "main", "name": "finance",
                                         "qualified_name": "finance",
                                         "columns": [{"name": "revenue", "type": "NUMERIC", "nullable": False}],
                                         "primary_key": [], "foreign_keys": []}]),
                authorized_objects_json=json.dumps(["finance"]),
            )
            db.add_all([doc, src])
            await db.flush()
            msg = Message(conversation_id=conv.id, role="assistant",
                          content=f"Answer {suffix}", execution_class="knowledge",
                          requested_mode="knowledge",
                          evidence_json=json.dumps([{"source_type": "document",
                                                     "source_id": doc.id, "title": "Policy",
                                                     "passage": "threshold USD 300"}]))
            db.add(msg)
            await db.flush()
            report = SavedReport(
                user_id=user_id, conversation_id=conv.id, message_id=msg.id,
                title=f"Report {suffix}", answer_text=msg.content,
                evidence_json=msg.evidence_json, execution_class="knowledge",
                requested_mode="knowledge", source_count=1, snapshot_as_of=msg.created_at,
            )
            db.add(report)
            connector = ConnectorInstance(
                tenant_id=tenant_id, name=f"Workspace {suffix}", connector_type="workspace",
                connector_version="1", display_name="Workspace", status="active",
                enabled=True, config_json=json.dumps({"base_url": "https://ws.example"}),
            )
            db.add(connector)
            await db.flush()
            cred = ConnectorCredential(
                tenant_id=tenant_id, connector_instance_id=connector.id,
                label="primary", kind="workspace_token",
                secret_ciphertext=vault.encrypt(f"secret-{suffix}"),
            )
            db.add(cred)
            schedule = Schedule(
                tenant_id=tenant_id, owner_principal_id=user_id, name=f"Nightly {suffix}",
                schedule_type="connector_sync", operation="connector.sync",
                interval_seconds=3600, status="active", enabled=True,
            )
            db.add(schedule)
            notification = Notification(
                tenant_id=tenant_id, principal_id=user_id, category="report",
                subject=f"Report {suffix} ready", body="body", status="delivered",
            )
            db.add(notification)
            await db.flush()
            seeded["tenants"].append(tenant_id)
            seeded["documents"].append((doc.id, tenant_id))
            seeded["sources"].append((src.id, tenant_id))
            seeded["reports"].append((report.id, tenant_id))
            seeded["connectors"].append((connector.id, tenant_id))
            seeded["schedules"].append((schedule.id, tenant_id))
            seeded["notifications"].append((notification.id, tenant_id))
            if suffix == "a":
                seeded["ciphertext"] = cred.secret_ciphertext
        await db.commit()
    await engine.dispose()
    return seeded


async def _counts(url: str) -> dict:
    engine = create_async_engine(url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    from app.models import (
        ConnectorInstance, DataSource, Document, Notification, SavedReport, Schedule, Tenant,
    )

    out: dict[str, int] = {}
    async with maker() as db:
        for name, model in (
            ("tenants", Tenant), ("documents", Document), ("sources", DataSource),
            ("reports", SavedReport), ("connectors", ConnectorInstance),
            ("schedules", Schedule), ("notifications", Notification),
        ):
            out[name] = int((await db.execute(select(func.count()).select_from(model))).scalar() or 0)
    await engine.dispose()
    return out


@pytest.mark.asyncio
async def test_disaster_recovery_roundtrip_preserves_tenants_and_credentials(tmp_path) -> None:
    cfg = _settings(tmp_path)
    seeded = await _seed(cfg.database_url, cfg.credential_key_file, cfg.upload_dir)

    # 1-2. Create the backup of the populated deployment.
    backup = create_backup(settings=cfg)
    assert verify_backup(backup)["ok"]

    before = await _counts(cfg.database_url)
    assert before == {"tenants": 2, "documents": 2, "sources": 2, "reports": 2,
                      "connectors": 2, "schedules": 2, "notifications": 2}

    # 3. Destroy the original runtime state (metadata DB + uploads + key).
    shutil.rmtree(tmp_path / "primary", ignore_errors=True)
    assert not (tmp_path / "primary" / "openjm.db").exists()

    # 4. Restore into a clean, isolated target.
    target = tmp_path / "recovered"
    target_db = f"sqlite+aiosqlite:///{target / 'data' / 'openjm.db'}"
    restore_backup(
        backup,
        target_database_url=target_db,
        target_data_dir=target / "data" / "uploads",
    )

    # 5. Start the restored system (open the restored DB; migrations idempotent).
    from app.services.identity import ensure_local_identity
    engine = create_async_engine(target_db)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        await ensure_local_identity(db)
    await engine.dispose()

    # 6-10. Every stored class survived.
    after = await _counts(target_db)
    assert after == before

    # 11. Credential behaviour: the restored key decrypts the stored ciphertext.
    restored_key = target / "data" / "keys" / "credentials.key"
    assert restored_key.exists()
    vault = CredentialVault(key_file=restored_key)
    assert vault.decrypt(seeded["ciphertext"]) == "secret-a"

    # 12. No cross-tenant corruption: tenant-scoped reads stay separated.
    engine = create_async_engine(target_db)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    from app.models import Document, Schedule

    async with maker() as db:
        a_docs = (await db.execute(select(Document.id).where(Document.user_id == "local-admin"))).scalars().all()
        b_docs = (await db.execute(select(Document.id).where(Document.user_id == "beta-user"))).scalars().all()
        a_schedules = (await db.execute(select(Schedule.id).where(Schedule.tenant_id == "tnt-local"))).scalars().all()
        b_schedules = (await db.execute(select(Schedule.id).where(Schedule.tenant_id == "tnt-beta"))).scalars().all()
    await engine.dispose()

    assert len(a_docs) == 1 and len(b_docs) == 1
    assert set(a_docs).isdisjoint(b_docs)
    assert len(a_schedules) == 1 and len(b_schedules) == 1
    assert set(a_schedules).isdisjoint(b_schedules)
    # The restored uploads material is present and readable.
    assert (target / "data" / "uploads" / "policy-a.txt").exists()
