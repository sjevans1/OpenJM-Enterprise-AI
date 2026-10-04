"""VS4-B2C1: owner-scoped read-only history API + revocation controls.

Tests for the service layer (list/get/revoke) and the HTTP API. Written first
(TDD RED) — the service functions list/get/revoke and the report_runs router do
not exist yet.
"""

from datetime import datetime, timezone

import pytest
from httpx import AsyncClient

from app.core.config import get_settings
from app.models import ReportRun
from app.services.report_runs import (
    finalize_success,
    get_report_run,
    list_report_runs,
    reserve_report_run,
    revoke_report_run,
)

from conftest import _Clock, seed_definition  # shared B2C1 test helpers

settings = get_settings()


# ---------------------------------------------------------------------------
# Service-layer: list, get, revoke
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_report_runs_owner_scoped(file_db):
    fixture = await seed_definition(file_db)
    clock = _Clock(datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc))
    async with file_db() as db:
        await reserve_report_run(
            db=db,
            report_id=fixture["report_id"],
            definition_version=1,
            idempotency_key="00000000-0000-4000-8000-000000000100",
            requested_mode=fixture["requested_mode"],
            question=fixture["question"],
            pinned_document_ids=fixture["pinned_document_ids"],
            pinned_source_tables=fixture["pinned_source_tables"],
            now=clock,
        )
        await db.commit()
    async with file_db() as db:
        runs = await list_report_runs(
            db,
            settings.dev_user_id,
            fixture["report_id"],
            limit=20,
            offset=0,
        )
    assert len(runs) == 1
    assert all(r.user_id == settings.dev_user_id for r in runs)


@pytest.mark.asyncio
async def test_list_report_runs_excludes_other_reports(file_db):
    ours = await seed_definition(file_db)
    other = await seed_definition(file_db)
    clock = _Clock(datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc))
    async with file_db() as db:
        await reserve_report_run(
            db=db,
            report_id=other["report_id"],
            definition_version=1,
            idempotency_key="00000000-0000-4000-8000-000000000101",
            requested_mode=other["requested_mode"],
            question=other["question"],
            pinned_document_ids=other["pinned_document_ids"],
            pinned_source_tables=other["pinned_source_tables"],
            now=clock,
        )
        await db.commit()
    async with file_db() as db:
        runs = await list_report_runs(
            db, settings.dev_user_id, ours["report_id"], limit=20, offset=0
        )
    assert len(runs) == 0


@pytest.mark.asyncio
async def test_list_report_runs_pagination(file_db):
    fixture = await seed_definition(file_db)
    clock = _Clock(datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc))
    ids = []
    async with file_db() as db:
        for i in range(3):
            run, _ = await reserve_report_run(
                db=db,
                report_id=fixture["report_id"],
                definition_version=1,
                idempotency_key=f"00000000-0000-4000-8000-{i:012d}",
                requested_mode=fixture["requested_mode"],
                question=fixture["question"],
                pinned_document_ids=fixture["pinned_document_ids"],
                pinned_source_tables=fixture["pinned_source_tables"],
                now=clock,
            )
            ids.append(run.id)
        await db.commit()
    async with file_db() as db:
        first = await list_report_runs(
            db, settings.dev_user_id, fixture["report_id"], limit=2, offset=0
        )
        second = await list_report_runs(
            db, settings.dev_user_id, fixture["report_id"], limit=2, offset=2
        )
    assert len(first) == 2
    assert len(second) == 1
    assert first[0].id != second[0].id
    assert second[0].id in ids


@pytest.mark.asyncio
async def test_get_report_run_owner_scoped(file_db):
    fixture = await seed_definition(file_db)
    clock = _Clock(datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc))
    async with file_db() as db:
        run, _ = await reserve_report_run(
            db=db,
            report_id=fixture["report_id"],
            definition_version=1,
            idempotency_key="00000000-0000-4000-8000-000000000102",
            requested_mode=fixture["requested_mode"],
            question=fixture["question"],
            pinned_document_ids=fixture["pinned_document_ids"],
            pinned_source_tables=fixture["pinned_source_tables"],
            now=clock,
        )
        await db.commit()
    async with file_db() as db:
        found = await get_report_run(db, settings.dev_user_id, run.id)
    assert found is not None
    assert found.id == run.id
    assert found.report_id == fixture["report_id"]


@pytest.mark.asyncio
async def test_get_report_run_rejects_unknown_owner(file_db):
    fixture = await seed_definition(file_db)
    clock = _Clock(datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc))
    async with file_db() as db:
        run, _ = await reserve_report_run(
            db=db,
            report_id=fixture["report_id"],
            definition_version=1,
            idempotency_key="00000000-0000-4000-8000-000000000103",
            requested_mode=fixture["requested_mode"],
            question=fixture["question"],
            pinned_document_ids=fixture["pinned_document_ids"],
            pinned_source_tables=fixture["pinned_source_tables"],
            now=clock,
        )
        await db.commit()
    async with file_db() as db:
        found = await get_report_run(db, "not-the-owner", run.id)
    assert found is None


@pytest.mark.asyncio
async def test_revoke_transitions_running_to_interrupted(file_db):
    fixture = await seed_definition(file_db)
    clock = _Clock(datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc))
    async with file_db() as db:
        run, _ = await reserve_report_run(
            db=db,
            report_id=fixture["report_id"],
            definition_version=1,
            idempotency_key="00000000-0000-4000-8000-000000000104",
            requested_mode=fixture["requested_mode"],
            question=fixture["question"],
            pinned_document_ids=fixture["pinned_document_ids"],
            pinned_source_tables=fixture["pinned_source_tables"],
            now=clock,
        )
        await db.commit()
    async with file_db() as db:
        count = await revoke_report_run(db, run.id, settings.dev_user_id)
        await db.commit()
    assert count == 1
    async with file_db() as db:
        row = await db.get(ReportRun, run.id)
    assert row.status == "interrupted"
    assert row.failure_category == "revoked"
    assert row.finished_at is not None


@pytest.mark.asyncio
async def test_revoke_rejects_terminal_run(file_db):
    fixture = await seed_definition(file_db)
    clock = _Clock(datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc))
    async with file_db() as db:
        run, _ = await reserve_report_run(
            db=db,
            report_id=fixture["report_id"],
            definition_version=1,
            idempotency_key="00000000-0000-4000-8000-000000000105",
            requested_mode=fixture["requested_mode"],
            question=fixture["question"],
            pinned_document_ids=fixture["pinned_document_ids"],
            pinned_source_tables=fixture["pinned_source_tables"],
            now=clock,
        )
        await db.commit()
        await finalize_success(
            db=db,
            run_id=run.id,
            user_id=settings.dev_user_id,
            fingerprint=run.request_fingerprint,
            answer="A",
            evidence=[
                {
                    "source_type": "document",
                    "source_id": "x",
                    "title": "t",
                    "passage": "p",
                }
            ],
            structured_result=None,
            trace_ids=[],
        )
        await db.commit()
        count = await revoke_report_run(db, run.id, settings.dev_user_id)
        await db.commit()
    assert count == 0
    async with file_db() as db:
        row = await db.get(ReportRun, run.id)
    assert row.status == "succeeded"


@pytest.mark.asyncio
async def test_revoke_rejects_foreign_owner(file_db):
    fixture = await seed_definition(file_db)
    clock = _Clock(datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc))
    async with file_db() as db:
        run, _ = await reserve_report_run(
            db=db,
            report_id=fixture["report_id"],
            definition_version=1,
            idempotency_key="00000000-0000-4000-8000-000000000106",
            requested_mode=fixture["requested_mode"],
            question=fixture["question"],
            pinned_document_ids=fixture["pinned_document_ids"],
            pinned_source_tables=fixture["pinned_source_tables"],
            now=clock,
        )
        await db.commit()
    async with file_db() as db:
        count = await revoke_report_run(db, run.id, "not-the-owner")
        await db.commit()
    assert count == 0


# ---------------------------------------------------------------------------
# HTTP API: owner-scoped read-only history + revocation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_list_runs_for_owner(file_db, client):
    fixture = await seed_definition(file_db)
    clock = _Clock(datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc))
    async with file_db() as db:
        await reserve_report_run(
            db=db,
            report_id=fixture["report_id"],
            definition_version=1,
            idempotency_key="00000000-0000-4000-8000-000000000110",
            requested_mode=fixture["requested_mode"],
            question=fixture["question"],
            pinned_document_ids=fixture["pinned_document_ids"],
            pinned_source_tables=fixture["pinned_source_tables"],
            now=clock,
        )
        await db.commit()
    resp = await client.get(f"/api/reports/{fixture['report_id']}/runs")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) == 1


@pytest.mark.asyncio
async def test_api_list_runs_excludes_other_owner_404(file_db, client):
    # A report_id the owner does not own -> 404 (fail closed on listing scope).
    resp = await client.get("/api/reports/nonexistent-report/runs")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_api_get_run_detail(file_db, client):
    fixture = await seed_definition(file_db)
    clock = _Clock(datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc))
    async with file_db() as db:
        run, _ = await reserve_report_run(
            db=db,
            report_id=fixture["report_id"],
            definition_version=1,
            idempotency_key="00000000-0000-4000-8000-000000000111",
            requested_mode=fixture["requested_mode"],
            question=fixture["question"],
            pinned_document_ids=fixture["pinned_document_ids"],
            pinned_source_tables=fixture["pinned_source_tables"],
            now=clock,
        )
        await finalize_success(
            db=db,
            run_id=run.id,
            user_id=settings.dev_user_id,
            fingerprint=run.request_fingerprint,
            answer="Delta exceeded",
            evidence=[
                {
                    "source_type": "document",
                    "source_id": fixture["pinned_document_ids"][0],
                    "title": "Policy",
                    "passage": "300",
                }
            ],
            structured_result={
                "source_id": next(iter(fixture["pinned_source_tables"])),
                "evidence_id": "t-1",
                "sql": "SELECT revenue FROM finance",
                "columns": ["revenue"],
                "rows": [[325]],
                "row_count": 1,
                "truncated": False,
            },
            trace_ids=["t-1"],
        )
        await db.commit()
    resp = await client.get(f"/api/reports/runs/{run.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "succeeded"
    assert body["result"]["answer"] == "Delta exceeded"
    assert body["result"]["structured_result"]["rows"] == [[325]]
    assert body["result"]["trace_ids"] == ["t-1"]


@pytest.mark.asyncio
async def test_api_get_unknown_run_returns_404(client):
    resp = await client.get("/api/reports/runs/nonexistent-id")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_public_revoke_route_absent(client, file_db):
    """The public POST /runs/{run_id}/revoke endpoint was an unrecorded
    addition to the read-only B2C1 scope and remains outside B2C2 Phase 1.
    No route may exist in this phase."""
    fixture = await seed_definition(file_db)
    clock = _Clock(datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc))
    async with file_db() as db:
        run, _ = await reserve_report_run(
            db=db,
            report_id=fixture["report_id"],
            definition_version=1,
            idempotency_key="00000000-0000-4000-8000-000000000112",
            requested_mode=fixture["requested_mode"],
            question=fixture["question"],
            pinned_document_ids=fixture["pinned_document_ids"],
            pinned_source_tables=fixture["pinned_source_tables"],
            now=clock,
        )
        await db.commit()
    resp = await client.post(f"/api/reports/runs/{run.id}/revoke")
    assert resp.status_code == 405 or resp.status_code == 404
    async with file_db() as db:
        row = await db.get(ReportRun, run.id)
    assert row.status == "running", "no public revoke route may mutate run state"
