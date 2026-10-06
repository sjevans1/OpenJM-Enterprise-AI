"""VS4-B2C1 correction pass: regressions for the PR #28 review findings.

Each test reproduces one defect reported in the independent review of
cc9ebdc against the same production modules, routers and services:

1. Source authorization: revoked documents, disabled sources and removed
   table grants must fail closed on run list and detail reads (409),
   consistently with saved reports and B2A definitions.
2. Startup recovery durability: the interruption must be committed; a
   fresh session must not still see an expired run as running.
3. Reservation authority: a foreign-owner definition or report and altered
   caller-supplied intent must be rejected.
4. DB-trigger owner immutability for RUNNING rows.
5. The read bound must match the persisted write bound: a valid
   69,172-byte result saves and must read without 422.
6. _canonical_key must reject brace, uppercase, compact and urn forms.

Acceptance evidence added per the batch contract: synthetic pre-B2C1
upgrade with repeated startup preservation, deletion/no-resurrection, and
zero model/retrieval/source-SQL/trace-write read sentinels.
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.db import (
    _REPORT_RUN_IMMUTABILITY_TRIGGERS_SQL,
    _recover_stale_report_runs,
    enable_sqlite_foreign_keys,
)
from app.main import app
from app.models import Base, DataSource, Document, ExecutionTrace, ReportRun
from app.services.report_runs import (
    MAX_RESULT_BYTES,
    ReportRunConflict,
    _canonical_key,
    finalize_success,
    reserve_report_run,
)

from conftest import _Clock, seed_definition

settings = get_settings()

KEY_DOC = "00000000-0000-4000-8000-000000000201"
KEY_SRC = "00000000-0000-4000-8000-000000000202"
KEY_GRANT = "00000000-0000-4000-8000-000000000203"
KEY_LARGE = "00000000-0000-4000-8000-000000000204"
KEY_FOREIGN_DEF = "00000000-0000-4000-8000-000000000205"
KEY_FOREIGN_REPORT = "00000000-0000-4000-8000-000000000206"
KEY_CONTROL = "00000000-0000-4000-8000-000000000207"
KEY_ALTERED_INTENT = "00000000-0000-4000-8000-000000000208"

BACKDATED_DEADLINE = "2026-10-02 08:00:00.000000"


def _pragmas(dbapi_connection, _record):
    enable_sqlite_foreign_keys(dbapi_connection, _record)
    cur = dbapi_connection.cursor()
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()


def _make_engine(path):
    return create_async_engine(f"sqlite+aiosqlite:///{path}", future=True)


async def _succeeded_run(maker, fixture, key: str, *, evidence=None) -> str:
    """Reserve + finalize a run against the seeded (authorized) fixture."""
    clock = _Clock(datetime.now(timezone.utc))
    async with maker() as db:
        run, _ = await reserve_report_run(
            db=db, report_id=fixture["report_id"], definition_version=1,
            idempotency_key=key, requested_mode=fixture["requested_mode"],
            question=fixture["question"], pinned_document_ids=fixture["pinned_document_ids"],
            pinned_source_tables=fixture["pinned_source_tables"], now=clock,
        )
        await db.commit()
        count = await finalize_success(
            db=db, run_id=run.id, user_id=settings.dev_user_id,
            fingerprint=run.request_fingerprint, answer="Answer",
            evidence=evidence or [{
                "source_type": "document",
                "source_id": fixture["pinned_document_ids"][0],
                "title": "t",
                "passage": "p",
            }],
            structured_result=None, trace_ids=["t-1"],
        )
        assert count == 1
        return run.id


async def _inject_succeeded_result(maker, fixture, key: str, evidence: list[dict]) -> str:
    clock = _Clock(datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc))
    async with maker() as db:
        run, _ = await reserve_report_run(
            db=db, report_id=fixture["report_id"], definition_version=1,
            idempotency_key=key, requested_mode=fixture["requested_mode"],
            question=fixture["question"], pinned_document_ids=fixture["pinned_document_ids"],
            pinned_source_tables=fixture["pinned_source_tables"], now=clock,
        )
        await db.commit()
        payload = json.dumps(
            {
                "answer": "Injected answer",
                "evidence": evidence,
                "structured_result": {},
                "trace_ids": [],
            },
            separators=(",", ":"),
        )
        await db.execute(
            text(
                "UPDATE report_runs SET status='succeeded',"
                "finished_at='2026-10-02 09:05:00.000000',result_json=:r,"
                "result_size_bytes=:s,result_sha256=:h WHERE id=:i"
            ),
            {
                "r": payload,
                "s": len(payload.encode("utf-8")),
                "h": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                "i": run.id,
            },
        )
        await db.commit()
        return run.id


async def _first_row_id(maker, table: str) -> str:
    async with maker() as db:
        return (
            await db.execute(
                text(f"SELECT id FROM {table} WHERE user_id = :u LIMIT 1"),
                {"u": settings.dev_user_id},
            )
        ).scalar_one()


# ---------------------------------------------------------------------------
# Finding 1: source authorization on run-history reads (fail closed)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_reads_409_when_document_revoked(client, file_db):
    fixture = await seed_definition(file_db)
    run_id = await _succeeded_run(file_db, fixture, KEY_DOC)
    async with file_db() as db:
        doc_id = await _first_row_id(file_db, "documents")
        await db.execute(text("UPDATE documents SET indexed = 0 WHERE id = :i"), {"i": doc_id})
        await db.commit()
    # sanity: the saved report itself now fails closed
    assert (await client.get(f"/api/reports/{fixture['report_id']}")).status_code == 409
    resp = await client.get(f"/api/reports/runs/{run_id}")
    assert resp.status_code == 409, f"revoked document leaked run detail (HTTP {resp.status_code})"
    resp = await client.get(f"/api/reports/{fixture['report_id']}/runs")
    assert resp.status_code == 409, f"revoked document leaked run list (HTTP {resp.status_code})"


@pytest.mark.asyncio
async def test_run_reads_409_when_source_disabled(client, file_db):
    fixture = await seed_definition(file_db)
    run_id = await _succeeded_run(file_db, fixture, KEY_SRC)
    async with file_db() as db:
        src_id = await _first_row_id(file_db, "data_sources")
        await db.execute(text("UPDATE data_sources SET enabled = 0 WHERE id = :i"), {"i": src_id})
        await db.commit()
    resp = await client.get(f"/api/reports/runs/{run_id}")
    assert resp.status_code == 409, f"disabled source leaked run detail (HTTP {resp.status_code})"
    resp = await client.get(f"/api/reports/{fixture['report_id']}/runs")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_run_reads_409_when_table_grant_removed(client, file_db):
    fixture = await seed_definition(file_db)
    run_id = await _succeeded_run(file_db, fixture, KEY_GRANT)
    async with file_db() as db:
        src_id = await _first_row_id(file_db, "data_sources")
        await db.execute(
            text("UPDATE data_sources SET authorized_objects_json = :aj WHERE id = :i"),
            {"aj": json.dumps(["other_table"]), "i": src_id},
        )
        await db.commit()
    resp = await client.get(f"/api/reports/runs/{run_id}")
    assert resp.status_code == 409, f"removed grant leaked run detail (HTTP {resp.status_code})"
    resp = await client.get(f"/api/reports/{fixture['report_id']}/runs")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_run_reads_ok_when_sources_authorized(client, file_db):
    """Control: sources intact, run list and detail stay readable (200)."""
    fixture = await seed_definition(file_db)
    run_id = await _succeeded_run(file_db, fixture, KEY_CONTROL)
    resp = await client.get(f"/api/reports/{fixture['report_id']}/runs")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    resp = await client.get(f"/api/reports/runs/{run_id}")
    assert resp.status_code == 200
    assert resp.json()["result"]["answer"] == "Answer"


async def _assert_run_reads_409(client, fixture, run_id: str) -> None:
    detail = await client.get(f"/api/reports/runs/{run_id}")
    assert detail.status_code == 409, detail.text
    listing = await client.get(f"/api/reports/{fixture['report_id']}/runs")
    assert listing.status_code == 409, listing.text


@pytest.mark.asyncio
async def test_run_reads_409_when_pinned_table_disappears_from_schema(client, file_db):
    fixture = await seed_definition(file_db)
    run_id = await _succeeded_run(
        file_db, fixture, "00000000-0000-4000-8000-000000000260"
    )
    source_id = next(iter(fixture["pinned_source_tables"]))
    async with file_db() as db:
        await db.execute(
            text("UPDATE data_sources SET schema_json = '[]' WHERE id = :i"),
            {"i": source_id},
        )
        await db.commit()
    # The old grant deliberately remains. Parent-snapshot availability alone
    # still passes, while the exact definition's current discovered scope does not.
    assert (await client.get(f"/api/reports/{fixture['report_id']}")).status_code == 200
    assert (
        await client.get(f"/api/reports/{fixture['report_id']}/definitions/1")
    ).status_code == 409
    await _assert_run_reads_409(client, fixture, run_id)


@pytest.mark.asyncio
async def test_run_reads_409_when_exact_definition_pins_missing_document(client, file_db):
    fixture = await seed_definition(file_db)
    run_id = await _succeeded_run(
        file_db, fixture, "00000000-0000-4000-8000-000000000261"
    )
    async with file_db() as db:
        await db.execute(
            text(
                "UPDATE report_definition_versions "
                "SET pinned_document_ids_json = :pins WHERE id = :i"
            ),
            {"pins": json.dumps(["missing-document"]), "i": fixture["definition_id"]},
        )
        await db.commit()
    assert (await client.get(f"/api/reports/{fixture['report_id']}")).status_code == 200
    await _assert_run_reads_409(client, fixture, run_id)


@pytest.mark.asyncio
async def test_run_reads_409_when_definition_identity_does_not_match_parent(client, file_db):
    fixture = await seed_definition(file_db)
    other = await seed_definition(file_db)
    now = datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc)
    corrupt = ReportRun(
        user_id=settings.dev_user_id,
        report_id=fixture["report_id"],
        definition_id=other["definition_id"],
        definition_version=other["definition_version"],
        requested_mode=fixture["requested_mode"],
        idempotency_key="00000000-0000-4000-8000-000000000272",
        request_fingerprint="a" * 64,
        status="running",
        started_at=now,
        deadline_at=now + timedelta(minutes=10),
    )
    async with file_db() as db:
        db.add(corrupt)
        await db.commit()
        await db.refresh(corrupt)
        run_id = corrupt.id
    await _assert_run_reads_409(client, fixture, run_id)


@pytest.mark.parametrize(
    ("definition_version", "requested_mode", "key"),
    [
        (2, "hybrid", "00000000-0000-4000-8000-000000000273"),
        (1, "knowledge", "00000000-0000-4000-8000-000000000274"),
    ],
)
@pytest.mark.asyncio
async def test_run_reads_409_when_definition_version_or_mode_is_corrupt(
    client, file_db, definition_version, requested_mode, key
):
    fixture = await seed_definition(file_db)
    now = datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc)
    corrupt = ReportRun(
        user_id=settings.dev_user_id,
        report_id=fixture["report_id"],
        definition_id=fixture["definition_id"],
        definition_version=definition_version,
        requested_mode=requested_mode,
        idempotency_key=key,
        request_fingerprint="c" * 64,
        status="running",
        started_at=now,
        deadline_at=now + timedelta(minutes=10),
    )
    async with file_db() as db:
        db.add(corrupt)
        await db.commit()
        await db.refresh(corrupt)
        run_id = corrupt.id
    await _assert_run_reads_409(client, fixture, run_id)


@pytest.mark.asyncio
async def test_run_reads_409_when_exact_definition_owner_is_foreign(client, file_db):
    fixture = await seed_definition(file_db)
    run_id = await _succeeded_run(
        file_db, fixture, "00000000-0000-4000-8000-000000000275"
    )
    async with file_db() as db:
        await db.execute(
            text("UPDATE report_definition_versions SET user_id='foreign' WHERE id=:i"),
            {"i": fixture["definition_id"]},
        )
        await db.commit()
    await _assert_run_reads_409(client, fixture, run_id)


@pytest.mark.asyncio
async def test_run_reads_409_for_real_foreign_owned_evidence_document(client, file_db):
    fixture = await seed_definition(file_db)
    async with file_db() as db:
        foreign = Document(
            user_id="foreign-owner",
            original_name="foreign.txt",
            stored_path="/tmp/foreign-test-only",
            size_bytes=7,
            status="ready",
            indexed=True,
        )
        db.add(foreign)
        await db.commit()
        await db.refresh(foreign)
        foreign_id = foreign.id
    run_id = await _succeeded_run(
        file_db,
        fixture,
        "00000000-0000-4000-8000-000000000262",
        evidence=[{
            "source_type": "document",
            "source_id": foreign_id,
            "title": "Foreign",
            "passage": "must not leak",
        }],
    )
    assert (await client.get(f"/api/reports/{fixture['report_id']}")).status_code == 200
    await _assert_run_reads_409(client, fixture, run_id)


@pytest.mark.parametrize(
    "case",
    [
        "missing_document",
        "owned_out_of_scope_document",
        "missing_equivalent_document",
        "missing_structured_source",
        "owned_out_of_scope_structured_source",
        "out_of_scope_table",
        "missing_grounded_parameter_document",
    ],
)
@pytest.mark.asyncio
async def test_run_reads_409_for_missing_or_out_of_scope_result_evidence(
    client, file_db, case
):
    fixture = await seed_definition(file_db)
    pinned_doc = fixture["pinned_document_ids"][0]
    pinned_source = next(iter(fixture["pinned_source_tables"]))
    async with file_db() as db:
        extra_doc = Document(
            user_id=settings.dev_user_id,
            original_name="extra.txt",
            stored_path="/tmp/extra-test-only",
            size_bytes=5,
            status="ready",
            indexed=True,
        )
        extra_source = DataSource(
            user_id=settings.dev_user_id,
            name="Extra",
            engine="sqlite",
            connection_secret="fixture-only-not-real",
            status="connected",
            enabled=True,
            schema_json=json.dumps([{
                "schema_name": "main",
                "name": "extra_table",
                "qualified_name": "extra_table",
                "columns": [],
                "primary_key": [],
                "foreign_keys": [],
            }]),
            authorized_objects_json=json.dumps(["extra_table"]),
        )
        db.add_all([extra_doc, extra_source])
        await db.commit()
        await db.refresh(extra_doc)
        await db.refresh(extra_source)

    evidence_by_case = {
        "missing_document": [{
            "source_type": "document", "source_id": "missing-document",
            "title": "Missing", "passage": "x",
        }],
        "owned_out_of_scope_document": [{
            "source_type": "document", "source_id": extra_doc.id,
            "title": "Extra", "passage": "x",
        }],
        "missing_equivalent_document": [{
            "source_type": "document", "source_id": pinned_doc,
            "title": "Pinned", "passage": "x",
            "provenance": {"equivalent_sources": [{"source_id": "missing-equivalent"}]},
        }],
        "missing_structured_source": [{
            "source_type": "structured_query", "source_id": "missing-source",
            "title": "Missing", "passage": "x", "metadata": {"tables": ["finance"]},
        }],
        "owned_out_of_scope_structured_source": [{
            "source_type": "structured_query", "source_id": extra_source.id,
            "title": "Extra", "passage": "x", "metadata": {"tables": ["extra_table"]},
        }],
        "out_of_scope_table": [{
            "source_type": "structured_query", "source_id": pinned_source,
            "title": "Finance", "passage": "x", "metadata": {"tables": ["other_table"]},
        }],
        "missing_grounded_parameter_document": [{
            "source_type": "structured_query", "source_id": pinned_source,
            "title": "Finance", "passage": "x", "metadata": {"tables": ["finance"]},
            "provenance": {"grounded_parameter": {"source_id": "missing-policy"}},
        }],
    }
    run_id = await _succeeded_run(
        file_db,
        fixture,
        f"00000000-0000-4000-8000-{263 + list(evidence_by_case).index(case):012d}",
        evidence=evidence_by_case[case],
    )
    await _assert_run_reads_409(client, fixture, run_id)


@pytest.mark.parametrize("case", ["empty", "unsupported_type"])
@pytest.mark.asyncio
async def test_run_reads_reject_semantically_invalid_persisted_evidence(
    client, file_db, case
):
    fixture = await seed_definition(file_db)
    evidence = [] if case == "empty" else [{
        "source_type": "internal_secret",
        "source_id": fixture["pinned_document_ids"][0],
        "title": "Invalid",
        "passage": "must not be returned",
    }]
    run_id = await _inject_succeeded_result(
        file_db,
        fixture,
        "00000000-0000-4000-8000-000000000276"
        if case == "empty"
        else "00000000-0000-4000-8000-000000000277",
        evidence,
    )
    detail = await client.get(f"/api/reports/runs/{run_id}")
    assert detail.status_code == 422, detail.text
    listing = await client.get(f"/api/reports/{fixture['report_id']}/runs")
    assert listing.status_code == 422, listing.text


# ---------------------------------------------------------------------------
# Finding 2: startup recovery durability
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_startup_recovery_persists_interruption(file_db, tmp_path):
    fixture = await seed_definition(file_db)
    async with file_db() as db:
        run, _ = await reserve_report_run(
            db=db, report_id=fixture["report_id"], definition_version=1,
            idempotency_key="00000000-0000-4000-8000-000000000220",
            requested_mode=fixture["requested_mode"], question=fixture["question"],
            pinned_document_ids=fixture["pinned_document_ids"],
            pinned_source_tables=fixture["pinned_source_tables"],
            # reserve with an already-expired wall clock: legitimate at insert
            # time and leaves the row expired for the recovery pass.
            now=datetime(2026, 10, 2, 8, 0, 0, tzinfo=timezone.utc),
        )
        await db.commit()
    engine = _make_engine(tmp_path / "b2c1.db")
    try:
        await _recover_stale_report_runs(engine=engine)
    finally:
        await engine.dispose()
    # a FRESH session (new connection) must observe the committed interruption
    async with file_db() as db:
        row = await db.get(ReportRun, run.id)
    assert row is not None
    assert row.status == "interrupted", "startup recovery did not persist the interruption"
    assert row.failure_category == "deadline_expired"
    assert row.finished_at is not None


@pytest.mark.asyncio
async def test_repeated_startup_recovery_is_idempotent(file_db, tmp_path):
    # Two definitions: the expired and live runs must not share the
    # one-active-run-per-definition partial unique index.
    fixture1 = await seed_definition(file_db)
    fixture2 = await seed_definition(file_db)
    async with file_db() as db:
        expired, _ = await reserve_report_run(
            db=db, report_id=fixture1["report_id"], definition_version=1,
            idempotency_key="00000000-0000-4000-8000-000000000221",
            requested_mode=fixture1["requested_mode"], question=fixture1["question"],
            pinned_document_ids=fixture1["pinned_document_ids"],
            pinned_source_tables=fixture1["pinned_source_tables"],
            now=datetime(2026, 10, 2, 8, 0, 0, tzinfo=timezone.utc),
        )
        live, _ = await reserve_report_run(
            db=db, report_id=fixture2["report_id"], definition_version=1,
            idempotency_key="00000000-0000-4000-8000-000000000222",
            requested_mode=fixture2["requested_mode"], question=fixture2["question"],
            pinned_document_ids=fixture2["pinned_document_ids"],
            pinned_source_tables=fixture2["pinned_source_tables"],
            # real wall clock: deadline is in the actual future, so recovery
            # must leave this run running.
            now=None,
        )
        await db.commit()
    engine = _make_engine(tmp_path / "b2c1.db")
    try:
        for _ in range(2):
            await _recover_stale_report_runs(engine=engine)
    finally:
        await engine.dispose()
    async with file_db() as db:
        expired_row = await db.get(ReportRun, expired.id)
        live_row = await db.get(ReportRun, live.id)
    assert expired_row.status == "interrupted"
    assert expired_row.failure_category == "deadline_expired"
    assert live_row.status == "running"
    assert live_row.finished_at is None


# ---------------------------------------------------------------------------
# Finding 3: reservation authority
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reservation_rejects_foreign_owner_definition(file_db):
    fixture = await seed_definition(file_db)
    async with file_db() as db:
        await db.execute(
            text("UPDATE report_definition_versions SET user_id = 'attacker' WHERE id = :i"),
            {"i": fixture["definition_id"]},
        )
        await db.commit()
    async with file_db() as db:
        with pytest.raises((ReportRunConflict, ValueError)):
            await reserve_report_run(
                db=db, report_id=fixture["report_id"], definition_version=1,
                idempotency_key=KEY_FOREIGN_DEF, requested_mode=fixture["requested_mode"],
                question=fixture["question"],
                pinned_document_ids=fixture["pinned_document_ids"],
                pinned_source_tables=fixture["pinned_source_tables"],
                now=_Clock(datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc)),
            )


@pytest.mark.asyncio
async def test_reservation_rejects_foreign_owner_report(file_db):
    fixture = await seed_definition(file_db)
    async with file_db() as db:
        await db.execute(
            text("UPDATE saved_reports SET user_id = 'attacker' WHERE id = :i"),
            {"i": fixture["report_id"]},
        )
        await db.commit()
    async with file_db() as db:
        with pytest.raises((ReportRunConflict, ValueError)):
            await reserve_report_run(
                db=db, report_id=fixture["report_id"], definition_version=1,
                idempotency_key=KEY_FOREIGN_REPORT, requested_mode=fixture["requested_mode"],
                question=fixture["question"],
                pinned_document_ids=fixture["pinned_document_ids"],
                pinned_source_tables=fixture["pinned_source_tables"],
                now=_Clock(datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc)),
            )


@pytest.mark.asyncio
async def test_reservation_rejects_altered_caller_intent(file_db):
    """Altered caller-supplied intent must not be accepted for reservation."""
    fixture = await seed_definition(file_db)
    async with file_db() as db:
        with pytest.raises((ReportRunConflict, ValueError)):
            await reserve_report_run(
                db=db, report_id=fixture["report_id"], definition_version=1,
                idempotency_key=KEY_ALTERED_INTENT,
                requested_mode="knowledge",  # definition pins hybrid
                question="Totally different attacker question",
                pinned_document_ids=[], pinned_source_tables={},
                now=_Clock(datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc)),
            )


@pytest.mark.asyncio
async def test_reservation_rejects_altered_pins(file_db):
    fixture = await seed_definition(file_db)
    async with file_db() as db:
        with pytest.raises((ReportRunConflict, ValueError)):
            await reserve_report_run(
                db=db, report_id=fixture["report_id"], definition_version=1,
                idempotency_key="00000000-0000-4000-8000-000000000209",
                requested_mode=fixture["requested_mode"], question=fixture["question"],
                pinned_document_ids=[],  # definition pins one document
                pinned_source_tables={},
                now=_Clock(datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc)),
            )


# ---------------------------------------------------------------------------
# Finding 4: DB-trigger owner immutability for RUNNING rows
# ---------------------------------------------------------------------------

@pytest.fixture
async def trigger_engine(tmp_path):
    engine = _make_engine(tmp_path / "trigger-regr.db")
    event.listen(engine.sync_engine, "connect", _pragmas)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            for statement in _REPORT_RUN_IMMUTABILITY_TRIGGERS_SQL:
                await conn.exec_driver_sql(statement)
        yield engine
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_trigger_blocks_owner_change_on_running_run(trigger_engine):
    maker = async_sessionmaker(trigger_engine, expire_on_commit=False)
    fixture = await seed_definition(maker)
    async with maker() as db:
        run, _ = await reserve_report_run(
            db=db, report_id=fixture["report_id"], definition_version=1,
            idempotency_key="00000000-0000-4000-8000-000000000230",
            requested_mode=fixture["requested_mode"], question=fixture["question"],
            pinned_document_ids=fixture["pinned_document_ids"],
            pinned_source_tables=fixture["pinned_source_tables"],
            now=_Clock(datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc)),
        )
        await db.commit()
    async with maker() as db:
        row = await db.get(ReportRun, run.id)
        assert row.status == "running"
        with pytest.raises(Exception):
            await db.execute(
                text("UPDATE report_runs SET user_id = 'attacker' WHERE id = :i"),
                {"i": run.id},
            )
        await db.rollback()
    async with maker() as db:
        row = await db.get(ReportRun, run.id)
    assert row.user_id == settings.dev_user_id
    assert row.status == "running"


@pytest.mark.asyncio
async def test_corrected_triggers_replace_older_deployed_triggers(tmp_path):
    """Upgraded databases must not keep the old (defective) trigger bodies:
    the startup trigger install drops and recreates each trigger."""
    engine = _make_engine(tmp_path / "upgrade.db")
    event.listen(engine.sync_engine, "connect", _pragmas)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            # install the OLD defective identity trigger, as a pre-fix deployment has
            await conn.exec_driver_sql(
                "CREATE TRIGGER trg_report_runs_protect_identity "
                "BEFORE UPDATE ON report_runs FOR EACH ROW BEGIN SELECT RAISE(ABORT, 'no'); END;"
            )
        from app.db import install_report_run_triggers

        async with engine.begin() as conn:
            await install_report_run_triggers(conn)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        fixture = await seed_definition(maker)
        async with maker() as db:
            run, _ = await reserve_report_run(
                db=db, report_id=fixture["report_id"], definition_version=1,
                idempotency_key="00000000-0000-4000-8000-000000000231",
                requested_mode=fixture["requested_mode"], question=fixture["question"],
                pinned_document_ids=fixture["pinned_document_ids"],
                pinned_source_tables=fixture["pinned_source_tables"],
                now=_Clock(datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc)),
            )
            await db.commit()
            with pytest.raises(Exception):
                await db.execute(
                    text("UPDATE report_runs SET user_id = 'attacker' WHERE id = :i"),
                    {"i": run.id},
                )
            await db.rollback()
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Finding 5: read bound must match the persisted write bound
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_large_valid_result_reads_back(client, file_db):
    """A persisted result between the old 65,536 read cap and the 131,072
    write bound (69,172 bytes) must read back without 422."""
    fixture = await seed_definition(file_db)
    clock = _Clock(datetime.now(timezone.utc))
    async with file_db() as db:
        run, _ = await reserve_report_run(
            db=db, report_id=fixture["report_id"], definition_version=1,
            idempotency_key=KEY_LARGE, requested_mode=fixture["requested_mode"],
            question=fixture["question"],
            pinned_document_ids=fixture["pinned_document_ids"],
            pinned_source_tables=fixture["pinned_source_tables"], now=clock,
        )
        await db.commit()
        # Build a valid result whose serialized size is exactly 69,172 bytes:
        # above the old 65,536 read cap, below the 131,072 write bound. The
        # bulk lives in structured_result (bounded only by the aggregate).
        evidence = [{
            "source_type": "document",
            "source_id": fixture["pinned_document_ids"][0],
            "title": "t",
            "passage": "x",
        }]
        answer = "Answer with a long body"

        def aggregate_bytes(pad_len: int) -> int:
            payload = {
                "answer": answer,
                "evidence": evidence,
                "structured_result": {"pad": "y" * pad_len},
                "trace_ids": ["t-1"],
            }
            return len(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))

        target = 69172
        base = aggregate_bytes(0)
        assert base < target
        pad_len = target - base
        assert aggregate_bytes(pad_len) == target

        count = await finalize_success(
            db=db, run_id=run.id, user_id=settings.dev_user_id,
            fingerprint=run.request_fingerprint,
            answer=answer,
            evidence=evidence,
            structured_result={"pad": "y" * pad_len},
            trace_ids=["t-1"],
        )
        assert count == 1, "finalize rejected a size the write bound permits"
        await db.commit()
        size = row_size = None
    async with file_db() as db:
        row = await db.get(ReportRun, run.id)
        assert row is not None
        assert row.result_size_bytes == 69172, row.result_size_bytes
        assert row.result_size_bytes > 65536
        assert row.result_size_bytes <= MAX_RESULT_BYTES
        size = row.result_size_bytes
    resp = await client.get(f"/api/reports/runs/{run.id}")
    assert resp.status_code == 200, (
        f"valid persisted result ({size} bytes) failed to read: HTTP {resp.status_code}"
    )
    assert resp.json()["result"]["answer"].startswith("Answer with a long body")


@pytest.mark.asyncio
async def test_oversized_result_injected_directly_still_422(client, file_db):
    """Defense in depth: a result envelope exceeding the shared bound that
    somehow reached storage is still refused at read time."""
    import hashlib

    fixture = await seed_definition(file_db)
    clock = _Clock(datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc))
    async with file_db() as db:
        run, _ = await reserve_report_run(
            db=db, report_id=fixture["report_id"], definition_version=1,
            idempotency_key="00000000-0000-4000-8000-000000000240",
            requested_mode=fixture["requested_mode"], question=fixture["question"],
            pinned_document_ids=fixture["pinned_document_ids"],
            pinned_source_tables=fixture["pinned_source_tables"], now=clock,
        )
        await db.commit()
        payload = json.dumps({"answer": "A" * 200000, "evidence": [], "structured_result": {}, "trace_ids": []})
        await db.execute(
            text(
                "UPDATE report_runs SET status='succeeded', "
                "finished_at='2026-10-02 09:05:00.000000', result_json=:r, "
                "result_size_bytes=:s, result_sha256=:h WHERE id=:i"
            ),
            {
                "r": payload,
                "s": len(payload.encode("utf-8")),
                "h": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                "i": run.id,
            },
        )
        await db.commit()
    resp = await client.get(f"/api/reports/runs/{run.id}")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Contract gap: _canonical_key rejection of non-canonical UUID forms
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "bad",
    [
        "{00000000-0000-4000-8000-000000000301}",
        "00000000-0000-4000-8000-00000000030F",
        "00000000000000000000000000000301",
        "urn:uuid:00000000-0000-4000-8000-000000000301",
        " 00000000-0000-4000-8000-000000000301",
        "00000000-0000-4000-8000-000000000301\n",
        "00000000-0000-4000-8000-000000000301 ",
        "0000000-00000-4000-8000-0000000000301",
        "NOT-A-UUID",
        "",
    ],
)
def test_canonical_key_rejects_non_canonical_forms(bad):
    with pytest.raises(ValueError):
        _canonical_key(bad)


def test_canonical_key_accepts_canonical_lowercase():
    good = "00000000-0000-4000-8000-000000000301"
    assert _canonical_key(good) == good


# ---------------------------------------------------------------------------
# Read sentinels: zero model/retrieval/source-SQL/trace-write on GET reads
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_history_reads_have_zero_execution_side_effects(client, file_db, monkeypatch):
    fixture = await seed_definition(file_db)
    run_id = await _succeeded_run(file_db, fixture, "00000000-0000-4000-8000-000000000210")
    calls = []

    def forbid(name):
        def _fail(*a, **k):
            calls.append(name)
            raise AssertionError(f"history read invoked {name}")

        return _fail

    import app.services.execution_trace as et
    import app.services.knowledge as kn
    import app.services.model_gateway as mg
    import app.services.structured_executor as se

    monkeypatch.setattr(se, "execute_structured_query", forbid("structured_query"))
    monkeypatch.setattr(et, "start_execution_trace", forbid("start_execution_trace"))
    monkeypatch.setattr(et, "complete_execution_trace", forbid("complete_execution_trace"))
    monkeypatch.setattr(mg, "OpenAICompatibleModelGateway", forbid("model_gateway"))
    monkeypatch.setattr(kn, "DBGPTKnowledgeEngine", forbid("knowledge_engine"))

    async with file_db() as db:
        traces_before = (
            await db.execute(text("SELECT count(*) FROM execution_traces"))
        ).scalar_one()
        messages_before = (
            await db.execute(text("SELECT count(*) FROM messages"))
        ).scalar_one()

    resp = await client.get(f"/api/reports/{fixture['report_id']}/runs")
    assert resp.status_code == 200
    resp = await client.get(f"/api/reports/runs/{run_id}")
    assert resp.status_code == 200

    async with file_db() as db:
        traces_after = (
            await db.execute(text("SELECT count(*) FROM execution_traces"))
        ).scalar_one()
        messages_after = (
            await db.execute(text("SELECT count(*) FROM messages"))
        ).scalar_one()
    assert calls == [], f"reads triggered forbidden execution calls: {calls}"
    assert traces_after == traces_before, "reads wrote execution traces"
    assert messages_after == messages_before, "reads wrote messages"


# ---------------------------------------------------------------------------
# Synthetic pre-B2C1 upgrade, preservation, and deletion/no-resurrection
# ---------------------------------------------------------------------------

_LEGACY_SNAPSHOT_QUERIES = {
    "conversations": (
        "SELECT id,user_id,title,created_at,updated_at FROM conversations ORDER BY id"
    ),
    "messages": (
        "SELECT id,conversation_id,role,content,execution_class,requested_mode,"
        "evidence_json,created_at FROM messages ORDER BY id"
    ),
    "execution_traces": (
        "SELECT id,request_id,tool_invocation_id,user_id,conversation_id,route,"
        "requested_mode,tool_name,operation_class,risk_level,requires_approval,"
        "source_id,model_name,input_hash,planned_sql,executed_sql,validation_decision,"
        "policy_decision,row_limit,duration_ms,status,error_class,evidence_ids_json,"
        "processing_location,metadata_json,created_at,completed_at "
        "FROM execution_traces ORDER BY id"
    ),
    "saved_reports": (
        "SELECT id,user_id,conversation_id,message_id,title,answer_text,evidence_json,"
        "execution_class,requested_mode,source_count,snapshot_as_of,created_at "
        "FROM saved_reports ORDER BY id"
    ),
    "report_definition_versions": (
        "SELECT id,report_id,user_id,version,question_text,requested_mode,"
        "pinned_document_ids_json,pinned_source_tables_json,created_at "
        "FROM report_definition_versions ORDER BY id"
    ),
}


async def _legacy_snapshot(path) -> dict[str, list[tuple]]:
    """Read preservation-critical rows through a new independent engine."""
    engine = _make_engine(path)
    event.listen(engine.sync_engine, "connect", _pragmas)
    try:
        async with engine.connect() as conn:
            return {
                table: [tuple(row) for row in (await conn.exec_driver_sql(query)).all()]
                for table, query in _LEGACY_SNAPSHOT_QUERIES.items()
            }
    finally:
        await engine.dispose()


async def _assert_run_schema(path) -> None:
    engine = _make_engine(path)
    event.listen(engine.sync_engine, "connect", _pragmas)
    try:
        async with engine.connect() as conn:
            columns = {
                row[1] for row in (await conn.exec_driver_sql("PRAGMA table_info(report_runs)")).all()
            }
            assert columns == {
                "id", "user_id", "report_id", "definition_id", "definition_version",
                "requested_mode", "idempotency_key", "request_fingerprint", "status",
                "started_at", "deadline_at", "finished_at", "failure_category",
                "trace_ids_json", "result_json", "result_size_bytes", "result_sha256",
                "created_at",
            }
            foreign_keys = {
                (row[3], row[2], row[4], row[6])
                for row in (await conn.exec_driver_sql("PRAGMA foreign_key_list(report_runs)")).all()
            }
            assert ("report_id", "saved_reports", "id", "CASCADE") in foreign_keys
            assert (
                "definition_id", "report_definition_versions", "id", "CASCADE"
            ) in foreign_keys

            indexes = (await conn.exec_driver_sql("PRAGMA index_list(report_runs)")).all()
            unique_columns = set()
            for index in indexes:
                if index[2]:
                    info = await conn.exec_driver_sql(f'PRAGMA index_info("{index[1]}")')
                    unique_columns.add(tuple(row[2] for row in info.all()))
            assert ("user_id", "idempotency_key") in unique_columns

            create_sql = (
                await conn.exec_driver_sql(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name='report_runs'"
                )
            ).scalar_one()
            for constraint in (
                "ck_report_run_status",
                "ck_report_run_running_unfinished",
                "ck_report_run_running_no_failure",
                "ck_report_run_terminal_finished",
                "ck_report_run_succeeded_has_result",
                "ck_report_run_failed_has_category",
            ):
                assert constraint in create_sql

            triggers = dict(
                (await conn.exec_driver_sql(
                    "SELECT name,sql FROM sqlite_master "
                    "WHERE type='trigger' AND tbl_name='report_runs'"
                )).all()
            )
            assert set(triggers) == {
                "trg_report_runs_protect_identity",
                "trg_report_runs_terminal_immutable",
            }
            assert "NEW.user_id IS NOT OLD.user_id" in triggers["trg_report_runs_protect_identity"]
            assert "OLD.status IN ('succeeded','failed','interrupted')" in triggers[
                "trg_report_runs_terminal_immutable"
            ]
            normalize_sql = lambda value: " ".join(value.split()).casefold().rstrip(";")
            assert {normalize_sql(value) for value in triggers.values()} == {
                normalize_sql(value) for value in _REPORT_RUN_IMMUTABILITY_TRIGGERS_SQL
            }
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_synthetic_pre_b2c1_upgrade_preserves_data(tmp_path, monkeypatch):
    """Production init_db(), run twice, preserves a populated pre-B2C1 file."""
    path = tmp_path / "pre-b2c1.db"
    old_engine = _make_engine(path)
    event.listen(old_engine.sync_engine, "connect", _pragmas)
    try:
        async with old_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.exec_driver_sql("DROP TABLE report_runs")
        old_maker = async_sessionmaker(old_engine, expire_on_commit=False)
        fixture = await seed_definition(old_maker)
        async with old_maker() as db:
            conversation_id = (
                await db.execute(
                    text("SELECT conversation_id FROM saved_reports WHERE id=:i"),
                    {"i": fixture["report_id"]},
                )
            ).scalar_one()
            db.add(ExecutionTrace(
                id="00000000-0000-4000-8000-000000000280",
                request_id="00000000-0000-4000-8000-000000000281",
                tool_invocation_id="00000000-0000-4000-8000-000000000282",
                user_id=settings.dev_user_id,
                conversation_id=conversation_id,
                route="hybrid",
                requested_mode="hybrid",
                tool_name="structured.query",
                operation_class="read",
                risk_level="low",
                requires_approval=False,
                source_id=next(iter(fixture["pinned_source_tables"])),
                model_name="synthetic-pre-b2c1",
                input_hash="b" * 64,
                planned_sql="SELECT revenue FROM finance WHERE revenue > 300",
                executed_sql="SELECT revenue FROM finance WHERE revenue > 300 LIMIT 200",
                validation_decision="allowed",
                policy_decision="allowed",
                row_limit=200,
                duration_ms=12,
                status="completed",
                evidence_ids_json=json.dumps(fixture["pinned_document_ids"]),
                processing_location="local",
                metadata_json=json.dumps({"tables": ["finance"], "rows": 1}),
            ))
            await db.commit()
    finally:
        await old_engine.dispose()

    before = await _legacy_snapshot(path)
    assert len(before["conversations"]) == 1
    assert len(before["messages"]) == 2
    assert len(before["execution_traces"]) == 1
    assert len(before["saved_reports"]) == 1
    assert len(before["report_definition_versions"]) == 1
    conversation_id = before["conversations"][0][0]
    messages_by_role = {row[2]: row for row in before["messages"]}
    assistant_row = messages_by_role["assistant"]
    trace_row = before["execution_traces"][0]
    report_row = before["saved_reports"][0]
    definition_row = before["report_definition_versions"][0]
    assert all(row[1] == conversation_id for row in before["messages"])
    assert trace_row[4] == conversation_id
    assert report_row[2] == conversation_id
    assert report_row[3] == assistant_row[0]
    assert json.loads(report_row[6]) == json.loads(assistant_row[6])
    assert definition_row[1] == report_row[0]
    assert json.loads(definition_row[6]) == fixture["pinned_document_ids"]
    assert json.loads(definition_row[7]) == fixture["pinned_source_tables"]
    assert json.loads(trace_row[22]) == fixture["pinned_document_ids"]
    assert json.loads(trace_row[24]) == {"tables": ["finance"], "rows": 1}
    assert next(iter(fixture["pinned_source_tables"])) == trace_row[11]

    import app.db as db_module

    startup_engine = _make_engine(path)
    event.listen(startup_engine.sync_engine, "connect", _pragmas)
    monkeypatch.setattr(db_module, "engine", startup_engine)
    startup_maker = async_sessionmaker(startup_engine, expire_on_commit=False)
    try:
        await db_module.init_db()
        assert await _legacy_snapshot(path) == before
        await _assert_run_schema(path)

        async with startup_maker() as db:
            expired, _ = await reserve_report_run(
                db=db, report_id=fixture["report_id"], definition_version=1,
                idempotency_key="00000000-0000-4000-8000-000000000283",
                requested_mode=fixture["requested_mode"], question=fixture["question"],
                pinned_document_ids=fixture["pinned_document_ids"],
                pinned_source_tables=fixture["pinned_source_tables"],
                now=datetime(2026, 10, 2, 8, 0, 0, tzinfo=timezone.utc),
            )
            await db.commit()
        # Recovery marks the expired run 'interrupted' (terminal), clearing
        # the one-active-run slot so the live run can be reserved.
        await _recover_stale_report_runs(engine=startup_engine)
        async with startup_maker() as db:
            live, _ = await reserve_report_run(
                db=db, report_id=fixture["report_id"], definition_version=1,
                idempotency_key="00000000-0000-4000-8000-000000000284",
                requested_mode=fixture["requested_mode"], question=fixture["question"],
                pinned_document_ids=fixture["pinned_document_ids"],
                pinned_source_tables=fixture["pinned_source_tables"], now=None,
            )
            await db.commit()
        expired_id, live_id = expired.id, live.id

        await db_module.init_db()
        assert await _legacy_snapshot(path) == before
        await _assert_run_schema(path)

        verify_engine = _make_engine(path)
        event.listen(verify_engine.sync_engine, "connect", _pragmas)
        try:
            async with verify_engine.connect() as conn:
                expired_row = (
                    await conn.exec_driver_sql(
                        "SELECT status,failure_category,finished_at FROM report_runs WHERE id=?",
                        (expired_id,),
                    )
                ).one()
                live_row = (
                    await conn.exec_driver_sql(
                        "SELECT status,failure_category,finished_at FROM report_runs WHERE id=?",
                        (live_id,),
                    )
                ).one()
                assert expired_row[0] == "interrupted"
                assert expired_row[1] == "deadline_expired"
                assert expired_row[2] is not None
                assert tuple(live_row) == ("running", None, None)

                with pytest.raises(Exception):
                    await conn.exec_driver_sql(
                        "UPDATE report_runs SET user_id='attacker' WHERE id=?", (live_id,)
                    )
                await conn.rollback()
                with pytest.raises(Exception):
                    await conn.exec_driver_sql(
                        "UPDATE report_runs SET status='running',failure_category=NULL,"
                        "finished_at=NULL WHERE id=?",
                        (expired_id,),
                    )
                await conn.rollback()
        finally:
            await verify_engine.dispose()
    finally:
        await startup_engine.dispose()


@pytest.mark.asyncio
async def test_report_deletion_cascades_runs_and_never_resurrects(tmp_path):
    """Deleting a report removes its runs through the reviewed FK; a later
    startup recovery must not resurrect any run history."""
    path = tmp_path / "resurrection.db"
    engine = _make_engine(path)
    event.listen(engine.sync_engine, "connect", _pragmas)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            from app.db import install_report_run_triggers

            await install_report_run_triggers(conn)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        fixture = await seed_definition(maker)
        async with maker() as db:
            run, _ = await reserve_report_run(
                db=db, report_id=fixture["report_id"], definition_version=1,
                idempotency_key="00000000-0000-4000-8000-000000000250",
                requested_mode=fixture["requested_mode"], question=fixture["question"],
                pinned_document_ids=fixture["pinned_document_ids"],
                pinned_source_tables=fixture["pinned_source_tables"],
                now=datetime(2026, 10, 2, 8, 0, 0, tzinfo=timezone.utc),
            )
            await db.commit()
            # delete the parent report; FK cascade must remove the run
            await db.execute(
                text("DELETE FROM saved_reports WHERE id = :i"), {"i": fixture["report_id"]}
            )
            await db.commit()
            remaining = (
                await db.execute(text("SELECT count(*) FROM report_runs"))
            ).scalar_one()
            assert remaining == 0, "run rows survived parent report deletion"
        await _recover_stale_report_runs(engine=engine)
        async with maker() as db:
            remaining = (
                await db.execute(text("SELECT count(*) FROM report_runs"))
            ).scalar_one()
        assert remaining == 0, "startup recovery resurrected deleted run history"
        # reserving against the deleted definition must fail closed
        async with maker() as db:
            with pytest.raises((ReportRunConflict, ValueError)):
                await reserve_report_run(
                    db=db, report_id=fixture["report_id"], definition_version=1,
                    idempotency_key="00000000-0000-4000-8000-000000000251",
                    requested_mode=fixture["requested_mode"], question=fixture["question"],
                    pinned_document_ids=fixture["pinned_document_ids"],
                    pinned_source_tables=fixture["pinned_source_tables"],
                    now=_Clock(datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc)),
                )
    finally:
        await engine.dispose()
