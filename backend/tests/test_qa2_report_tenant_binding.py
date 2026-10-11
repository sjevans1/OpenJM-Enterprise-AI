"""QA2 regression: a Saved Report must be bound to the caller's tenant.

The create path wrote the tenant-qualified ``user_id`` but omitted ``tenant_id``,
so the column fell back to the model default (the legacy tenant). A report saved
by a principal acting in any other tenant was then invisible to every
tenant-scoped read over ``saved_reports``: the BV6 curation surface returned
404 "Saved report not found" for the report's own owner.

The offline suite never saw it because requests in tests resolve the development
principal, which lives in the legacy tenant, where the default coincides with the
correct value. This test drives the real endpoint while the request resolves into
a NON-legacy tenant (through the accepted ``X-OpenJM-Tenant`` narrowing path) and
asserts both the persisted tenant and that the tenant-scoped curation surface can
find the owner's own report.
"""

from __future__ import annotations

import json

from httpx import AsyncClient
from sqlalchemy import select

from app.core.tenancy import LEGACY_PRINCIPAL_ID, LEGACY_PRINCIPAL_SUBJECT
from app.models import (
    Conversation,
    Document,
    Message,
    PrincipalAccount,
    SavedReport,
    Tenant,
    TenantMembership,
)

TENANT = "tnt-qa2"
TENANT_HEADER = "X-OpenJM-Tenant"


async def _seed_tenant_and_message(file_db) -> str:
    """Provision the tenant, bind the principal to it, and seed a reportable message."""
    owner_user_id = f"{TENANT}:{LEGACY_PRINCIPAL_ID}"
    async with file_db() as db:
        db.add(Tenant(id=TENANT, slug="qa2-tenant", name="QA2 Tenant", status="active"))
        if await db.get(PrincipalAccount, LEGACY_PRINCIPAL_ID) is None:
            db.add(
                PrincipalAccount(
                    id=LEGACY_PRINCIPAL_ID,
                    subject=LEGACY_PRINCIPAL_SUBJECT,
                    issuer="openjm-local",
                    display_name="Local administrator",
                    status="active",
                )
            )
        db.add(
            TenantMembership(
                tenant_id=TENANT,
                principal_id=LEGACY_PRINCIPAL_ID,
                role="owner",
                status="active",
                version=1,
            )
        )
        await db.flush()

        conversation = Conversation(tenant_id=TENANT, user_id=owner_user_id, title="Conv")
        db.add(conversation)
        await db.flush()
        document = Document(
            tenant_id=TENANT,
            user_id=owner_user_id,
            original_name="qa2-policy.txt",
            stored_path="/tmp/qa2-tenant-policy",
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
                "title": "QA2 policy",
                "passage": "bounded governed passage",
            }
        ]
        message = Message(
            conversation_id=conversation.id,
            role="assistant",
            content="answer",
            execution_class="knowledge",
            requested_mode="knowledge",
            evidence_json=json.dumps(evidence),
        )
        db.add(message)
        await db.flush()
        await db.commit()
        return message.id


async def test_saved_report_is_bound_to_the_callers_tenant(client: AsyncClient, file_db):
    message_id = await _seed_tenant_and_message(file_db)
    headers = {TENANT_HEADER: TENANT}

    created = await client.post(
        "/api/reports",
        json={"message_id": message_id, "title": "QA2 tenant binding"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    report_id = created.json()["id"]

    async with file_db() as db:
        row = (
            await db.execute(select(SavedReport).where(SavedReport.id == report_id))
        ).scalar_one()
        # The row must carry the acting tenant, not the legacy default.
        assert row.tenant_id == TENANT, row.tenant_id
        assert row.user_id == f"{TENANT}:{LEGACY_PRINCIPAL_ID}"

    # The tenant-scoped curation surface must find the owner's own report.
    curation = await client.get(f"/api/reports/{report_id}/curation", headers=headers)
    assert curation.status_code == 200, curation.text
    assert curation.json()["curation_state"] == "none"

    review = await client.post(
        f"/api/reports/{report_id}/curation/request-review", json={}, headers=headers
    )
    assert review.status_code == 200, review.text
    assert review.json()["curation_state"] == "under_review"
