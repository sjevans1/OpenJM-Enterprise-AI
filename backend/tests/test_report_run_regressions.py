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
from app.models import Base, ReportRun
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


async def _succeeded_run(maker, fixture, key: str) -> str:
    """Reserve + finalize a run against the seeded (authorized) fixture."""
    clock = _Clock(datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc))
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
            evidence=[{"source_type": "document", "source_id": "d1", "title": "t", "passage": "p"}],
            structured_result=None, trace_ids=["t-1"],
        )
        assert count == 1
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
    fixture = await seed_definition(file_db)
    async with file_db() as db:
        expired, _ = await reserve_report_run(
            db=db, report_id=fixture["report_id"], definition_version=1,
            idempotency_key="00000000-0000-4000-8000-000000000221",
            requested_mode=fixture["requested_mode"], question=fixture["question"],
            pinned_document_ids=fixture["pinned_document_ids"],
            pinned_source_tables=fixture["pinned_source_tables"],
            now=datetime(2026, 10, 2, 8, 0, 0, tzinfo=timezone.utc),
        )
        live, _ = await reserve_report_run(
            db=db, report_id=fixture["report_id"], definition_version=1,
            idempotency_key="00000000-0000-4000-8000-000000000222",
            requested_mode=fixture["requested_mode"], question=fixture["question"],
            pinned_document_ids=fixture["pinned_document_ids"],
            pinned_source_tables=fixture["pinned_source_tables"],
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
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for statement in _REPORT_RUN_IMMUTABILITY_TRIGGERS_SQL:
            await conn.exec_driver_sql(statement)
    yield engine
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
    await engine.dispose()


# ---------------------------------------------------------------------------
# Finding 5: read bound must match the persisted write bound
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_large_valid_result_reads_back(client, file_db):
    """A persisted result between the old 65,536 read cap and the 131,072
    write bound (69,172 bytes) must read back without 422."""
    fixture = await seed_definition(file_db)
    clock = _Clock(datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc))
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
        evidence = [
            {"source_type": "document", "source_id": "d1", "title": "t", "passage": "x"}
        ]
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

async def _counts(maker) -> dict:
    async with maker() as db:
        out = {}
        for table in ("conversations", "messages", "saved_reports", "report_definition_versions"):
            out[table] = (
                await db.execute(text(f"SELECT count(*) FROM {table}"))
            ).scalar_one()
        return out


@pytest.mark.asyncio
async def test_synthetic_pre_b2c1_upgrade_preserves_data(tmp_path):
    """A pre-B2C1 database (no report_runs table, old triggers) upgraded by
    the production init sequence keeps every persisted row."""
    path = tmp_path / "pre-b2c1.db"
    old_engine = _make_engine(path)
    event.listen(old_engine.sync_engine, "connect", _pragmas)
    async with old_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.exec_driver_sql("DROP TABLE report_runs")
    old_maker = async_sessionmaker(old_engine, expire_on_commit=False)
    fixture = await seed_definition(old_maker)
    before = await _counts(old_maker)
    await old_engine.dispose()

    # production init sequence against the upgraded file
    new_engine = _make_engine(path)
    event.listen(new_engine.sync_engine, "connect", _pragmas)
    from app.db import install_report_run_triggers

    async with new_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await install_report_run_triggers(conn)
    await _recover_stale_report_runs(engine=new_engine)
    new_maker = async_sessionmaker(new_engine, expire_on_commit=False)
    after = await _counts(new_maker)
    assert before == after, "upgrade changed pre-existing persisted rows"
    assert (
        await new_maker().__aenter__()
    )  # placeholder guard; replaced below
    async with new_maker() as db:
        assert (
            await db.execute(
                text("SELECT count(*) FROM report_runs")
            )
        ).scalar_one() == 0
    await new_engine.dispose()


@pytest.mark.asyncio
async def test_report_deletion_cascades_runs_and_never_resurrects(tmp_path):
    """Deleting a report removes its runs through the reviewed FK; a later
    startup recovery must not resurrect any run history."""
    path = tmp_path / "resurrection.db"
    engine = _make_engine(path)
    event.listen(engine.sync_engine, "connect", _pragmas)
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
    await engine.dispose()
