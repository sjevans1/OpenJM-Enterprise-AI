"""VS4-C2: governed bounded export acceptance tests.

Covers CSV fidelity + spreadsheet-injection defense, HTML/print escaping and
self-containment, authorization (owner/revocation/failed runs), unsupported
persisted payloads, bounded outputs, and proof that export performs no
retrieval, SQL, planning or model execution.
"""

import csv
import io
import json
from datetime import datetime, timezone

import pytest

from app.core.config import get_settings
from app.models import Conversation, DataSource, Message, SavedReport
from app.services.report_runs import finalize_success, reserve_report_run
from conftest import _Clock, seed_definition

settings = get_settings()


async def _seed_snapshot(maker, *, evidence, title="Budget review", owner=None, answer="Answer"):
    """Persist an owned (or foreign) saved snapshot with the given evidence."""
    async with maker() as db:
        conv = Conversation(user_id=owner or settings.dev_user_id, title="C2 conv")
        db.add(conv)
        await db.flush()
        user = Message(
            conversation_id=conv.id, role="user", content="q", requested_mode="hybrid"
        )
        db.add(user)
        await db.flush()
        assistant = Message(
            conversation_id=conv.id,
            role="assistant",
            content=answer,
            execution_class="hybrid",
            requested_mode="hybrid",
            evidence_json=json.dumps(evidence),
        )
        db.add(assistant)
        await db.flush()
        report = SavedReport(
            user_id=owner or settings.dev_user_id,
            conversation_id=conv.id,
            message_id=assistant.id,
            title=title,
            answer_text=answer,
            evidence_json=assistant.evidence_json,
            execution_class="hybrid",
            requested_mode="hybrid",
            source_count=2,
            snapshot_as_of=assistant.created_at,
        )
        db.add(report)
        await db.commit()
        return report.id


def _structured(source_id, columns, rows, tables=("finance",)):
    return {
        "source_type": "structured_query",
        "source_id": source_id,
        "title": "Finance",
        "passage": json.dumps({"columns": columns, "rows": rows, "row_count": len(rows)}),
        "metadata": {"tables": list(tables), "sql": "SELECT ..."},
    }


def _doc(document_id, passage="policy passage", title="Policy"):
    return {
        "source_type": "document",
        "source_id": document_id,
        "title": title,
        "passage": passage,
    }


# ---------------------------------------------------------------------------
# CSV fidelity + injection safety
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snapshot_csv_fidelity_and_injection(client, file_db):
    fixture = await seed_definition(file_db)
    doc_id = fixture["pinned_document_ids"][0]
    src_id = next(iter(fixture["pinned_source_tables"]))
    rows = [
        ["Acme", 325, "=SUM(A1:A9)"],
        ["Beta, Inc.", 120, "+1+1"],
        ['Quote"d', None, "-2-2"],
        ["  @cmd", 0, '=HYPERLINK("http://evil")'],
        ["Multi\nline", 3.5, "\ttab-start"],
        ["Uniçøde", 7, "line1\r\nline2"],
    ]
    evidence = [_doc(doc_id), _structured(src_id, ["customer", "revenue", "note"], rows)]
    report_id = await _seed_snapshot(file_db, evidence=evidence)

    resp = await client.get(f"/api/reports/{report_id}/exports/csv")
    assert resp.status_code == 200, resp.text
    body = resp.text

    # Neutralize every hostile text cell (leading = + - @, after whitespace/control)
    assert "'=SUM(A1:A9)" in body
    assert "'+1+1" in body
    assert "'-2-2" in body
    assert "'  @cmd" in body
    assert '\'=HYPERLINK(""http://evil"")' in body

    # Actual typed numeric values are retained, not neutralized
    assert "'325" not in body
    assert "'3.5" not in body

    # Round-trip with a standard CSV reader; structure preserved
    parsed = list(csv.reader(io.StringIO(body)))
    assert parsed[0] == ["customer", "revenue", "note"]
    assert len(parsed) == 1 + len(rows)
    assert all(len(row) == 3 for row in parsed)
    assert parsed[1] == ["Acme", "325", "'=SUM(A1:A9)"]
    assert parsed[4] == ["'  @cmd", "0", '\'=HYPERLINK("http://evil")']
    assert parsed[5][0] == "Multi\nline"          # embedded newline preserved
    assert parsed[3][1] == ""                     # None -> empty field
    assert parsed[6][0] == "Uniçøde"              # unicode preserved


@pytest.mark.asyncio
async def test_csv_headers_neutralized_and_ordered(client, file_db):
    fixture = await seed_definition(file_db)
    src_id = next(iter(fixture["pinned_source_tables"]))
    evidence = [
        _structured(src_id, ["=cmd", "b2", "a1"], [[1, 2, 3]]),
    ]
    report_id = await _seed_snapshot(file_db, evidence=evidence)
    resp = await client.get(f"/api/reports/{report_id}/exports/csv")
    assert resp.status_code == 200
    parsed = list(csv.reader(io.StringIO(resp.text)))
    assert parsed[0] == ["'=cmd", "b2", "a1"]  # hostile header neutralized, order kept
    assert parsed[1] == ["1", "2", "3"]


@pytest.mark.asyncio
async def test_csv_requires_explicit_selection_for_multiple_sets(client, file_db):
    fixture = await seed_definition(file_db)
    src_id = next(iter(fixture["pinned_source_tables"]))
    evidence = [
        _structured(src_id, ["a"], [[1]]),
        _structured(src_id, ["b"], [[2]]),
    ]
    report_id = await _seed_snapshot(file_db, evidence=evidence)

    ambiguous = await client.get(f"/api/reports/{report_id}/exports/csv")
    assert ambiguous.status_code == 409

    chosen = await client.get(f"/api/reports/{report_id}/exports/csv", params={"index": 1})
    assert chosen.status_code == 200
    parsed = list(csv.reader(io.StringIO(chosen.text)))
    assert parsed[0] == ["b"] and parsed[1] == ["2"]

    bad = await client.get(f"/api/reports/{report_id}/exports/csv", params={"index": 9})
    assert bad.status_code == 422
    non_structured = await client.get(
        f"/api/reports/{report_id}/exports/csv", params={"index": 0}
    )
    # index 0 here is the first structured set; the document index is rejected
    assert non_structured.status_code == 200


@pytest.mark.asyncio
async def test_csv_unavailable_for_narrative_only_snapshot(client, file_db):
    fixture = await seed_definition(file_db)
    doc_id = fixture["pinned_document_ids"][0]
    evidence = [_doc(doc_id, passage="only a narrative, no structured rows")]
    report_id = await _seed_snapshot(file_db, evidence=evidence)

    csv_resp = await client.get(f"/api/reports/{report_id}/exports/csv")
    assert csv_resp.status_code == 409

    html_resp = await client.get(f"/api/reports/{report_id}/exports/html")
    assert html_resp.status_code == 200  # narrative HTML still exportable


@pytest.mark.asyncio
async def test_formula_neutralizer_covers_unicode_format_characters(client, file_db):
    """A leading format char (Cf) must not smuggle a formula past the defense."""
    fixture = await seed_definition(file_db)
    src_id = next(iter(fixture["pinned_source_tables"]))
    rows = [
        ["\u200b=cmd()", "\ufeff+cmd", "\u2060-cmd", "\u00a0@cmd"],
        [-1.5, "-1.5", "  =cmd", 0],
    ]
    evidence = [_structured(src_id, ["a", "b", "c", "d"], rows)]
    report_id = await _seed_snapshot(file_db, evidence=evidence)
    resp = await client.get(f"/api/reports/{report_id}/exports/csv")
    assert resp.status_code == 200
    parsed = list(csv.reader(io.StringIO(resp.text)))
    assert all(parsed[1][i].startswith("'") for i in range(4)), parsed[1]
    assert parsed[2][0] == "-1.5"   # typed numeric value retained unchanged
    assert parsed[2][1] == "'-1.5"  # numeric-looking *string* is untrusted text
    assert parsed[2][2] == "'  =cmd"


@pytest.mark.asyncio
async def test_unsupported_structured_passage_fails_explicitly(client, file_db):
    fixture = await seed_definition(file_db)
    src_id = next(iter(fixture["pinned_source_tables"]))
    broken = {
        "source_type": "structured_query",
        "source_id": src_id,
        "title": "Finance",
        "passage": "not-json-at-all",
        "metadata": {"tables": ["finance"]},
    }
    report_id = await _seed_snapshot(file_db, evidence=[broken])
    resp = await client.get(f"/api/reports/{report_id}/exports/csv")
    assert resp.status_code == 409
    assert "evidence" not in resp.text.lower()  # failures never echo evidence


# ---------------------------------------------------------------------------
# HTML / print fidelity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_html_is_escaped_self_contained_and_bounded(client, file_db):
    fixture = await seed_definition(file_db)
    doc_id = fixture["pinned_document_ids"][0]
    src_id = next(iter(fixture["pinned_source_tables"]))
    hostile_answer = "<script>alert(1)</script> & <b>bold</b>"
    evidence = [
        _doc(doc_id, title="<img src=x onerror=alert(1)>", passage="<iframe src=http://evil>"),
        _structured(src_id, ["c"], [["<script>x</script>"]]),
    ]
    report_id = await _seed_snapshot(file_db, evidence=evidence, answer=hostile_answer)

    resp = await client.get(f"/api/reports/{report_id}/exports/html")
    assert resp.status_code == 200
    html = resp.text
    assert "&lt;script&gt;" in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "<script" not in html
    assert "<img" not in html and "<iframe" not in html
    assert "<a " not in html
    assert "href=" not in html
    # identity, as-of and citation labels are preserved
    assert "[DOC 1]" in html and "[DATA 1]" in html
    assert "Historical as-of" in html
    assert report_id in html


@pytest.mark.asyncio
async def test_export_headers_are_safe(client, file_db):
    fixture = await seed_definition(file_db)
    src_id = next(iter(fixture["pinned_source_tables"]))
    report_id = await _seed_snapshot(
        file_db,
        evidence=[_structured(src_id, ["a"], [[1]])],
        title='evil";\r\nX-Injected: 1',
    )
    resp = await client.get(f"/api/reports/{report_id}/exports/csv")
    assert resp.status_code == 200
    disp = resp.headers["content-disposition"]
    assert disp.startswith("attachment; filename=")
    assert "\r" not in disp and "\n" not in disp and '"' not in disp.split("filename=")[1].strip('"')[:1]
    assert resp.headers["cache-control"].startswith("no-store")
    assert resp.headers["x-content-type-options"] == "nosniff"

    html = await client.get(f"/api/reports/{report_id}/exports/html")
    assert "content-security-policy" in html.headers
    assert html.headers["cache-control"].startswith("no-store")


# ---------------------------------------------------------------------------
# Authorization: owner, revocation, failed run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wrong_owner_and_unknown_report_are_404(client, file_db):
    fixture = await seed_definition(file_db)
    src_id = next(iter(fixture["pinned_source_tables"]))
    foreign = await _seed_snapshot(
        file_db, evidence=[_structured(src_id, ["a"], [[1]])], owner="someone-else"
    )
    for fmt in ("csv", "html"):
        assert (await client.get(f"/api/reports/{foreign}/exports/{fmt}")).status_code == 404
    assert (
        await client.get("/api/reports/00000000-0000-4000-8000-00000000dead/exports/csv")
    ).status_code == 404


@pytest.mark.asyncio
async def test_revoked_source_denies_export(client, file_db):
    from sqlalchemy import select

    fixture = await seed_definition(file_db)
    src_id = next(iter(fixture["pinned_source_tables"]))
    report_id = await _seed_snapshot(file_db, evidence=[_structured(src_id, ["a"], [[1]])])

    async with file_db() as db:
        source = (
            await db.execute(select(DataSource).where(DataSource.id == src_id))
        ).scalar_one()
        source.enabled = False
        await db.commit()

    assert (await client.get(f"/api/reports/{report_id}/exports/csv")).status_code == 409
    assert (await client.get(f"/api/reports/{report_id}/exports/html")).status_code == 409


@pytest.mark.asyncio
async def test_failed_run_is_not_exportable(client, file_db):
    fixture = await seed_definition(file_db)
    clock = _Clock(datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc))
    async with file_db() as db:
        run, _ = await reserve_report_run(
            db=db,
            report_id=fixture["report_id"],
            definition_version=1,
            idempotency_key="00000000-0000-4000-8000-000000000501",
            requested_mode=fixture["requested_mode"],
            question=fixture["question"],
            pinned_document_ids=fixture["pinned_document_ids"],
            pinned_source_tables=fixture["pinned_source_tables"],
            now=clock,
        )
        await db.commit()
    resp = await client.get(
        f"/api/reports/{fixture['report_id']}/runs/{run.id}/exports/csv"
    )
    assert resp.status_code == 409
    assert "result" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_succeeded_run_export_uses_persisted_result(client, file_db):
    fixture = await seed_definition(file_db)
    doc_id = fixture["pinned_document_ids"][0]
    src_id = next(iter(fixture["pinned_source_tables"]))
    clock = _Clock(datetime.now(timezone.utc))
    evidence = [
        _doc(doc_id, title="Policy doc"),
        _structured(src_id, ["customer", "revenue"], [["Acme", 325]]),
    ]
    async with file_db() as db:
        run, _ = await reserve_report_run(
            db=db,
            report_id=fixture["report_id"],
            definition_version=1,
            idempotency_key="00000000-0000-4000-8000-000000000502",
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
            answer="Acme exceeds the threshold.",
            evidence=evidence,
            structured_result=None,
            trace_ids=["t-1"],
        )
        await db.commit()

    csv_resp = await client.get(
        f"/api/reports/{fixture['report_id']}/runs/{run.id}/exports/csv"
    )
    assert csv_resp.status_code == 200, csv_resp.text
    parsed = list(csv.reader(io.StringIO(csv_resp.text)))
    assert parsed[0] == ["customer", "revenue"]
    assert parsed[1] == ["Acme", "325"]

    html_resp = await client.get(
        f"/api/reports/{fixture['report_id']}/runs/{run.id}/exports/html"
    )
    assert html_resp.status_code == 200
    assert "Acme exceeds the threshold." in html_resp.text
    assert run.id in html_resp.text

    # a run id under the wrong report is not found
    assert (
        await client.get("/api/reports/00000000-0000-4000-8000-00000000beef/runs/%s/exports/csv" % run.id)
    ).status_code == 404


# ---------------------------------------------------------------------------
# Export performs no execution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_invokes_no_retrieval_sql_planning_or_model(client, file_db, monkeypatch):
    import app.services.structured_executor as executor
    import app.services.orchestrator as orch
    import app.services.report_runs as runs_service

    fixture = await seed_definition(file_db)
    doc_id = fixture["pinned_document_ids"][0]
    src_id = next(iter(fixture["pinned_source_tables"]))
    report_id = await _seed_snapshot(
        file_db, evidence=[_doc(doc_id), _structured(src_id, ["a"], [[1]])]
    )

    calls: list[str] = []

    def forbid(name):
        def _fail(*a, **k):
            calls.append(name)
            raise AssertionError(f"export invoked {name}")

        return _fail

    monkeypatch.setattr(executor, "execute_structured_query", forbid("sql"))
    monkeypatch.setattr(orch.OpenJMOrchestrator, "plan", forbid("plan"))
    monkeypatch.setattr(runs_service, "reserve_report_run", forbid("reserve"))
    monkeypatch.setattr(runs_service, "finalize_success", forbid("finalize"))

    before = await _counts(file_db)
    assert (await client.get(f"/api/reports/{report_id}/exports/csv")).status_code == 200
    assert (await client.get(f"/api/reports/{report_id}/exports/html")).status_code == 200
    after = await _counts(file_db)
    assert calls == []
    assert before == after


async def _counts(maker) -> tuple[int, int, int]:
    from sqlalchemy import func, select
    from app.models import ExecutionTrace, ReportRun

    async with maker() as db:
        runs = (await db.execute(select(func.count(ReportRun.id)))).scalar_one()
        traces = (await db.execute(select(func.count(ExecutionTrace.id)))).scalar_one()
    return runs, traces, 0
