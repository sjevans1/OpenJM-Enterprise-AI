"""VS8 Workstream F: bounded, tenant-safe, auditable retention.

Retention must delete only operational transient rows, must be bounded, must
default to a dry run, and must never touch audit evidence, report history or
customer source data.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.core.tenancy import LEGACY_PRINCIPAL_ID, LEGACY_TENANT_ID
from app.models import (
    AuditRecord,
    AuthSession,
    Notification,
    Schedule,
    ScheduleRun,
)
from app.services import retention


def _old(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


async def _seed(file_db) -> None:
    async with file_db() as db:
        schedule = Schedule(
            tenant_id=LEGACY_TENANT_ID,
            owner_principal_id=LEGACY_PRINCIPAL_ID,
            name="nightly",
            schedule_type="connector_sync",
            operation="connector.sync",
            interval_seconds=60,
            status="active",
            enabled=True,
        )
        db.add(schedule)
        await db.flush()
        db.add_all(
            [
                AuthSession(
                    token_hash="old-session",
                    principal_id=LEGACY_PRINCIPAL_ID,
                    tenant_id=LEGACY_TENANT_ID,
                    expires_at=_old(90),
                ),
                AuthSession(
                    token_hash="fresh-session",
                    principal_id=LEGACY_PRINCIPAL_ID,
                    tenant_id=LEGACY_TENANT_ID,
                    expires_at=datetime.now(timezone.utc) + timedelta(days=1),
                ),
                ScheduleRun(
                    tenant_id=LEGACY_TENANT_ID,
                    schedule_id=schedule.id,
                    scheduled_for=_old(120),
                    status="succeeded",
                    claim_token="tok-1",
                    finished_at=_old(120),
                ),
                Notification(
                    tenant_id=LEGACY_TENANT_ID,
                    principal_id=LEGACY_PRINCIPAL_ID,
                    category="report",
                    subject="done",
                    body="body",
                    status="delivered",
                    created_at=_old(200),
                ),
                # This audit row must survive any retention run.
                AuditRecord(
                    tenant_id=LEGACY_TENANT_ID,
                    action="authorize:data.read",
                    decision="allow",
                    created_at=_old(400),
                ),
            ]
        )
        await db.commit()


async def _count(db, model) -> int:
    return int((await db.execute(select(func.count()).select_from(model))).scalar() or 0)


async def test_dry_run_deletes_nothing(file_db) -> None:
    await _seed(file_db)
    async with file_db() as db:
        outcome = await retention.plan_retention(db, settings=_retention_settings())
    assert outcome.dry_run is True
    assert outcome.counts["expired_sessions"] == 1
    assert outcome.counts["scheduler_run_history"] == 1
    assert outcome.counts["notification_history"] == 1
    async with file_db() as db:
        assert await _count(db, AuthSession) == 2
        assert await _count(db, ScheduleRun) == 1


async def test_apply_deletes_only_eligible(file_db) -> None:
    await _seed(file_db)
    async with file_db() as db:
        outcome = await retention.apply_retention(
            db, dry_run=False, settings=_retention_settings()
        )
    assert outcome.total >= 3
    async with file_db() as db:
        # Fresh session survives; old session, old run and old notification go.
        assert await _count(db, AuthSession) == 1
        assert await _count(db, ScheduleRun) == 0
        assert await _count(db, Notification) == 0
        # Audit evidence is never eligible.
        assert await _count(db, AuditRecord) >= 1


async def test_retention_is_bounded(file_db) -> None:
    async with file_db() as db:
        db.add_all(
            [
                AuthSession(
                    token_hash=f"old-{i}",
                    principal_id=LEGACY_PRINCIPAL_ID,
                    tenant_id=LEGACY_TENANT_ID,
                    expires_at=_old(90),
                )
                for i in range(5)
            ]
        )
        await db.commit()
    async with file_db() as db:
        outcome = await retention.apply_retention(
            db, dry_run=False, limit=2, settings=_retention_settings()
        )
    assert outcome.counts["expired_sessions"] == 2


async def test_retention_records_audit(file_db) -> None:
    await _seed(file_db)
    async with file_db() as db:
        before = await _count(db, AuditRecord)
    async with file_db() as db:
        await retention.apply_retention(db, dry_run=False, settings=_retention_settings())
    async with file_db() as db:
        after = await _count(db, AuditRecord)
    assert after > before  # a retention.apply audit row was written


def test_never_delete_set_is_documented() -> None:
    joined = " ".join(retention.NEVER_DELETE)
    assert "audit_records" in joined
    assert "report" in joined
    assert "documents" in joined


def _retention_settings():
    from app.core.config import get_settings

    return get_settings().model_copy(
        update={
            "retention_enabled": True,
            "retention_sessions_days": 30,
            "retention_scheduler_runs_days": 30,
            "retention_notification_history_days": 90,
        }
    )
