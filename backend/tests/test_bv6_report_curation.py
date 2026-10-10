"""BV6-A governed report curation acceptance (RED -> GREEN).

Every test runs against a real file-backed SQLite database and the real service
layer. Nothing reaches a model, a vector store or governed SQL. The suite pins
the required negative contract: save is uncurated, the owner cannot self-approve,
one actor cannot satisfy the two-person ``authoritative`` rule, ordinary
viewer/editor and platform-operator-only principals cannot curate, cross-tenant
curation is denied without a leak, revoked sources block promotion, withdrawal
returns to ``none``, audit history is preserved and archived governing scope
invalidates steward authority.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.core.governance import StewardScopeType
from app.core.identity import AuthorizationError, Principal
from app.core.permissions import permissions_for_role
from app.core.platform import PlatformCapability
from app.models import (
    AccessGroup,
    AuditRecord,
    Conversation,
    DataSource,
    Department,
    Document,
    Message,
    SavedReport,
    Tenant,
)
from app.services import access_governance as governance
from app.services import identity as identity_service
from app.services import report_curation as curation

TENANT_A = "tnt-bv6-a"
TENANT_B = "tnt-bv6-b"
OWNER_A = "bv6-owner-a"
ADMIN_A = "bv6-admin-a"
STEWARD_A = "bv6-steward-a"
STAFF_A = "bv6-staff-a"
EDITOR_A = "bv6-editor-a"
OPERATOR_A = "bv6-operator-a"
OWNER_B = "bv6-owner-b"

ALL_MEMBERS = (OWNER_A, ADMIN_A, STEWARD_A, STAFF_A, EDITOR_A, OPERATOR_A)


def _principal(principal_id: str, tenant_id: str, role: str, **kwargs) -> Principal:
    return Principal(
        principal_id=principal_id,
        tenant_id=tenant_id,
        subject=f"sub:{principal_id}",
        role=role,
        membership_id=f"m:{tenant_id}:{principal_id}",
        auth_method="local-dev",
        permissions=permissions_for_role(role),
        **kwargs,
    )


async def _resolved(db, principal_id: str, tenant_id: str, role: str, **kwargs) -> Principal:
    """A principal with scopes resolved from the database, as a request would."""
    access = await governance.load_principal_access(
        db, principal_id=principal_id, tenant_id=tenant_id
    )
    return _principal(
        principal_id,
        tenant_id,
        role,
        department_ids=access.department_ids,
        group_ids=access.group_ids,
        steward_scopes=access.steward_scopes,
        support_scopes=access.support_scopes,
        **kwargs,
    )


async def _seed_report(db, *, owner: Principal, documents, source=None, title="Report"):
    conv = Conversation(tenant_id=owner.tenant_id, user_id=owner.user_id, title="Conv")
    db.add(conv)
    await db.flush()
    evidence = [
        {
            "source_type": "document",
            "source_id": document.id,
            "title": document.original_name,
            "passage": "pinned passage",
        }
        for document in documents
    ]
    if source is not None:
        evidence.append(
            {
                "source_type": "structured_query",
                "source_id": source.id,
                "title": source.name,
                "passage": "{}",
                "metadata": {"tables": ["finance"], "sql": "SELECT 1 FROM finance"},
            }
        )
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
        tenant_id=owner.tenant_id,
        user_id=owner.user_id,
        conversation_id=conv.id,
        message_id=assistant.id,
        title=title,
        answer_text="answer",
        evidence_json=json.dumps(evidence),
        execution_class="knowledge",
        requested_mode="knowledge",
        source_count=len(evidence),
        snapshot_as_of=assistant.created_at,
    )
    db.add(report)
    await db.flush()
    return report


async def _document(db, *, owner: Principal, department_id=None, allowed_groups=None, name="policy.txt"):
    document = Document(
        tenant_id=owner.tenant_id,
        user_id=owner.user_id,
        original_name=name,
        stored_path=f"/tmp/{name}",
        size_bytes=10,
        status="ready",
        indexed=True,
        classification="internal",
        tenant_visible=True,
        department_id=department_id,
        allowed_group_ids_json=json.dumps(allowed_groups) if allowed_groups else None,
    )
    db.add(document)
    await db.flush()
    return document


@pytest.fixture
async def world(file_db):
    """Two tenants with a department and the full authority matrix in tenant A."""
    async with file_db() as db:
        db.add_all(
            [
                Tenant(id=TENANT_A, slug="bv6-a", name="A", status="active"),
                Tenant(id=TENANT_B, slug="bv6-b", name="B", status="active"),
            ]
        )
        await db.flush()
        for pid in (*ALL_MEMBERS, OWNER_B):
            await identity_service.get_or_create_principal(
                db, subject=f"sub:{pid}", principal_id=pid
            )
        for pid, role in (
            (OWNER_A, "admin"),
            (ADMIN_A, "owner"),
            (STEWARD_A, "viewer"),
            (STAFF_A, "viewer"),
            (EDITOR_A, "editor"),
            (OPERATOR_A, "viewer"),
            (OWNER_B, "owner"),
        ):
            tenant = TENANT_B if pid == OWNER_B else TENANT_A
            await identity_service.add_membership(db, tenant_id=tenant, principal_id=pid, role=role)

        hr = Department(tenant_id=TENANT_A, slug="hr", name="HR", status="active")
        ops = Department(tenant_id=TENANT_A, slug="ops", name="Ops", status="active")
        db.add_all([hr, ops])
        await db.flush()
        group = AccessGroup(
            tenant_id=TENANT_A, department_id=hr.id, slug="hr-team", name="HR", status="active"
        )
        db.add(group)
        await db.flush()

        admin_actor = _principal(ADMIN_A, TENANT_A, "owner")
        await governance.grant_steward(
            db,
            principal=admin_actor,
            steward_principal_id=STEWARD_A,
            scope_type=StewardScopeType.DEPARTMENT.value,
            scope_id=hr.id,
        )

        owner_a = await _resolved(db, OWNER_A, TENANT_A, "admin")
        owner_b = await _resolved(db, OWNER_B, TENANT_B, "owner")
        admin_a = await _resolved(db, ADMIN_A, TENANT_A, "owner")
        steward_a = await _resolved(db, STEWARD_A, TENANT_A, "viewer")
        staff_a = await _resolved(db, STAFF_A, TENANT_A, "viewer")
        editor_a = await _resolved(db, EDITOR_A, TENANT_A, "editor")
        operator_a = await _resolved(
            db,
            OPERATOR_A,
            TENANT_A,
            "viewer",
            platform_capabilities=frozenset({PlatformCapability.CONTENT_SUPPORT.value}),
        )

        doc_hr = await _document(db, owner=owner_a, department_id=hr.id, name="hr-policy.txt")
        doc_ops = await _document(db, owner=owner_a, department_id=ops.id, name="ops-policy.txt")
        report = await _seed_report(db, owner=owner_a, documents=[doc_hr], title="HR report")
        mixed_report = await _seed_report(
            db, owner=owner_a, documents=[doc_hr, doc_ops], title="Mixed report"
        )
        await db.commit()

        return {
            "hr": hr.id,
            "ops": ops.id,
            "group": group.id,
            "owner_a": owner_a,
            "owner_b": owner_b,
            "admin_a": admin_a,
            "steward_a": steward_a,
            "staff_a": staff_a,
            "editor_a": editor_a,
            "operator_a": operator_a,
            "doc_hr": doc_hr.id,
            "doc_ops": doc_ops.id,
            "report": report.id,
            "mixed_report": mixed_report.id,
        }


async def _load(db, report_id):
    return (await db.execute(select(SavedReport).where(SavedReport.id == report_id))).scalar_one()


# ---------------------------------------------------------------------------
# Save is uncurated
# ---------------------------------------------------------------------------


def test_saved_report_defaults_to_uncurated_without_featured(world):
    # The model default is the contract: saving produces none/false.
    assert SavedReport.__table__.c.curation_state.default.arg == "none"
    assert SavedReport.__table__.c.featured.default.arg is False


async def test_save_produces_uncurated_state(world, file_db):
    async with file_db() as db:
        report = await _load(db, world["report"])
        assert report.curation_state == "none"
        assert bool(report.featured) is False


# ---------------------------------------------------------------------------
# Owner cannot self-approve; one actor cannot satisfy two-person
# ---------------------------------------------------------------------------


async def test_owner_requests_review_then_cannot_self_approve(world, file_db):
    async with file_db() as db:
        report = await _load(db, world["report"])
        await curation.request_review(db, principal=world["owner_a"], report=report)
        assert report.curation_state == "under_review"
        with pytest.raises(AuthorizationError) as exc:
            await curation.approve(db, principal=world["owner_a"], report=report)
        assert exc.value.code == "self_approval_forbidden"
        # State is unchanged by the refused self-approval.
        assert report.curation_state == "under_review"


async def test_single_actor_cannot_satisfy_two_person_rule(world, file_db):
    async with file_db() as db:
        report = await _load(db, world["report"])
        await curation.request_review(db, principal=world["owner_a"], report=report)
        await curation.approve(db, principal=world["steward_a"], report=report)
        assert report.curation_state == "approved"
        with pytest.raises(AuthorizationError) as exc:
            await curation.mark_authoritative(db, principal=world["steward_a"], report=report)
        assert exc.value.code == "two_person_rule"
        assert report.curation_state == "approved"


async def test_two_distinct_principals_reach_authoritative(world, file_db):
    async with file_db() as db:
        report = await _load(db, world["report"])
        await curation.request_review(db, principal=world["owner_a"], report=report)
        await curation.approve(db, principal=world["steward_a"], report=report)
        await curation.mark_authoritative(db, principal=world["admin_a"], report=report)
        assert report.curation_state == "authoritative"
        assert report.curation_first_approver_id == STEWARD_A
        assert report.curation_second_approver_id == ADMIN_A


# ---------------------------------------------------------------------------
# Ordinary roles and platform operators cannot curate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("actor_key", ["staff_a", "editor_a", "operator_a"])
async def test_non_authority_cannot_approve(world, file_db, actor_key):
    async with file_db() as db:
        report = await _load(db, world["report"])
        await curation.request_review(db, principal=world["owner_a"], report=report)
        with pytest.raises(AuthorizationError):
            await curation.approve(db, principal=world[actor_key], report=report)


async def test_platform_operator_alone_cannot_request_or_feature(world, file_db):
    async with file_db() as db:
        report = await _load(db, world["report"])
        with pytest.raises(AuthorizationError):
            await curation.request_review(db, principal=world["operator_a"], report=report)
        with pytest.raises(AuthorizationError):
            await curation.set_featured(
                db, principal=world["operator_a"], report=report, featured=True
            )


# ---------------------------------------------------------------------------
# Cross-tenant denial without a leak
# ---------------------------------------------------------------------------


async def test_cross_tenant_curation_denied_without_leak(world, file_db):
    async with file_db() as db:
        # Tenant B principal cannot even load the tenant A report.
        assert (
            await curation.load_report(
                db, principal=world["owner_b"], report_id=world["report"]
            )
            is None
        )
        # And a tenant B report is invisible to a tenant A principal.
        owner_b = world["owner_b"]
        doc_b = await _document(db, owner=owner_b, name="b.txt")
        report_b = await _seed_report(db, owner=owner_b, documents=[doc_b], title="B report")
        await db.flush()
        assert (
            await curation.load_report(
                db, principal=world["owner_a"], report_id=report_b.id
            )
            is None
        )


# ---------------------------------------------------------------------------
# Revoked sources block promotion; auto-demote preserves history
# ---------------------------------------------------------------------------


async def test_revoked_source_blocks_promotion(world, file_db):
    async with file_db() as db:
        report = await _load(db, world["report"])
        await curation.request_review(db, principal=world["owner_a"], report=report)
        doc = await db.get(Document, world["doc_hr"])
        doc.lifecycle_state = "deleted"
        doc.deleted_at = datetime.now(timezone.utc)
        await db.flush()
        with pytest.raises(curation.CurationStateError):
            await curation.approve(db, principal=world["steward_a"], report=report)
        assert report.curation_state == "under_review"


async def test_auto_demote_on_lost_source_preserves_history(world, file_db):
    async with file_db() as db:
        report = await _load(db, world["report"])
        await curation.request_review(db, principal=world["owner_a"], report=report)
        await curation.approve(db, principal=world["steward_a"], report=report)
        doc = await db.get(Document, world["doc_hr"])
        doc.lifecycle_state = "deleted"
        doc.deleted_at = datetime.now(timezone.utc)
        await db.flush()
        await curation.auto_demote(db, report=report, principal=world["steward_a"])
        assert report.curation_state == "none"
        await db.commit()
    async with file_db() as db:
        rows = (
            await db.execute(
                select(AuditRecord).where(
                    AuditRecord.resource_id == world["report"],
                    AuditRecord.action.like("governance.report.curation.%"),
                )
            )
        ).scalars().all()
        actions = {row.action.rsplit(".", 1)[-1] for row in rows}
        assert {"approve", "auto_demote"} <= actions, "history must be preserved"


# ---------------------------------------------------------------------------
# Withdrawal returns to non-curated; audit rows preserve transitions
# ---------------------------------------------------------------------------


async def test_withdraw_returns_to_none_and_keeps_audit(world, file_db):
    async with file_db() as db:
        report = await _load(db, world["report"])
        await curation.request_review(db, principal=world["owner_a"], report=report, reason="please")
        await curation.approve(db, principal=world["steward_a"], report=report)
        await curation.set_featured(db, principal=world["admin_a"], report=report, featured=True)
        # Featuring must not have changed the evidentiary state.
        assert report.curation_state == "approved" and report.featured is True
        await curation.withdraw(db, principal=world["owner_a"], report=report, reason="retract")
        assert report.curation_state == "none"
        assert bool(report.featured) is False
        assert report.curation_first_approver_id is None
        await db.commit()

    async with file_db() as db:
        rows = (
            await db.execute(
                select(AuditRecord).where(
                    AuditRecord.resource_id == world["report"],
                    AuditRecord.action.like("governance.report.curation.%"),
                )
            )
        ).scalars().all()
        assert len(rows) >= 4
        transitions = {row.action.rsplit(".", 1)[-1] for row in rows}
        assert {"request_review", "approve", "feature", "withdraw"} <= transitions
        # The withdrawal row records the prior state it moved away from.
        withdraw_row = next(row for row in rows if row.action.endswith(".withdraw"))
        meta = json.loads(withdraw_row.metadata_json)
        assert meta["prior_state"] == "approved" and meta["new_state"] == "none"


# ---------------------------------------------------------------------------
# Departmental ownership: derived, never guessed; mixed fails closed
# ---------------------------------------------------------------------------


async def test_department_derived_from_pinned_source_scope(world, file_db):
    async with file_db() as db:
        report = await _load(db, world["report"])
        await curation.request_review(db, principal=world["owner_a"], report=report)
        await curation.approve(db, principal=world["steward_a"], report=report)
        assert report.curation_department_id == world["hr"]


async def test_mixed_department_fails_closed_for_steward(world, file_db):
    async with file_db() as db:
        report = await _load(db, world["mixed_report"])
        await curation.request_review(db, principal=world["owner_a"], report=report)
        # A department steward cannot cover a mixed scope.
        with pytest.raises(AuthorizationError):
            await curation.approve(db, principal=world["steward_a"], report=report)
        # A tenant admin resolves it.
        await curation.approve(db, principal=world["admin_a"], report=report)
        assert report.curation_state == "approved"
        assert report.curation_department_id is None


# ---------------------------------------------------------------------------
# Archived governing scope invalidates steward authority
# ---------------------------------------------------------------------------


async def test_archiving_department_invalidates_steward_authority(world, file_db):
    async with file_db() as db:
        report = await _load(db, world["report"])
        await curation.request_review(db, principal=world["owner_a"], report=report)
        await db.commit()

    # Archive the governing department, then re-resolve the steward.
    async with file_db() as db:
        await governance.set_department_status(
            db,
            principal=_principal(ADMIN_A, TENANT_A, "owner"),
            department_id=world["hr"],
            status="archived",
        )
        await db.commit()
    async with file_db() as db:
        resolved = await _resolved(db, STEWARD_A, TENANT_A, "viewer")
        assert (StewardScopeType.DEPARTMENT.value, world["hr"]) not in resolved.steward_scopes
        report = await _load(db, world["report"])
        with pytest.raises(AuthorizationError):
            await curation.approve(db, principal=resolved, report=report)


# ---------------------------------------------------------------------------
# Featured is a separate presentation flag
# ---------------------------------------------------------------------------


async def test_feature_requires_admin_and_curated_state(world, file_db):
    async with file_db() as db:
        report = await _load(db, world["report"])
        # Cannot feature an uncurated report.
        with pytest.raises(curation.CurationStateError):
            await curation.set_featured(
                db, principal=world["admin_a"], report=report, featured=True
            )
        await curation.request_review(db, principal=world["owner_a"], report=report)
        await curation.approve(db, principal=world["steward_a"], report=report)
        # A non-admin cannot feature it.
        with pytest.raises(AuthorizationError):
            await curation.set_featured(
                db, principal=world["steward_a"], report=report, featured=True
            )
        await curation.set_featured(db, principal=world["admin_a"], report=report, featured=True)
        assert report.featured is True
        assert report.curation_state == "approved"


# ---------------------------------------------------------------------------
# Structured source revocation (DataSource gate) blocks promotion
# ---------------------------------------------------------------------------


async def test_disabled_structured_source_blocks_promotion(world, file_db):
    async with file_db() as db:
        owner_a = world["owner_a"]
        source = DataSource(
            tenant_id=TENANT_A,
            user_id=owner_a.user_id,
            name="Finance",
            engine="sqlite",
            connection_secret="x",
            status="connected",
            enabled=True,
            schema_json=json.dumps(
                [{"schema_name": "main", "name": "finance", "qualified_name": "finance",
                  "columns": [], "primary_key": [], "foreign_keys": []}]
            ),
            authorized_objects_json=json.dumps(["finance"]),
        )
        db.add(source)
        await db.flush()
        doc = await _document(db, owner=owner_a, department_id=world["hr"], name="d2.txt")
        report = await _seed_report(db, owner=owner_a, documents=[doc], source=source)
        await curation.request_review(db, principal=owner_a, report=report)
        source.enabled = False
        await db.flush()
        with pytest.raises(curation.CurationStateError):
            await curation.approve(db, principal=world["steward_a"], report=report)
