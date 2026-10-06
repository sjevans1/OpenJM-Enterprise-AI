"""VS7 bounded scheduler acceptance.

These tests drive the real scheduler against a real (SQLite) database. The
properties that matter are the negative ones: a revoked membership, a downgraded
role, a disabled schedule or a disabled connector must stop a scheduled run, and
a restart or a concurrent claimer must not run an occurrence twice.

The connector tests use a counting in-process connector so "the operation ran
once" is an observed number rather than an inference.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from app.core.connectors import (
    AuthorizationBehavior,
    ConnectorCapability,
    ConnectorTypeSpec,
    DeclaredOperation,
    OperationClass,
    connector_registry,
)
from app.core.permissions import Permission
from app.core.tenancy import LEGACY_PRINCIPAL_ID, LEGACY_TENANT_ID
from app.models import (
    AuditRecord,
    ConnectorCredential,
    ConnectorInstance,
    ConnectorSyncRun,
    Notification,
    Schedule,
    ScheduleRun,
    Tenant,
)
from app.services import identity as identity_service
from app.services import notifications, scheduler
from app.services.connectors import service as connector_service
from app.services.connectors.base import Connector, ConnectorContext
from app.services.credentials import credential_vault

TENANT_A = "tnt-sched-a"
TENANT_B = "tnt-sched-b"
PRINCIPAL_A = "sched-pa"
PRINCIPAL_B = "sched-pb"

FAKE_KEY = "schedtest@1.0.0"


# ---------------------------------------------------------------------------
# A counting connector whose initial sync does exactly one bounded page read
# ---------------------------------------------------------------------------


class _CountingConnector(Connector):
    type_id = "schedtest"
    version = "1.0.0"

    def __init__(self) -> None:
        self.list_calls = 0

    async def test_connection(self, ctx: ConnectorContext) -> dict:
        return {"ok": True, "detail": "ok"}

    async def list_resources(
        self, ctx: ConnectorContext, *, limit: int, cursor: str | None = None
    ):
        self.list_calls += 1
        return [], None

    async def fetch_resource(self, ctx: ConnectorContext, external_id: str):
        return None

    async def check_user_access(
        self, ctx: ConnectorContext, *, external_user_id: str, external_id: str
    ) -> bool:
        return False


def _fake_spec() -> ConnectorTypeSpec:
    return ConnectorTypeSpec(
        type_id="schedtest",
        version="1.0.0",
        display_name="Scheduler Test",
        description="In-process connector used to observe scheduled execution.",
        capabilities=frozenset({ConnectorCapability.DOCUMENTS}),
        operations=(
            DeclaredOperation(
                name="schedtest.list_resources",
                capability=ConnectorCapability.DOCUMENTS,
                operation_class=OperationClass.READ,
                description="List resources.",
                required_permission=Permission.CONNECTOR_READ.value,
            ),
        ),
        authorization_behavior=AuthorizationBehavior.CONNECTOR_SCOPED,
        requires_user_mapping=False,
        credential_kind="bearer_token",
        credential_fields=("token",),
        credential_rotation="replace",
        event_support=False,
        reconciliation_support=False,
        incremental_support=False,
        supports_test_connection=True,
        timeout_seconds=30,
        max_retries=3,
        initial_sync_limit=100,
    )


@pytest.fixture
def counting_connector(monkeypatch):
    # Give the vault an ephemeral key so the test never writes a key file.
    monkeypatch.setattr(credential_vault, "_key", Fernet.generate_key())
    implementation = _CountingConnector()
    if not connector_registry.has("schedtest", "1.0.0"):
        connector_registry.register(_fake_spec())
    connector_service.register_implementation(implementation)
    try:
        yield implementation
    finally:
        connector_registry.unregister(FAKE_KEY)
        connector_service._IMPLEMENTATIONS.pop(FAKE_KEY, None)


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


async def _seed(maker) -> None:
    async with maker() as db:
        db.add_all(
            [
                Tenant(id=TENANT_A, slug="sched-a", name="Scheduler A", status="active"),
                Tenant(id=TENANT_B, slug="sched-b", name="Scheduler B", status="active"),
            ]
        )
        await db.flush()
        await identity_service.get_or_create_principal(
            db, subject="sub-sched-a", principal_id=PRINCIPAL_A
        )
        await identity_service.get_or_create_principal(
            db, subject="sub-sched-b", principal_id=PRINCIPAL_B
        )
        await identity_service.add_membership(
            db, tenant_id=TENANT_A, principal_id=PRINCIPAL_A, role="editor"
        )
        await identity_service.add_membership(
            db, tenant_id=TENANT_B, principal_id=PRINCIPAL_B, role="owner"
        )
        await db.commit()


async def _make_instance(maker, tenant_id: str, *, enabled: bool, name: str) -> str:
    async with maker() as db:
        instance = ConnectorInstance(
            tenant_id=tenant_id,
            name=name,
            connector_type="schedtest",
            connector_version="1.0.0",
            display_name="Scheduler Test",
            status="active" if enabled else "disabled",
            enabled=enabled,
        )
        db.add(instance)
        await db.flush()
        credential = ConnectorCredential(
            tenant_id=tenant_id,
            connector_instance_id=instance.id,
            label="primary",
            kind="bearer_token",
            secret_ciphertext=credential_vault.encrypt(json.dumps({"token": "fake"})),
            status="active",
            version=1,
        )
        db.add(credential)
        await db.flush()
        instance.credential_id = credential.id
        await db.commit()
        return instance.id


async def _create_schedule(maker, **kwargs) -> str:
    async with maker() as db:
        schedule = await scheduler.create_schedule(db, **kwargs)
        await db.commit()
        return schedule.id


async def _occurrence(maker, schedule_id: str):
    async with maker() as db:
        schedule = await db.get(Schedule, schedule_id)
        return scheduler._as_utc(schedule.next_run_at)


def _connector_kwargs(tenant_id, owner, name, instance_id, interval=60):
    return {
        "tenant_id": tenant_id,
        "owner_principal_id": owner,
        "name": name,
        "schedule_type": "connector_sync",
        "operation": "connector.sync",
        "interval_seconds": interval,
        "target": {"connector_instance_id": instance_id},
    }


async def _single_run(db, schedule_id):
    result = await db.execute(
        select(ScheduleRun).where(ScheduleRun.schedule_id == schedule_id)
    )
    rows = list(result.scalars().all())
    assert len(rows) == 1, f"expected exactly one schedule run, found {len(rows)}"
    return rows[0]


# ---------------------------------------------------------------------------
# Registry and creation validation
# ---------------------------------------------------------------------------


def test_registered_operations_are_exactly_the_closed_set():
    assert scheduler.operation_supported("connector.sync")
    assert scheduler.operation_supported("connector.incremental_sync")
    assert scheduler.operation_supported("connector.reconcile")
    assert scheduler.operation_supported("notification.retry")
    assert scheduler.operation_supported("report.rerun")
    assert scheduler.operation_supported("connector.purge") is False

    assert scheduler.registered_operations() == (
        "connector.incremental_sync",
        "connector.reconcile",
        "connector.sync",
        "notification.retry",
        "report.rerun",
    )

    specs = scheduler.REGISTERED_OPERATIONS
    assert specs["connector.sync"].required_permission == "connector:read"
    assert specs["connector.sync"].run_type == "initial"
    assert specs["connector.sync"].requires_connector is True
    assert specs["connector.incremental_sync"].run_type == "incremental"
    assert specs["connector.reconcile"].run_type == "reconcile"
    assert specs["notification.retry"].required_permission == "notifications:write"
    assert specs["report.rerun"].required_permission == "reports:run"


async def test_unsupported_operation_is_refused(file_db):
    with pytest.raises(scheduler.SchedulerError) as exc:
        async with file_db() as db:
            await scheduler.create_schedule(
                db,
                tenant_id=TENANT_A,
                owner_principal_id=PRINCIPAL_A,
                name="bad-op",
                schedule_type="workflow",
                operation="connector.purge",
                interval_seconds=60,
            )
    assert exc.value.code == "unsupported_operation"


async def test_interval_below_minimum_is_refused(file_db):
    with pytest.raises(scheduler.SchedulerError) as exc:
        async with file_db() as db:
            await scheduler.create_schedule(
                db,
                tenant_id=TENANT_A,
                owner_principal_id=PRINCIPAL_A,
                name="too-fast",
                schedule_type="connector_sync",
                operation="connector.sync",
                interval_seconds=59,
            )
    assert exc.value.code == "interval_too_small"


async def test_unknown_timezone_is_refused(file_db):
    with pytest.raises(scheduler.SchedulerError) as exc:
        async with file_db() as db:
            await scheduler.create_schedule(
                db,
                tenant_id=TENANT_A,
                owner_principal_id=PRINCIPAL_A,
                name="bad-tz",
                schedule_type="connector_sync",
                operation="connector.sync",
                interval_seconds=60,
                timezone_name="Mars/Olympus_Mons",
            )
    assert exc.value.code == "unknown_timezone"


async def test_create_schedule_persists_and_audits(file_db):
    await _seed(file_db)
    schedule_id = await _create_schedule(
        file_db,
        tenant_id=TENANT_A,
        owner_principal_id=PRINCIPAL_A,
        name="audit-one",
        schedule_type="connector_sync",
        operation="connector.sync",
        interval_seconds=120,
        target={"connector_instance_id": "whatever"},
        actor="tester",
    )
    async with file_db() as db:
        schedule = await db.get(Schedule, schedule_id)
        assert schedule.status == "active"
        assert schedule.enabled is True
        assert schedule.next_run_at is not None
        assert schedule.interval_seconds == 120
        assert schedule.timezone == "UTC"
        actions = (await db.execute(select(AuditRecord.action))).scalars().all()
        assert "schedule.create" in actions


# ---------------------------------------------------------------------------
# A disabled schedule does not run
# ---------------------------------------------------------------------------


async def test_disabled_schedule_is_not_selected_and_does_not_run(file_db):
    await _seed(file_db)
    schedule_id = await _create_schedule(
        file_db,
        **_connector_kwargs(TENANT_A, PRINCIPAL_A, "disabled-one", "ci-x"),
    )
    async with file_db() as db:
        schedule = await db.get(Schedule, schedule_id)
        await scheduler.set_schedule_state(
            db, schedule=schedule, enabled=False, actor="tester"
        )
        await db.commit()
        occurrence = scheduler._as_utc(schedule.next_run_at)
        actions = (await db.execute(select(AuditRecord.action))).scalars().all()
        assert "schedule.disable" in actions

    async with file_db() as db:
        due = await scheduler.due_schedules(db, now=occurrence, limit=10)
        assert schedule_id not in [s.id for s in due]
        assert await scheduler.run_due(db, now=occurrence, limit=10) == 0
        runs = (
            await db.execute(select(ScheduleRun).where(ScheduleRun.schedule_id == schedule_id))
        ).scalars().all()
        assert runs == []


async def test_execute_run_marks_a_paused_schedule_skipped(file_db):
    await _seed(file_db)
    schedule_id = await _create_schedule(
        file_db,
        **_connector_kwargs(TENANT_A, PRINCIPAL_A, "paused-one", "ci-x"),
    )
    async with file_db() as db:
        schedule = await db.get(Schedule, schedule_id)
        await scheduler.set_schedule_state(
            db, schedule=schedule, status="paused", actor="tester"
        )
        await db.commit()
        occurrence = scheduler._as_utc(schedule.next_run_at)

    async with file_db() as db:
        schedule = await db.get(Schedule, schedule_id)
        run = await scheduler.claim_run(
            db, schedule=schedule, scheduled_for=occurrence, claim_token="tok-paused"
        )
        assert run is not None
        finished = await scheduler.execute_run(
            db, schedule=schedule, run=run, claim_token="tok-paused"
        )
        assert finished.status == "skipped"
        assert finished.failure_category == "schedule_disabled"
        assert finished.finished_at is not None


# ---------------------------------------------------------------------------
# A disabled connector blocks scheduled connector work
# ---------------------------------------------------------------------------


async def test_disabled_connector_blocks_scheduled_sync(file_db, counting_connector):
    await _seed(file_db)
    instance_id = await _make_instance(file_db, TENANT_A, enabled=False, name="off-conn")
    schedule_id = await _create_schedule(
        file_db,
        **_connector_kwargs(TENANT_A, PRINCIPAL_A, "off-sync", instance_id),
    )
    occurrence = await _occurrence(file_db, schedule_id)
    now = occurrence + timedelta(seconds=1)

    async with file_db() as db:
        assert await scheduler.run_due(db, now=now, limit=10) == 1
        run = await _single_run(db, schedule_id)
        assert run.status == "failed"
        assert run.failure_category == "connector_disabled"
        sync_runs = (await db.execute(select(ConnectorSyncRun))).scalars().all()
        assert sync_runs == []
    assert counting_connector.list_calls == 0


# ---------------------------------------------------------------------------
# Restart safety and concurrent claimers
# ---------------------------------------------------------------------------


async def test_restart_does_not_duplicate_execution(file_db, counting_connector):
    await _seed(file_db)
    instance_id = await _make_instance(file_db, TENANT_A, enabled=True, name="on-conn")
    schedule_id = await _create_schedule(
        file_db,
        **_connector_kwargs(TENANT_A, PRINCIPAL_A, "restart-sync", instance_id),
    )
    occurrence = await _occurrence(file_db, schedule_id)
    now = occurrence + timedelta(seconds=1)

    # First tick claims and runs the occurrence exactly once.
    async with file_db() as db:
        assert await scheduler.run_due(db, now=now, limit=10) == 1
    assert counting_connector.list_calls == 1

    # A restart still sees the old occurrence: force next_run_at back and tick
    # again. The claim must lose on the unique constraint.
    async with file_db() as db:
        schedule = await db.get(Schedule, schedule_id)
        schedule.next_run_at = occurrence
        await db.commit()

    async with file_db() as db:
        assert await scheduler.run_due(db, now=now, limit=10) == 0

    async with file_db() as db:
        runs = (
            await db.execute(select(ScheduleRun).where(ScheduleRun.schedule_id == schedule_id))
        ).scalars().all()
        assert len(runs) == 1
        assert runs[0].scheduled_for is not None
        sync_runs = (await db.execute(select(ConnectorSyncRun))).scalars().all()
        assert len(sync_runs) == 1
    # The operation itself ran exactly once across both ticks.
    assert counting_connector.list_calls == 1


async def test_two_concurrent_claimers_do_not_double_run(file_db):
    await _seed(file_db)
    schedule_id = await _create_schedule(
        file_db,
        **_connector_kwargs(TENANT_A, PRINCIPAL_A, "claim-race", "ci-x"),
    )
    async with file_db() as db:
        schedule = await db.get(Schedule, schedule_id)
        occurrence = scheduler._as_utc(schedule.next_run_at)

        first = await scheduler.claim_run(
            db, schedule=schedule, scheduled_for=occurrence, claim_token="token-a"
        )
        assert first is not None

        second = await scheduler.claim_run(
            db, schedule=schedule, scheduled_for=occurrence, claim_token="token-b"
        )
        assert second is None

        rows = (
            await db.execute(select(ScheduleRun).where(ScheduleRun.schedule_id == schedule_id))
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].claim_token == "token-a"

        # The losing claim rolled back cleanly: the session is still usable.
        assert (await db.execute(select(Schedule.id))).scalars().all() == [schedule_id]


# ---------------------------------------------------------------------------
# Authorization revalidated at execution time
# ---------------------------------------------------------------------------


async def test_revoked_membership_blocks_the_scheduled_operation(
    file_db, counting_connector
):
    await _seed(file_db)
    instance_id = await _make_instance(file_db, TENANT_A, enabled=True, name="revoke-conn")
    schedule_id = await _create_schedule(
        file_db,
        **_connector_kwargs(TENANT_A, PRINCIPAL_A, "revoke-sched", instance_id),
    )
    occurrence = await _occurrence(file_db, schedule_id)
    now = occurrence + timedelta(seconds=1)

    # The owner is revoked AFTER the schedule exists and BEFORE it runs.
    async with file_db() as db:
        assert (
            await identity_service.revoke_membership(
                db, tenant_id=TENANT_A, principal_id=PRINCIPAL_A
            )
            == 1
        )
        await db.commit()

    async with file_db() as db:
        assert await scheduler.run_due(db, now=now, limit=10) == 1
        run = await _single_run(db, schedule_id)
        assert run.status == "failed"
        assert run.failure_category == "authorization_revoked"
        sync_runs = (await db.execute(select(ConnectorSyncRun))).scalars().all()
        assert sync_runs == []
    assert counting_connector.list_calls == 0


async def test_role_downgrade_is_revalidated_at_execution(file_db, counting_connector):
    await _seed(file_db)
    instance_id = await _make_instance(file_db, TENANT_A, enabled=True, name="downgrade-conn")
    schedule_id = await _create_schedule(
        file_db,
        **_connector_kwargs(TENANT_A, PRINCIPAL_A, "downgrade-sched", instance_id),
    )
    occurrence = await _occurrence(file_db, schedule_id)
    now = occurrence + timedelta(seconds=1)

    # editor -> viewer: 'connector:read' is gone, so the run is refused even
    # though the schedule row still records an editor-era creation.
    async with file_db() as db:
        await identity_service.set_membership_role(
            db, tenant_id=TENANT_A, principal_id=PRINCIPAL_A, role="viewer"
        )
        await db.commit()

    async with file_db() as db:
        assert await scheduler.run_due(db, now=now, limit=10) == 1
        run = await _single_run(db, schedule_id)
        assert run.status == "failed"
        assert run.failure_category == "permission_denied"
        sync_runs = (await db.execute(select(ConnectorSyncRun))).scalars().all()
        assert sync_runs == []
    assert counting_connector.list_calls == 0


# ---------------------------------------------------------------------------
# Other operations
# ---------------------------------------------------------------------------


async def test_report_rerun_is_refused_as_not_implemented(file_db):
    schedule_id = await _create_schedule(
        file_db,
        tenant_id=LEGACY_TENANT_ID,
        owner_principal_id=LEGACY_PRINCIPAL_ID,
        name="report-rerun",
        schedule_type="report_rerun",
        operation="report.rerun",
        interval_seconds=60,
    )
    occurrence = await _occurrence(file_db, schedule_id)
    now = occurrence + timedelta(seconds=1)

    async with file_db() as db:
        assert await scheduler.run_due(db, now=now, limit=10) == 1
        run = await _single_run(db, schedule_id)
        assert run.status == "failed"
        assert run.failure_category == "operation_not_implemented"


async def test_notification_retry_operation_runs(file_db):
    async with file_db() as db:
        db.add(
            Notification(
                tenant_id=LEGACY_TENANT_ID,
                principal_id=LEGACY_PRINCIPAL_ID,
                category="report_ready",
                subject="s",
                body="b",
                status="failed",
                attempts=0,
                max_attempts=3,
                channel_id=None,
            )
        )
        await db.commit()

    schedule_id = await _create_schedule(
        file_db,
        tenant_id=LEGACY_TENANT_ID,
        owner_principal_id=LEGACY_PRINCIPAL_ID,
        name="notify-retry",
        schedule_type="notification_retry",
        operation="notification.retry",
        interval_seconds=60,
    )
    occurrence = await _occurrence(file_db, schedule_id)
    now = occurrence + timedelta(seconds=1)

    async with file_db() as db:
        assert await scheduler.run_due(db, now=now, limit=10) == 1
        run = await _single_run(db, schedule_id)
        assert run.status == "succeeded"
    async with file_db() as db:
        notification = (
            await db.execute(select(Notification))
        ).scalars().one()
        assert notification.attempts == 1


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


async def test_due_schedules_are_tenant_scoped(file_db):
    await _seed(file_db)
    schedule_a = await _create_schedule(
        file_db, **_connector_kwargs(TENANT_A, PRINCIPAL_A, "iso-a", "ci-a")
    )
    schedule_b = await _create_schedule(
        file_db, **_connector_kwargs(TENANT_B, PRINCIPAL_B, "iso-b", "ci-b")
    )
    async with file_db() as db:
        a = await db.get(Schedule, schedule_a)
        b = await db.get(Schedule, schedule_b)
        now = max(
            scheduler._as_utc(a.next_run_at), scheduler._as_utc(b.next_run_at)
        ) + timedelta(seconds=1)

    async with file_db() as db:
        assert [
            s.id
            for s in await scheduler.due_schedules(
                db, now=now, limit=10, tenant_id=TENANT_A
            )
        ] == [schedule_a]
        assert [
            s.id
            for s in await scheduler.due_schedules(
                db, now=now, limit=10, tenant_id=TENANT_B
            )
        ] == [schedule_b]
        assert [s.id for s in await scheduler.list_schedules(db, tenant_id=TENANT_A)] == [
            schedule_a
        ]


async def test_a_schedule_cannot_reach_another_tenants_connector(file_db, counting_connector):
    await _seed(file_db)
    foreign_instance = await _make_instance(file_db, TENANT_A, enabled=True, name="tenant-a-conn")
    schedule_id = await _create_schedule(
        file_db,
        **_connector_kwargs(TENANT_B, PRINCIPAL_B, "cross-tenant", foreign_instance),
    )
    occurrence = await _occurrence(file_db, schedule_id)
    now = occurrence + timedelta(seconds=1)

    async with file_db() as db:
        assert await scheduler.run_due(db, now=now, limit=10) == 1
        run = await _single_run(db, schedule_id)
        assert run.status == "failed"
        assert run.failure_category == "connector_not_found"
    assert counting_connector.list_calls == 0


# ---------------------------------------------------------------------------
# Bounds and misfire policy
# ---------------------------------------------------------------------------


async def test_run_due_respects_the_limit(file_db):
    await _seed(file_db)
    for index in range(3):
        await _create_schedule(
            file_db,
            **_connector_kwargs(TENANT_A, PRINCIPAL_A, f"bound-{index}", "ci-x"),
        )
    async with file_db() as db:
        schedules = (
            await db.execute(select(Schedule).where(Schedule.tenant_id == TENANT_A))
        ).scalars().all()
        now = max(scheduler._as_utc(s.next_run_at) for s in schedules) + timedelta(seconds=1)

    async with file_db() as db:
        assert await scheduler.run_due(db, now=now, limit=2) == 2
        assert await scheduler.run_due(db, now=now, limit=2) == 1
        assert await scheduler.run_due(db, now=now, limit=2) == 0


async def test_misfire_skip_advances_to_a_future_occurrence(file_db, counting_connector):
    await _seed(file_db)
    instance_id = await _make_instance(file_db, TENANT_A, enabled=True, name="misfire-conn")
    schedule_id = await _create_schedule(
        file_db,
        **_connector_kwargs(TENANT_A, PRINCIPAL_A, "misfire-sched", instance_id, interval=60),
    )
    occurrence = await _occurrence(file_db, schedule_id)
    # An outage of ten intervals: the schedule fires once, not ten times.
    now = occurrence + timedelta(seconds=600)

    async with file_db() as db:
        assert await scheduler.run_due(db, now=now, limit=10) == 1
        schedule = await db.get(Schedule, schedule_id)
        assert scheduler._as_utc(schedule.next_run_at) > now
        runs = (
            await db.execute(select(ScheduleRun).where(ScheduleRun.schedule_id == schedule_id))
        ).scalars().all()
        assert len(runs) == 1
    assert counting_connector.list_calls == 1
