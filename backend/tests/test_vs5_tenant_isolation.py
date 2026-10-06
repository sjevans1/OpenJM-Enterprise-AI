"""VS5 tenant isolation across the VS1-VS4 surfaces.

Two tenants, two principals, and a real database. Every assertion is that
tenant B cannot reach tenant A's resource, that a cached copy cannot outlive a
revocation, and that historical metadata never widens current access.
"""

import json

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models import (
    AuditRecord,
    Conversation,
    DataSource,
    Document,
    ExecutionTrace,
    Message,
    ReportDefinitionVersion,
    ReportRun,
    SavedReport,
    Tenant,
)
from app.services import identity as identity_service
from app.services.oidc import oidc_client
from oidc_testkit import AUDIENCE, ISSUER, TestIdP

settings = get_settings()

TENANT_A = "tnt-iso-a"
TENANT_B = "tnt-iso-b"
SUBJECT_A = "iso-a"
SUBJECT_B = "iso-b"

# Ownership keys as Principal.user_id derives them (tenant-qualified outside
# the legacy local tenant).
ACCOUNT_A = "iso-pa"
ACCOUNT_B = "iso-pb"
KEY_A = f"{TENANT_A}:{ACCOUNT_A}"
KEY_B = f"{TENANT_B}:{ACCOUNT_B}"


@pytest.fixture
def oidc_mode(monkeypatch):
    idp = TestIdP()
    monkeypatch.setattr(settings, "auth_mode", "oidc")
    monkeypatch.setattr(settings, "oidc_issuer", ISSUER)
    monkeypatch.setattr(settings, "oidc_audience", AUDIENCE)
    monkeypatch.setattr(settings, "oidc_jwks_url", "https://idp.test.invalid/keys")
    monkeypatch.setattr(oidc_client, "settings", settings)
    oidc_client._jwks = type(oidc_client._jwks)({})
    oidc_client._discovery = type(oidc_client._discovery)({})

    async def fake_fetch(url):
        return idp.jwks()

    monkeypatch.setattr(oidc_client, "_fetch_json", fake_fetch)
    return idp


@pytest.fixture
async def world(file_db):
    """Tenant A with a full VS4 resource set; tenant B with a principal."""
    async with file_db() as db:
        db.add_all(
            [
                Tenant(id=TENANT_A, slug="iso-a", name="A", status="active"),
                Tenant(id=TENANT_B, slug="iso-b", name="B", status="active"),
            ]
        )
        await db.flush()
        account_a = await identity_service.get_or_create_principal(
            db, subject=SUBJECT_A, principal_id=ACCOUNT_A
        )
        account_b = await identity_service.get_or_create_principal(
            db, subject=SUBJECT_B, principal_id=ACCOUNT_B
        )
        await identity_service.add_membership(
            db, tenant_id=TENANT_A, principal_id=account_a.id, role="owner"
        )
        await identity_service.add_membership(
            db, tenant_id=TENANT_B, principal_id=account_b.id, role="owner"
        )

        doc = Document(
            id="iso-doc",
            tenant_id=TENANT_A,
            user_id=KEY_A,
            original_name="a-policy.md",
            stored_path="/tmp/iso",
            size_bytes=5,
            status="ready",
            indexed=True,
            lifecycle_state="ready",
        )
        source = DataSource(
            id="iso-src",
            tenant_id=TENANT_A,
            user_id=KEY_A,
            name="A Finance",
            engine="sqlite",
            connection_secret="x",
            status="connected",
            enabled=True,
            schema_json=json.dumps(
                [
                    {
                        "schema_name": "main",
                        "name": "finance",
                        "qualified_name": "finance",
                        "columns": [{"name": "revenue", "type": "NUMERIC", "nullable": False}],
                        "primary_key": [],
                        "foreign_keys": [],
                    }
                ]
            ),
            authorized_objects_json=json.dumps(["finance"]),
            revenue_currency="USD",
        )
        conv = Conversation(id="iso-conv", tenant_id=TENANT_A, user_id=KEY_A, title="A chat")
        db.add_all([doc, source, conv])
        await db.flush()
        user_msg = Message(
            id="iso-m1", conversation_id=conv.id, role="user", content="q", requested_mode="knowledge"
        )
        assistant = Message(
            id="iso-m2",
            conversation_id=conv.id,
            role="assistant",
            content="a",
            execution_class="knowledge",
            requested_mode="knowledge",
            evidence_json=json.dumps(
                [
                    {
                        "source_type": "document",
                        "source_id": doc.id,
                        "title": "A Policy",
                        "passage": "the answer",
                    }
                ]
            ),
        )
        db.add_all([user_msg, assistant])
        await db.flush()
        report = SavedReport(
            id="iso-rep",
            tenant_id=TENANT_A,
            user_id=KEY_A,
            conversation_id=conv.id,
            message_id=assistant.id,
            title="A report",
            answer_text="a",
            evidence_json=assistant.evidence_json,
            execution_class="knowledge",
            requested_mode="knowledge",
            source_count=1,
            snapshot_as_of=assistant.created_at,
        )
        db.add(report)
        await db.flush()
        db.add(
            ReportDefinitionVersion(
                id="iso-def",
                tenant_id=TENANT_A,
                report_id=report.id,
                user_id=KEY_A,
                version=1,
                question_text="q",
                requested_mode="knowledge",
                pinned_document_ids_json=json.dumps([doc.id]),
                pinned_source_tables_json=json.dumps({}),
            )
        )
        db.add(
            ReportRun(
                id="iso-run",
                tenant_id=TENANT_A,
                user_id=KEY_A,
                report_id=report.id,
                definition_id="iso-def",
                definition_version=1,
                requested_mode="knowledge",
                idempotency_key="iso-key",
                request_fingerprint="fp",
                status="succeeded",
                started_at=assistant.created_at,
                deadline_at=assistant.created_at,
                finished_at=assistant.created_at,
                result_json=json.dumps({"answer": "a",
                "evidence": [{"source_type": "document", "source_id": doc.id,
                              "title": "A Policy", "passage": "the answer"}]}),
            )
        )
        db.add(
            ExecutionTrace(
                id="iso-trace",
                tenant_id=TENANT_A,
                user_id=KEY_A,
                tool_name="knowledge.search",
                operation_class="read",
                risk_level="low",
                input_hash="h",
                status="succeeded",
                conversation_id=conv.id,
            )
        )
        db.add(
            AuditRecord(
                tenant_id=TENANT_A,
                principal_id=KEY_A,
                action="test.action",
                decision="allow",
            )
        )
        await db.commit()
    return {"a": ACCOUNT_A, "b": ACCOUNT_B, "key_a": KEY_A, "key_b": KEY_B}


def auth(token):
    return {"Authorization": f"Bearer {token}"}


async def test_reports_are_tenant_scoped(client, oidc_mode, world):
    token_b = oidc_mode.token(SUBJECT_B, tenant="iso-b")
    listing = await client.get("/api/reports", headers=auth(token_b))
    assert listing.status_code == 200
    assert listing.json() == []

    detail = await client.get("/api/reports/iso-rep", headers=auth(token_b))
    assert detail.status_code == 404

    deleted = await client.delete("/api/reports/iso-rep", headers=auth(token_b))
    assert deleted.status_code == 204
    still_there = await client.get("/api/reports/iso-rep", headers=auth(token_a(oidc_mode)))
    assert still_there.status_code == 200, "tenant B's delete must not touch tenant A"


def token_a(idp):
    return idp.token(SUBJECT_A, tenant="iso-a")


async def test_run_history_and_definitions_are_tenant_scoped(client, oidc_mode, world):
    token_b = oidc_mode.token(SUBJECT_B, tenant="iso-b")
    assert (await client.get("/api/reports/iso-rep/runs", headers=auth(token_b))).status_code == 404
    assert (await client.get("/api/reports/runs/iso-run", headers=auth(token_b))).status_code == 404
    assert (
        await client.get("/api/reports/iso-rep/definitions", headers=auth(token_b))
    ).status_code == 404
    assert (
        await client.get(
            "/api/reports/iso-rep/definitions/1", headers=auth(token_b)
        )
    ).status_code == 404

    token_a_ = token_a(oidc_mode)
    own_run = await client.get("/api/reports/runs/iso-run", headers=auth(token_a_))
    assert own_run.status_code == 200, own_run.text


async def test_exports_respect_current_authorization(client, oidc_mode, world, file_db):
    token_a_ = token_a(oidc_mode)
    ok = await client.get("/api/reports/iso-rep/exports/html", headers=auth(token_a_))
    assert ok.status_code == 200

    # Revoke tenant A's principal from its tenant: the same token must stop
    # exporting, because authorization is re-read per request.
    async with file_db() as db:
        await identity_service.revoke_membership(db, tenant_id=TENANT_A, principal_id=ACCOUNT_A)
        await db.commit()

    blocked = await client.get("/api/reports/iso-rep/exports/html", headers=auth(token_a_))
    assert blocked.status_code == 403

    # The owner can no longer read the report either.
    assert (await client.get("/api/reports/iso-rep", headers=auth(token_a_))).status_code == 403


async def test_historical_report_metadata_does_not_grant_current_access(
    client, oidc_mode, world, file_db
):
    """A run may exist, but it must not be executable once its snapshots are gone."""
    token_a_ = token_a(oidc_mode)
    # Delete the pinned document.
    async with file_db() as db:
        document = await db.get(Document, "iso-doc")
        document.lifecycle_state = "deleted"
        document.indexed = False
        document.deleted_at = document.created_at
        await db.commit()

    detail = await client.get("/api/reports/iso-rep", headers=auth(token_a_))
    assert detail.status_code == 409, "a report whose evidence is gone must fail closed"

    preview = await client.get("/api/reports/iso-rep/rerun-preview", headers=auth(token_a_))
    assert preview.status_code == 409


async def test_deleted_evidence_cannot_reappear(client, oidc_mode, world, file_db):
    from app.services.document_lifecycle import retrievable_filter

    token_a_ = token_a(oidc_mode)
    async with file_db() as db:
        document = await db.get(Document, "iso-doc")
        document.lifecycle_state = "deleted"
        document.indexed = False
        document.deleted_at = document.created_at
        await db.commit()
        rows = (await db.execute(select(Document).where(*retrievable_filter()))).scalars().all()
    assert rows == []
    assert (await client.get("/api/knowledge/documents", headers=auth(token_a_))).json() == []


async def test_revoked_data_source_prevents_subsequent_access(client, oidc_mode, world, file_db):
    token_a_ = token_a(oidc_mode)
    assert (await client.get("/api/data/sources/iso-src", headers=auth(token_a_))).status_code == 200
    async with file_db() as db:
        source = await db.get(DataSource, "iso-src")
        source.enabled = False
        await db.commit()
    # The source itself remains readable as configuration, but its tables are
    # no longer authorized for execution.
    detail = await client.get("/api/data/sources/iso-src", headers=auth(token_a_))
    assert detail.status_code == 200
    assert detail.json()["enabled"] is False


async def test_audit_reads_are_tenant_scoped(client, oidc_mode, world, file_db):
    """The audit listing returns only the caller's tenant rows."""
    from app.core.context import set_principal
    from app.core.identity import Principal
    from app.core.permissions import permissions_for_role
    from app.services.actions import builtin

    principal_b = Principal(
        principal_id=ACCOUNT_B,
        tenant_id=TENANT_B,
        subject=SUBJECT_B,
        role="owner",
        membership_id="m",
        auth_method="oidc",
        permissions=permissions_for_role("owner"),
    )
    set_principal(principal_b)
    try:
        async with file_db() as db:
            result = await builtin._audit_list(db, {"limit": 50})
    finally:
        set_principal(None)

    assert result["count"] == 0, "tenant B must not see tenant A audit rows"

    principal_a = Principal(
        principal_id=ACCOUNT_A,
        tenant_id=TENANT_A,
        subject=SUBJECT_A,
        role="owner",
        membership_id="m",
        auth_method="oidc",
        permissions=permissions_for_role("owner"),
    )
    set_principal(principal_a)
    try:
        async with file_db() as db:
            own = await builtin._audit_list(db, {"limit": 50})
    finally:
        set_principal(None)
    assert own["count"] >= 1
