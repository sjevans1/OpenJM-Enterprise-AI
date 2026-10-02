"""VS4-B2C1: persistence, idempotency, immutability, history, revocation.

B2C1 introduces a ReportRun model and a read-only history API. No public
run-submission endpoint, no model calls, no retrieval or SQL execution. The
internal reserve/finalize functions are exercised through isolated tests only;
they do not fabricate reports in the application.
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.db import Base, enable_sqlite_foreign_keys, get_db
from app.main import app
from app.models import (
    Conversation,
    DataSource,
    Document,
    Message,
    ReportDefinitionVersion,
    ReportRun,
    SavedReport,
)
from app.services.report_runs import (
    ReportRunConflict,
    finalize_failure,
    finalize_success,
    interrupt_expired_runs,
    reserve_report_run,
)

settings = get_settings()


def _pragmas(dbapi_connection, _record):
    enable_sqlite_foreign_keys(dbapi_connection, _record)
    cur = dbapi_connection.cursor()
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()


@pytest.fixture
async def file_db(tmp_path):
    path = tmp_path / "b2c1.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{path}", pool_recycle=999, connect_args={"timeout": 10}
    )
    event.listen(engine.sync_engine, "connect", _pragmas)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield maker
    finally:
        await engine.dispose()


@pytest.fixture
async def sessions(file_db):
    """Two distinct sessions over the same file-backed store."""
    async with file_db() as a, file_db() as b:
        yield a, b


@pytest.fixture
async def client(file_db):
    async def override_db():
        async with file_db() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http
    app.dependency_overrides.clear()


async def seed_definition(maker):
    """Persist a minimal report + definition version for reuse."""
    async with maker() as db:
        conv = Conversation(user_id=settings.dev_user_id, title="Conv")
        db.add(conv)
        await db.flush()
        doc = Document(
            user_id=settings.dev_user_id,
            original_name="policy.txt",
            stored_path="/tmp/b2c1-test-only-policy",
            size_bytes=22,
            status="ready",
            indexed=True,
        )
        src = DataSource(
            user_id=settings.dev_user_id,
            name="Finance",
            engine="sqlite",
            connection_secret="fixture-only-not-real",
            status="connected",
            enabled=True,
            schema_json=json.dumps([
                {
                    "schema_name": "main",
                    "name": "finance",
                    "qualified_name": "finance",
                    "columns": [{"name": "revenue", "type": "NUMERIC", "nullable": False}],
                    "primary_key": [],
                    "foreign_keys": [],
                }
            ]),
            authorized_objects_json=json.dumps(["finance"]),
        )
        db.add_all([doc, src])
        await db.flush()
        user = Message(
            conversation_id=conv.id,
            role="user",
            content="Which customers exceed the FY2025 USD 300 threshold?",
            requested_mode="hybrid",
        )
        db.add(user)
        await db.flush()
        evidence = [
            {
                "source_type": "document",
                "source_id": doc.id,
                "title": "Policy",
                "passage": "FY2025 threshold USD 300",
            },
            {
                "source_type": "structured_query",
                "source_id": src.id,
                "title": "Finance",
                "passage": '{"columns":["revenue"],"rows":[[325]],"row_count":1}',
                "metadata": {"tables": ["finance"], "sql": "SELECT revenue FROM finance"},
                "provenance": {
                    "grounded_parameter": {"source_id": doc.id, "value": "300"}
                },
            },
        ]
        assistant = Message(
            conversation_id=conv.id,
            role="assistant",
            content="Delta Co exceeded the FY2025 USD 300 threshold.",
            execution_class="hybrid",
            requested_mode="hybrid",
            evidence_json=json.dumps(evidence),
        )
        db.add(assistant)
        await db.flush()
        report = SavedReport(
            user_id=settings.dev_user_id,
            conversation_id=conv.id,
            message_id=assistant.id,
            title="Budget review",
            answer_text=assistant.content,
            evidence_json=assistant.evidence_json,
            execution_class="hybrid",
            requested_mode="hybrid",
            source_count=2,
            snapshot_as_of=assistant.created_at,
        )
        db.add(report)
        await db.flush()
        definition = ReportDefinitionVersion(
            user_id=settings.dev_user_id,
            report_id=report.id,
            version=1,
            question_text=user.content,
            requested_mode="hybrid",
            pinned_document_ids_json=json.dumps([doc.id]),
            pinned_source_tables_json=json.dumps({src.id: ["finance"]}),
        )
        db.add(definition)
        await db.commit()
        return {
            "report_id": report.id,
            "definition_id": definition.id,
            "definition_version": 1,
            "assistant_id": assistant.id,
        }


class _Clock:
    def __init__(self, start):
        self._now = start

    def __call__(self):
        return self._now


# ---------------------------------------------------------------------------
# Phase 1 tests: ReportRun model and DB-level immutability guards
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_report_run_table_exists_and_constraints(file_db):
    async with file_db() as db:
        # The table must exist and the FK to saved_reports must be enforced:
        # referencing a nonexistent report must fail.
        now = datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc)
        later = now + timedelta(hours=1)
        with pytest.raises(IntegrityError):
            await db.execute(text(
                "INSERT INTO report_runs (id, user_id, report_id, definition_id, "
                "definition_version, requested_mode, idempotency_key, request_fingerprint, "
                "status, started_at, deadline_at, created_at) "
                "VALUES ('r1', 'u1', 'rep1', 'def1', 1, 'hybrid', "
                "'00000000-0000-4000-8000-000000000000', 'fp1', 'running', :now, :later, :now)"
            ), {"now": now, "later": later})
        await db.rollback()
        # A row without the required report_id must also fail.
        with pytest.raises(IntegrityError):
            await db.execute(text(
                "INSERT INTO report_runs (id, user_id, definition_id, "
                "definition_version, requested_mode, idempotency_key, "
                "request_fingerprint, status, started_at, deadline_at, created_at) "
                "VALUES ('r2', 'u1', 'def1', 1, 'hybrid', "
                "'00000000-0000-4000-8000-000000000002', 'fp2', 'running', :now, :later, :now)"
            ), {"now": now, "later": later})
        await db.rollback()
        assert (
            await db.execute(text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='report_runs'"
            ))
        ).scalar_one() == "report_runs"


# ---------------------------------------------------------------------------
# Phase 2 tests: reserve_report_run idempotency and concurrency
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reservation_returns_canonical_uuid_token(file_db):
    fixture = await seed_definition(file_db)
    async with file_db() as db:
        clock = _Clock(datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc))
        run, owns = await reserve_report_run(
            db=db,
            report_id=fixture["report_id"],
            definition_version=1,
            idempotency_key="00000000-0000-4000-8000-000000000006",
            requested_mode="hybrid",
            question="Which customers exceed the FY2025 USD 300 threshold?",
            pinned_document_ids=[],
            pinned_source_tables={},
            now=clock,
        )
    assert owns is True
    assert run.idempotency_key == "00000000-0000-4000-8000-000000000006"
    assert run.status == "running"


@pytest.mark.asyncio
async def test_reservation_rejects_non_canonical_uuid(file_db):
    fixture = await seed_definition(file_db)
    async with file_db() as db:
        clock = _Clock(datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc))
        with pytest.raises((ValueError, ReportRunConflict)):
            await reserve_report_run(
                db=db,
                report_id=fixture["report_id"],
                definition_version=1,
                idempotency_key="NOT-A-UUID",
                requested_mode="hybrid",
                question="q",
                pinned_document_ids=[],
                pinned_source_tables={},
                now=clock,
            )


@pytest.mark.asyncio
async def test_same_key_same_intent_returns_existing_run_without_ownership(file_db):
    fixture = await seed_definition(file_db)
    async with file_db() as db:
        clock = _Clock(datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc))
        first, owns_first = await reserve_report_run(
            db=db,
            report_id=fixture["report_id"],
            definition_version=1,
            idempotency_key="00000000-0000-4000-8000-000000000001",
            requested_mode="hybrid",
            question="Which customers exceed the FY2025 USD 300 threshold?",
            pinned_document_ids=[],
            pinned_source_tables={},
            now=clock,
        )
        await db.commit()
    async with file_db() as db:
        second, owns_second = await reserve_report_run(
            db=db,
            report_id=fixture["report_id"],
            definition_version=1,
            idempotency_key="00000000-0000-4000-8000-000000000001",
            requested_mode="hybrid",
            question="Which customers exceed the FY2025 USD 300 threshold?",
            pinned_document_ids=[],
            pinned_source_tables={},
            now=clock,
        )
        await db.commit()
    assert owns_first is True
    assert owns_second is False
    assert second.id == first.id


@pytest.mark.asyncio
async def test_same_key_different_intent_returns_conflict(file_db):
    fixture = await seed_definition(file_db)
    async with file_db() as db:
        clock = _Clock(datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc))
        run, owns = await reserve_report_run(
            db=db,
            report_id=fixture["report_id"],
            definition_version=1,
            idempotency_key="00000000-0000-4000-8000-000000000002",
            requested_mode="hybrid",
            question="Which customers exceed the FY2025 USD 300 threshold?",
            pinned_document_ids=[],
            pinned_source_tables={},
            now=clock,
        )
        await db.commit()
    async with file_db() as db:
        with pytest.raises(ReportRunConflict):
            await reserve_report_run(
                db=db,
                report_id=fixture["report_id"],
                definition_version=1,
                idempotency_key="00000000-0000-4000-8000-000000000002",
                requested_mode="hybrid",
                question="Different question entirely",
                pinned_document_ids=[],
                pinned_source_tables={},
                now=clock,
            )


@pytest.mark.asyncio
async def test_concurrent_reservation_one_winner(file_db, sessions):
    fixture = await seed_definition(file_db)
    a, b = sessions
    clock = _Clock(datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc))
    key = "00000000-0000-4000-8000-000000000003"
    results = await asyncio.gather(
        reserve_report_run(
            db=a, report_id=fixture["report_id"], definition_version=1,
            idempotency_key=key, requested_mode="hybrid",
            question="Q", pinned_document_ids=[], pinned_source_tables={}, now=clock,
        ),
        reserve_report_run(
            db=b, report_id=fixture["report_id"], definition_version=1,
            idempotency_key=key, requested_mode="hybrid",
            question="Q", pinned_document_ids=[], pinned_source_tables={}, now=clock,
        ),
    )
    owners = sorted(r.id for r, owns in results if owns)
    non_owners = sorted(r.id for r, owns in results if not owns)
    assert len(owners) == 1
    assert len(non_owners) == 1
    assert owners[0] == non_owners[0]


# ---------------------------------------------------------------------------
# Phase 2 tests: stale-run recovery and failure terminal states
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_expired_running_becomes_interrupted(file_db):
    fixture = await seed_definition(file_db)
    async with file_db() as db:
        clock = _Clock(datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc))
        run, owns = await reserve_report_run(
            db=db, report_id=fixture["report_id"], definition_version=1,
            idempotency_key="00000000-0000-4000-8000-000000000004",
            requested_mode="hybrid", question="Q", pinned_document_ids=[],
            pinned_source_tables={}, now=clock,
        )
        await db.commit()
    recovery_clock = datetime(2026, 10, 2, 10, 30, 1, tzinfo=timezone.utc)
    async with file_db() as db:
        count = await interrupt_expired_runs(db=db, now=recovery_clock)
        await db.commit()
    async with file_db() as db:
        row = await db.get(ReportRun, run.id)
    assert count == 1
    assert row.status == "interrupted"
    assert row.finished_at is not None
    assert row.failure_category == "deadline_expired"
    assert row.result_json is None


@pytest.mark.asyncio
async def test_interrupted_run_rejects_same_token(file_db):
    fixture = await seed_definition(file_db)
    async with file_db() as db:
        clock = _Clock(datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc))
        run, owns = await reserve_report_run(
            db=db, report_id=fixture["report_id"], definition_version=1,
            idempotency_key="00000000-0000-4000-8000-000000000005",
            requested_mode="hybrid", question="Q", pinned_document_ids=[],
            pinned_source_tables={}, now=clock,
        )
        await db.commit()
        await interrupt_expired_runs(
            db=db, now=datetime(2026, 10, 2, 10, 30, 1, tzinfo=timezone.utc)
        )
        await db.commit()
    async with file_db() as db:
        result, owns = await reserve_report_run(
            db=db, report_id=fixture["report_id"], definition_version=1,
            idempotency_key="00000000-0000-4000-8000-000000000005",
            requested_mode="hybrid", question="Q", pinned_document_ids=[],
            pinned_source_tables={}, now=clock,
        )
        await db.commit()
    assert result.id == run.id
    assert result.status == "interrupted"
    assert owns is False


# ---------------------------------------------------------------------------
# Phase 2 tests: finalization immutability and bounds
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_finalize_success_then_rejects_again(file_db):
    fixture = await seed_definition(file_db)
    async with file_db() as db:
        clock = _Clock(datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc))
        run, _ = await reserve_report_run(
            db=db, report_id=fixture["report_id"], definition_version=1,
            idempotency_key="00000000-0000-4000-8000-000000000010",
            requested_mode="hybrid", question="Q", pinned_document_ids=[],
            pinned_source_tables={}, now=clock,
        )
        await db.commit()
        count = await finalize_success(
            db=db, run_id=run.id, user_id=settings.dev_user_id,
            fingerprint=run.request_fingerprint,
            answer="Answer",
            evidence=[{"source_type": "document", "source_id": "x", "title": "t", "passage": "p"}],
            structured_result=None, trace_ids=["trace-1"],
        )
        assert count == 1
    async with file_db() as db:
        row = await db.get(ReportRun, run.id)
    assert row.status == "succeeded"
    async with file_db() as db:
        count2 = await finalize_success(
            db=db, run_id=row.id, user_id=settings.dev_user_id,
            fingerprint=row.request_fingerprint,
            answer="Answer", evidence=[], structured_result=None, trace_ids=[],
        )
        assert count2 == 0


@pytest.mark.asyncio
async def test_finalize_failure_records_category(file_db):
    fixture = await seed_definition(file_db)
    async with file_db() as db:
        clock = _Clock(datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc))
        run, _ = await reserve_report_run(
            db=db, report_id=fixture["report_id"], definition_version=1,
            idempotency_key="00000000-0000-4000-8000-000000000011",
            requested_mode="hybrid", question="Q", pinned_document_ids=[],
            pinned_source_tables={}, now=clock,
        )
        await db.commit()
        count = await finalize_failure(
            db=db, run_id=run.id, user_id=settings.dev_user_id,
            fingerprint=run.request_fingerprint,
            failure_category="model",
        )
        assert count == 1
    async with file_db() as db:
        row = await db.get(ReportRun, run.id)
    assert row.status == "failed"
    assert row.failure_category == "model"
    assert row.result_json is None


@pytest.mark.asyncio
async def test_oversized_result_fails_finalization(file_db):
    fixture = await seed_definition(file_db)
    async with file_db() as db:
        clock = _Clock(datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc))
        run, _ = await reserve_report_run(
            db=db, report_id=fixture["report_id"], definition_version=1,
            idempotency_key="00000000-0000-4000-8000-000000000012",
            requested_mode="hybrid", question="Q", pinned_document_ids=[],
            pinned_source_tables={}, now=clock,
        )
        await db.commit()
        oversized_answer = "X" * 24001
        count = await finalize_success(
            db=db, run_id=run.id, user_id=settings.dev_user_id,
            fingerprint=run.request_fingerprint,
            answer=oversized_answer, evidence=[], structured_result=None, trace_ids=[],
        )
        assert count == 0
    async with file_db() as db:
        row = await db.get(ReportRun, run.id)
    assert row.status == "running"


# ---------------------------------------------------------------------------
# Phase 3 tests: DB-level immutability triggers (defense-in-depth)
# ---------------------------------------------------------------------------

@pytest.fixture
async def trigger_db(tmp_path):
    """Engine with tables + immutability triggers, mirroring init_db."""
    path = tmp_path / "trigger.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}", future=True)
    event.listen(engine.sync_engine, "connect", _pragmas)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        from app.db import _REPORT_RUN_IMMUTABILITY_TRIGGERS_SQL
        for statement in _REPORT_RUN_IMMUTABILITY_TRIGGERS_SQL:
            await conn.exec_driver_sql(statement)

    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


async def _seed_run(maker):
    """Create parent rows + one running report_run; return its id + fingerprint."""
    async with maker() as db:
        conv = Conversation(user_id=settings.dev_user_id, title="C")
        db.add(conv)
        await db.flush()
        msg = Message(
            conversation_id=conv.id, role="user", content="q", requested_mode="hybrid",
        )
        db.add(msg)
        await db.flush()
        sr = SavedReport(
            user_id=settings.dev_user_id, conversation_id=conv.id, message_id=msg.id,
            title="R", answer_text="A", evidence_json="[]",
            execution_class="hybrid", requested_mode="hybrid",
            source_count=1, snapshot_as_of=msg.created_at,
        )
        db.add(sr)
        await db.flush()
        rdv = ReportDefinitionVersion(
            user_id=settings.dev_user_id, report_id=sr.id, version=1,
            question_text="q", requested_mode="hybrid",
            pinned_document_ids_json="[]", pinned_source_tables_json="{}",
        )
        db.add(rdv)
        await db.flush()
        run = ReportRun(
            user_id=settings.dev_user_id, report_id=sr.id,
            definition_id=rdv.id, definition_version=1,
            requested_mode="hybrid",
            idempotency_key="00000000-0000-4000-8000-000000000020",
            request_fingerprint="fp20",
            status="running",
            started_at=datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc),
            deadline_at=datetime(2026, 10, 2, 9, 30, 0, tzinfo=timezone.utc),
        )
        db.add(run)
        await db.commit()
        return run.id, run.request_fingerprint


@pytest.mark.asyncio
async def test_trigger_blocks_identity_field_update(trigger_db):
    rid, fp = await _seed_run(trigger_db)
    async with trigger_db() as db:
        with pytest.raises(IntegrityError):
            await db.execute(text(
                "UPDATE report_runs SET idempotency_key='different' WHERE id=:rid"
            ), {"rid": rid})
        await db.rollback()
        # status-only re-set (no identity change) is allowed
        await db.execute(text(
            "UPDATE report_runs SET status='running' WHERE id=:rid"
        ), {"rid": rid})
        await db.commit()


@pytest.mark.asyncio
async def test_trigger_blocks_terminal_then_reopen(trigger_db):
    rid, fp = await _seed_run(trigger_db)
    async with trigger_db() as db:
        # transition running -> succeeded (allowed by trigger + checks)
        await db.execute(text(
            "UPDATE report_runs SET status='succeeded', finished_at='2026-10-02 09:05:00', "
            "result_json='{\"answer\":\"ok\"}', result_size_bytes=14, "
            "result_sha256='abc' WHERE id=:rid"
        ), {"rid": rid})
        await db.commit()
        # now attempt to regress succeeded -> failed (must be blocked)
        with pytest.raises(IntegrityError):
            await db.execute(text(
                "UPDATE report_runs SET status='failed', finished_at='2026-10-02 09:10:00', "
                "failure_category='model' WHERE id=:rid"
            ), {"rid": rid})
        await db.rollback()


@pytest.mark.asyncio
async def test_trigger_blocks_status_regression_from_failed(trigger_db):
    rid, fp = await _seed_run(trigger_db)
    async with trigger_db() as db:
        await db.execute(text(
            "UPDATE report_runs SET status='failed', finished_at='2026-10-02 09:05:00', "
            "failure_category='model' WHERE id=:rid"
        ), {"rid": rid})
        await db.commit()
        with pytest.raises(IntegrityError):
            await db.execute(text(
                "UPDATE report_runs SET status='succeeded', finished_at='2026-10-02 09:06:00', "
                "result_json='{}', result_size_bytes=2, result_sha256='x' WHERE id=:rid"
            ), {"rid": rid})
        await db.rollback()


@pytest.mark.asyncio
async def test_trigger_requires_result_for_succeeded(trigger_db):
    rid, fp = await _seed_run(trigger_db)
    async with trigger_db() as db:
        # succeeded without result_json violates ck_report_run_succeeded_has_result
        with pytest.raises(IntegrityError):
            await db.execute(text(
                "UPDATE report_runs SET status='succeeded', finished_at='2026-10-02 09:05:00' "
                "WHERE id=:rid"
            ), {"rid": rid})
        await db.rollback()


@pytest.mark.asyncio
async def test_trigger_blocks_protected_started_at_change(trigger_db):
    rid, fp = await _seed_run(trigger_db)
    async with trigger_db() as db:
        with pytest.raises(IntegrityError):
            await db.execute(text(
                "UPDATE report_runs SET started_at='2026-10-02 12:00:00' WHERE id=:rid"
            ), {"rid": rid})
        await db.rollback()


# ---------------------------------------------------------------------------
# Phase 3 tests: startup stale-run recovery
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_recover_stale_report_runs_marks_expired(file_db):
    fixture = await seed_definition(file_db)
    clock = _Clock(datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc))
    async with file_db() as db:
        run, owns = await reserve_report_run(
            db=db, report_id=fixture["report_id"], definition_version=1,
            idempotency_key="00000000-0000-4000-8000-000000000030",
            requested_mode="hybrid", question="Q", pinned_document_ids=[],
            pinned_source_tables={}, now=clock,
        )
        await db.commit()
    async with file_db() as db:
        count = await interrupt_expired_runs(
            db=db, now=datetime(2026, 10, 2, 10, 0, 1, tzinfo=timezone.utc)
        )
        await db.commit()
        assert count == 1
    async with file_db() as db:
        row = await db.get(ReportRun, run.id)
    assert row.status == "interrupted"
    assert row.failure_category == "deadline_expired"


@pytest.mark.asyncio
async def test_recover_preserves_non_expired_running(file_db):
    fixture = await seed_definition(file_db)
    clock = _Clock(datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc))
    async with file_db() as db:
        run, owns = await reserve_report_run(
            db=db, report_id=fixture["report_id"], definition_version=1,
            idempotency_key="00000000-0000-4000-8000-000000000031",
            requested_mode="hybrid", question="Q", pinned_document_ids=[],
            pinned_source_tables={}, now=clock,
        )
        await db.commit()
    # recovery "now" is BEFORE the deadline -> nothing should change
    async with file_db() as db:
        count = await interrupt_expired_runs(
            db=db, now=datetime(2026, 10, 2, 9, 5, 0, tzinfo=timezone.utc)
        )
        await db.commit()
    assert count == 0
    async with file_db() as db:
        row = await db.get(ReportRun, run.id)
    assert row.status == "running"
