"""VS4-B2C2 Phase 1 HTTP contract for explicit run reservation."""

import pytest
from sqlalchemy import func, select

from app.api import report_runs as report_runs_api
from app.models import ReportDefinitionVersion, ReportRun, SavedReport

from conftest import seed_definition


SUBMIT_KEY = "00000000-0000-4000-8000-000000000200"


@pytest.mark.asyncio
async def test_submit_reserves_running_detail_with_stable_202(file_db, client):
    """Phase 1 uses 202 for both new reservations and later replays."""
    fixture = await seed_definition(file_db)

    response = await client.post(
        f"/api/reports/{fixture['report_id']}/definitions/1/runs",
        json={"idempotency_key": SUBMIT_KEY},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["report_id"] == fixture["report_id"]
    assert body["definition_version"] == 1
    assert body["requested_mode"] == fixture["requested_mode"]
    assert body["status"] == "running"
    assert body["finished_at"] is None
    assert body["result"] is None
    async with file_db() as db:
        assert await db.scalar(select(func.count()).select_from(ReportRun)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"idempotency_key": SUBMIT_KEY, "question": "client-controlled"},
        {"idempotency_key": "AAAAAAAA-0000-4000-8000-000000000200"},
        {"idempotency_key": " 00000000-0000-4000-8000-000000000200"},
        {"idempotency_key": "00000000000040008000000000000200"},
        {"idempotency_key": 200},
    ],
)
async def test_submit_strict_body_rejects_missing_extra_and_noncanonical_keys(
    file_db, client, payload
):
    fixture = await seed_definition(file_db)

    response = await client.post(
        f"/api/reports/{fixture['report_id']}/definitions/1/runs",
        json=payload,
    )

    assert response.status_code == 422
    async with file_db() as db:
        assert await db.scalar(select(func.count()).select_from(ReportRun)) == 0


@pytest.mark.asyncio
async def test_submit_loads_exact_definition_before_reserving_trusted_intent(
    file_db, client, monkeypatch
):
    fixture = await seed_definition(file_db)
    events = []
    original_load = report_runs_api.load_validated_definition_scope
    original_reserve = report_runs_api.reserve_report_run

    async def tracked_load(*args, **kwargs):
        events.append(("load", kwargs.get("definition_id")))
        return await original_load(*args, **kwargs)

    async def tracked_reserve(**kwargs):
        events.append(
            (
                "reserve",
                kwargs["question"],
                kwargs["requested_mode"],
                kwargs["pinned_document_ids"],
                kwargs["pinned_source_tables"],
            )
        )
        return await original_reserve(**kwargs)

    monkeypatch.setattr(report_runs_api, "load_validated_definition_scope", tracked_load)
    monkeypatch.setattr(report_runs_api, "reserve_report_run", tracked_reserve)

    response = await client.post(
        f"/api/reports/{fixture['report_id']}/definitions/1/runs",
        json={"idempotency_key": "00000000-0000-4000-8000-000000000201"},
    )

    assert response.status_code == 202
    assert events[:2] == [
        ("load", fixture["definition_id"]),
        (
            "reserve",
            fixture["question"],
            fixture["requested_mode"],
            fixture["pinned_document_ids"],
            fixture["pinned_source_tables"],
        ),
    ]


@pytest.mark.asyncio
async def test_submit_replay_reauthorizes_detail_without_new_reservation(
    file_db, client, monkeypatch
):
    fixture = await seed_definition(file_db)
    detail_calls = []
    ownership = []
    original_detail = report_runs_api._detail
    original_reserve = report_runs_api.reserve_report_run

    async def tracked_detail(db, run):
        detail_calls.append(run.id)
        return await original_detail(db, run)

    async def tracked_reserve(**kwargs):
        run, owns_execution = await original_reserve(**kwargs)
        ownership.append(owns_execution)
        return run, owns_execution

    monkeypatch.setattr(report_runs_api, "_detail", tracked_detail)
    monkeypatch.setattr(report_runs_api, "reserve_report_run", tracked_reserve)
    path = f"/api/reports/{fixture['report_id']}/definitions/1/runs"
    payload = {"idempotency_key": "00000000-0000-4000-8000-000000000202"}

    first = await client.post(path, json=payload)
    replay = await client.post(path, json=payload)

    assert first.status_code == replay.status_code == 202
    assert replay.json() == first.json()
    assert ownership == [True, False]
    assert detail_calls == [first.json()["id"], first.json()["id"]]
    async with file_db() as db:
        assert await db.scalar(select(func.count()).select_from(ReportRun)) == 1


@pytest.mark.asyncio
async def test_submit_same_key_different_intent_returns_bounded_409(file_db, client):
    first_fixture = await seed_definition(file_db)
    second_fixture = await seed_definition(file_db)
    payload = {"idempotency_key": "00000000-0000-4000-8000-000000000203"}

    first = await client.post(
        f"/api/reports/{first_fixture['report_id']}/definitions/1/runs",
        json=payload,
    )
    conflict = await client.post(
        f"/api/reports/{second_fixture['report_id']}/definitions/1/runs",
        json=payload,
    )

    assert first.status_code == 202
    assert conflict.status_code == 409
    assert conflict.json() == {"detail": "Report run reservation conflict"}
    async with file_db() as db:
        assert await db.scalar(select(func.count()).select_from(ReportRun)) == 1


@pytest.mark.asyncio
async def test_submit_unknown_report_or_version_returns_404(file_db, client):
    fixture = await seed_definition(file_db)
    payload = {"idempotency_key": "00000000-0000-4000-8000-000000000204"}

    unknown_report = await client.post(
        "/api/reports/not-a-report/definitions/1/runs", json=payload
    )
    unknown_version = await client.post(
        f"/api/reports/{fixture['report_id']}/definitions/99/runs", json=payload
    )

    assert unknown_report.status_code == 404
    assert unknown_version.status_code == 404


@pytest.mark.asyncio
async def test_submit_foreign_report_returns_404(file_db, client, monkeypatch):
    fixture = await seed_definition(file_db)
    async with file_db() as db:
        report = await db.get(SavedReport, fixture["report_id"])
        report.user_id = "foreign-owner"
        await db.commit()

    async def must_not_load_foreign_definition(*_args, **_kwargs):
        raise AssertionError("foreign parent must be rejected while resolving definition ID")

    monkeypatch.setattr(
        report_runs_api,
        "load_validated_definition_scope",
        must_not_load_foreign_definition,
    )
    response = await client.post(
        f"/api/reports/{fixture['report_id']}/definitions/1/runs",
        json={"idempotency_key": "00000000-0000-4000-8000-000000000205"},
    )

    assert response.status_code == 404
    async with file_db() as db:
        assert await db.scalar(select(func.count()).select_from(ReportRun)) == 0


@pytest.mark.asyncio
async def test_submit_foreign_definition_version_returns_404(file_db, client):
    fixture = await seed_definition(file_db)
    async with file_db() as db:
        definition = await db.get(ReportDefinitionVersion, fixture["definition_id"])
        definition.user_id = "foreign-owner"
        await db.commit()

    response = await client.post(
        f"/api/reports/{fixture['report_id']}/definitions/1/runs",
        json={"idempotency_key": "00000000-0000-4000-8000-000000000207"},
    )

    assert response.status_code == 404
    async with file_db() as db:
        assert await db.scalar(select(func.count()).select_from(ReportRun)) == 0


@pytest.mark.asyncio
async def test_public_revoke_route_remains_absent_in_b2c2_phase_1(file_db, client):
    fixture = await seed_definition(file_db)
    submitted = await client.post(
        f"/api/reports/{fixture['report_id']}/definitions/1/runs",
        json={"idempotency_key": "00000000-0000-4000-8000-000000000206"},
    )
    run_id = submitted.json()["id"]

    response = await client.post(f"/api/reports/runs/{run_id}/revoke")

    assert response.status_code in {404, 405}
    async with file_db() as db:
        run = await db.get(ReportRun, run_id)
        assert run.status == "running"
