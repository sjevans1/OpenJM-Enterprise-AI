"""BV6-A curation HTTP surface: wiring, self-approval and cross-tenant 404.

Runs the real FastAPI app against the file-backed SQLite database, using the
resolved local development principal (an owner who owns the seeded report).
"""

from __future__ import annotations

import json

from httpx import AsyncClient

from app.core.config import get_settings
from app.models import Conversation, Document, Message, SavedReport

settings = get_settings()


async def _seed_owned_report(maker, *, tenant_id=None) -> str:
    async with maker() as db:
        conv = Conversation(
            tenant_id=tenant_id or "tnt-local", user_id=settings.dev_user_id, title="Conv"
        )
        db.add(conv)
        await db.flush()
        document = Document(
            tenant_id=tenant_id or "tnt-local",
            user_id=settings.dev_user_id,
            original_name="policy.txt",
            stored_path="/tmp/bv6-api-policy",
            size_bytes=10,
            status="ready",
            indexed=True,
            classification="internal",
            tenant_visible=True,
        )
        db.add(document)
        await db.flush()
        evidence = [
            {
                "source_type": "document",
                "source_id": document.id,
                "title": "Policy",
                "passage": "secret foreign passage",
            }
        ]
        assistant = Message(
            conversation_id=conv.id,
            role="assistant",
            content="answer",
            execution_class="knowledge",
            requested_mode="knowledge",
            evidence_json=json.dumps(evidence),
        )
        db.add(assistant)
        await db.flush()
        report = SavedReport(
            tenant_id=tenant_id or "tnt-local",
            user_id=settings.dev_user_id,
            conversation_id=conv.id,
            message_id=assistant.id,
            title="Secret foreign title",
            answer_text="answer",
            evidence_json=json.dumps(evidence),
            execution_class="knowledge",
            requested_mode="knowledge",
            source_count=1,
            snapshot_as_of=assistant.created_at,
        )
        db.add(report)
        await db.commit()
        return report.id


async def test_get_curation_defaults_to_uncurated(client: AsyncClient, file_db):
    report_id = await _seed_owned_report(file_db)
    resp = await client.get(f"/api/reports/{report_id}/curation")
    assert resp.status_code == 200
    body = resp.json()
    assert body["curation_state"] == "none"
    assert body["featured"] is False


async def test_owner_requests_review_but_cannot_self_approve(client: AsyncClient, file_db):
    report_id = await _seed_owned_report(file_db)
    started = await client.post(f"/api/reports/{report_id}/curation/request-review", json={})
    assert started.status_code == 200
    assert started.json()["curation_state"] == "under_review"

    denied = await client.post(f"/api/reports/{report_id}/curation/approve", json={})
    assert denied.status_code == 403
    assert "owner" in denied.json()["detail"].lower()

    # The refused approval left the state untouched.
    view = await client.get(f"/api/reports/{report_id}/curation")
    assert view.json()["curation_state"] == "under_review"


async def test_authoritative_from_wrong_state_conflicts(client: AsyncClient, file_db):
    report_id = await _seed_owned_report(file_db)
    resp = await client.post(f"/api/reports/{report_id}/curation/authoritative", json={})
    assert resp.status_code == 409


async def test_cross_tenant_curation_returns_404_without_leak(client: AsyncClient, file_db):
    foreign_id = await _seed_owned_report(file_db, tenant_id="tnt-foreign")
    resp = await client.get(f"/api/reports/{foreign_id}/curation")
    assert resp.status_code == 404
    assert "Secret foreign" not in resp.text

    more = await client.post(f"/api/reports/{foreign_id}/curation/request-review", json={})
    assert more.status_code == 404
    assert "Secret foreign" not in more.text


async def test_unknown_report_is_404(client: AsyncClient):
    resp = await client.get("/api/reports/does-not-exist/curation")
    assert resp.status_code == 404
