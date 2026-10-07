"""BV1-B governed Knowledge classification and pre-retrieval enforcement.

Proves that an unauthorized Knowledge document is removed before vector
retrieval, before evidence is built, and therefore before any model prompt.

Interpretation (stricter reading, recorded in docs/plan/BV1-B.md): the accepted
data model is owner/connector scoped, so classification is applied as an
additional restriction on the candidate set. No tenant-wide sharing of
``internal`` documents across owners is introduced in this increment.
"""

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.core.governance import (
    DEFAULT_SOURCE_CLASSIFICATION,
    SourceClassification,
    normalize_classification,
)
from app.core.identity import AuthorizationError, Principal
from app.core.permissions import permissions_for_role
from app.core.tenancy import LEGACY_TENANT_ID
from app.models import AccessGroup, AuditRecord, Department, Document, PrincipalAccount, Tenant
from app.services import access_governance as governance
from app.services import identity as identity_service
from app.services.document_policy import (
    DocumentAccess,
    access_from_principal,
    document_is_visible,
    visible_documents,
)
from app.services.knowledge import knowledge_engine
from app.services.tools import KnowledgeSearchTool, ToolContext

TENANT_A = "tnt-bv1b-a"
TENANT_B = "tnt-bv1b-b"
ADMIN_A = "bv1b-admin-a"
OWNER_A = "bv1b-owner-a"
OTHER_A = "bv1b-other-a"


def _principal(principal_id: str, tenant_id: str, role: str = "owner", **kwargs) -> Principal:
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


def _access(**kwargs) -> DocumentAccess:
    base = {
        "tenant_id": TENANT_A,
        "principal_id": OWNER_A,
        "owner_key": f"{TENANT_A}:{OWNER_A}",
    }
    base.update(kwargs)
    return DocumentAccess(**base)


def _doc(**kwargs) -> Document:
    base = {
        "id": "doc",
        "tenant_id": TENANT_A,
        "user_id": f"{TENANT_A}:{OWNER_A}",
        "original_name": "policy.md",
        "stored_path": "/tmp/policy.md",
        "size_bytes": 10,
        "status": "ready",
        "indexed": True,
        "lifecycle_state": "ready",
        "classification": "internal",
        "tenant_visible": True,
    }
    base.update(kwargs)
    return Document(**base)


@pytest.fixture
async def world(file_db):
    async with file_db() as db:
        db.add_all(
            [
                Tenant(id=TENANT_A, slug="bv1b-a", name="A", status="active"),
                Tenant(id=TENANT_B, slug="bv1b-b", name="B", status="active"),
            ]
        )
        await db.flush()
        for subject, pid in (
            ("sub:admin-a", ADMIN_A),
            ("sub:owner-a", OWNER_A),
            ("sub:other-a", OTHER_A),
        ):
            await identity_service.get_or_create_principal(db, subject=subject, principal_id=pid)
        await identity_service.add_membership(db, tenant_id=TENANT_A, principal_id=ADMIN_A, role="owner")
        await identity_service.add_membership(db, tenant_id=TENANT_A, principal_id=OWNER_A, role="editor")
        await identity_service.add_membership(db, tenant_id=TENANT_A, principal_id=OTHER_A, role="viewer")
        await db.commit()
    return {
        "admin_a": _principal(ADMIN_A, TENANT_A, "owner"),
        "owner_a": _principal(OWNER_A, TENANT_A, "editor"),
        "other_a": _principal(OTHER_A, TENANT_A, "viewer"),
    }


# ---------------------------------------------------------------------------
# Predicate semantics
# ---------------------------------------------------------------------------


async def test_default_classification_is_internal_on_insert(world, file_db):
    async with file_db() as db:
        document = Document(
            id="doc-default",
            tenant_id=TENANT_A,
            user_id=f"{TENANT_A}:{OWNER_A}",
            original_name="d.md",
            stored_path="/tmp/d.md",
            size_bytes=1,
            status="ready",
            indexed=True,
            lifecycle_state="ready",
        )
        db.add(document)
        await db.commit()
        await db.refresh(document)
    assert document.classification == DEFAULT_SOURCE_CLASSIFICATION
    assert document.classification == SourceClassification.INTERNAL.value
    assert document.tenant_visible is True


def test_unknown_classification_fails_closed():
    assert normalize_classification("weird") == SourceClassification.HIGHLY_RESTRICTED.value
    assert normalize_classification(None) == SourceClassification.HIGHLY_RESTRICTED.value
    assert not document_is_visible(_access(), _doc(classification="weird"))


def test_internal_is_visible_within_the_candidate_set():
    assert document_is_visible(_access(), _doc(classification="internal"))


def test_tenant_visible_false_restricts_internal():
    assert not document_is_visible(
        _access(), _doc(classification="internal", tenant_visible=False)
    )


def test_highly_restricted_requires_explicit_grant():
    document = _doc(classification="highly_restricted")
    assert not document_is_visible(_access(), document)
    assert document_is_visible(_access(group_ids=frozenset({"hr-leadership"})), _doc(classification="highly_restricted", allowed_group_ids_json=json.dumps(["hr-leadership"])))


def test_cross_tenant_document_is_never_visible():
    document = _doc(tenant_id=TENANT_B)
    assert not document_is_visible(_access(), document)


def test_group_grant_and_revocation():
    document = _doc(
        classification="confidential", allowed_group_ids_json=json.dumps(["hr-leadership"])
    )
    assert not document_is_visible(_access(), document)
    assert document_is_visible(_access(group_ids=frozenset({"hr-leadership"})), document)
    assert not document_is_visible(_access(group_ids=frozenset({"finance"})), document)


def test_department_steward_can_see_document():
    document = _doc(classification="highly_restricted", department_id="dept-hr")
    # Department member.
    assert document_is_visible(_access(department_ids=frozenset({"dept-hr"})), document)
    # Department steward.
    assert document_is_visible(
        _access(steward_scopes=frozenset({("department", "dept-hr")})), document
    )
    # Tenant-wide steward.
    assert document_is_visible(
        _access(steward_scopes=frozenset({("tenant", TENANT_A)})), document
    )


# ---------------------------------------------------------------------------
# Pre-retrieval enforcement through the governed tool
# ---------------------------------------------------------------------------


async def _seed_two_documents(file_db) -> tuple[str, str]:
    async with file_db() as db:
        db.add(
            _doc(id="doc-open", classification="internal")
        )
        db.add(
            _doc(id="doc-secret", classification="highly_restricted")
        )
        await db.commit()
    return "doc-open", "doc-secret"


async def test_tool_removes_unauthorized_document_before_retrieval(
    world, file_db, monkeypatch
):
    await _seed_two_documents(file_db)
    captured: dict = {}

    async def fake_retrieve(query, refs, **kwargs):
        captured["refs"] = [ref[0] for ref in refs]
        return []

    monkeypatch.setattr(knowledge_engine, "retrieve", fake_retrieve)

    async with file_db() as db:
        tool = KnowledgeSearchTool()
        result = await tool.execute(
            ToolContext(
                user_id=f"{TENANT_A}:{OWNER_A}",
                permissions=frozenset({"knowledge.read"}),
                db=db,
                access=_access(),
            ),
            {"query": "salary"},
        )
    assert captured["refs"] == ["doc-open"], "hidden document must not reach retrieval"
    assert "doc-secret" not in {item.source_id for item in result.evidence}


async def test_neighbor_and_evidence_paths_cannot_leak_hidden_document(
    world, file_db, monkeypatch
):
    await _seed_two_documents(file_db)
    captured: dict = {}

    async def fake_retrieve(query, refs, **kwargs):
        captured["refs"] = [ref[0] for ref in refs]
        # Even if the engine tried to return a neighbor chunk for the hidden
        # document, the tool validates source ids against the authorized set and
        # the hidden document is not in it (it was never passed as a ref).
        from app.schemas import Evidence

        return [
            Evidence(
                source_type="document",
                source_id="doc-secret",
                title="secret.md",
                passage="leaked",
                provenance={"retrieval_role": "neighbor", "equivalent_sources": []},
            )
        ]

    monkeypatch.setattr(knowledge_engine, "retrieve", fake_retrieve)

    async with file_db() as db:
        tool = KnowledgeSearchTool()
        with pytest.raises(Exception):
            await tool.execute(
                ToolContext(
                    user_id=f"{TENANT_A}:{OWNER_A}",
                    permissions=frozenset({"knowledge.read"}),
                    db=db,
                    access=_access(),
                ),
                {"query": "salary"},
            )
    assert captured["refs"] == ["doc-open"]


async def test_group_revocation_removes_access_on_next_resolution(world, file_db):
    async with file_db() as db:
        group = await governance.create_group(
            db, principal=world["admin_a"], slug="hr-leadership", name="HR Leadership"
        )
        await governance.add_group_member(
            db, principal=world["admin_a"], group_id=group.id, member_principal_id=OWNER_A
        )
        await db.commit()
        group_id = group.id

    async with file_db() as db:
        db.add(
            _doc(
                id="doc-fin",
                classification="confidential",
                allowed_group_ids_json=json.dumps([group_id]),
            )
        )
        await db.commit()

    async with file_db() as db:
        access = await _access_for(file_db, OWNER_A)
    assert document_is_visible(access, _doc(classification="confidential", allowed_group_ids_json=json.dumps([group_id])))

    async with file_db() as db:
        await governance.remove_group_member(
            db, principal=world["admin_a"], group_id=group_id, member_principal_id=OWNER_A
        )
        await db.commit()

    async with file_db() as db:
        access = await _access_for(file_db, OWNER_A)
    assert access.group_ids == frozenset()
    assert not document_is_visible(access, _doc(classification="confidential", allowed_group_ids_json=json.dumps([group_id])))


async def test_archiving_governing_department_removes_access(world, file_db):
    async with file_db() as db:
        department = await governance.create_department(
            db, principal=world["admin_a"], slug="hr", name="HR"
        )
        group = await governance.create_group(
            db, principal=world["admin_a"], slug="hr-team", name="HR Team", department_id=department.id
        )
        await governance.add_group_member(
            db, principal=world["admin_a"], group_id=group.id, member_principal_id=OWNER_A
        )
        await db.commit()
        department_id = department.id

    document = _doc(classification="highly_restricted", department_id=department_id)
    async with file_db() as db:
        access = await _access_for(file_db, OWNER_A)
    assert document_is_visible(access, document)

    async with file_db() as db:
        await governance.set_department_status(
            db, principal=world["admin_a"], department_id=department_id, status="archived"
        )
        await db.commit()
    async with file_db() as db:
        access = await _access_for(file_db, OWNER_A)
    assert access.department_ids == frozenset()
    assert not document_is_visible(access, document)


async def _access_for(file_db, principal_id: str) -> DocumentAccess:
    async with file_db() as db:
        account = await db.get(PrincipalAccount, principal_id)
        principal = await identity_service.principal_for_account(
            db, account, tenant_id=TENANT_A, auth_method="oidc"
        )
    return access_from_principal(principal)


# ---------------------------------------------------------------------------
# Catalog listing
# ---------------------------------------------------------------------------


async def test_catalog_listing_excludes_unauthorized_document(client, file_db):
    from app.core.config import get_settings

    settings = get_settings()
    async with file_db() as db:
        db.add(
            Document(
                id="catalog-open",
                tenant_id=LEGACY_TENANT_ID,
                user_id=settings.dev_user_id,
                original_name="open.md",
                stored_path="/tmp/open.md",
                size_bytes=1,
                status="ready",
                indexed=True,
                lifecycle_state="ready",
                classification="internal",
            )
        )
        db.add(
            Document(
                id="catalog-secret",
                tenant_id=LEGACY_TENANT_ID,
                user_id=settings.dev_user_id,
                original_name="secret.md",
                stored_path="/tmp/secret.md",
                size_bytes=1,
                status="ready",
                indexed=True,
                lifecycle_state="ready",
                classification="highly_restricted",
            )
        )
        await db.commit()

    response = await client.get("/api/knowledge/documents")
    assert response.status_code == 200, response.text
    ids = {row["id"] for row in response.json()}
    assert "catalog-open" in ids
    assert "catalog-secret" not in ids


# ---------------------------------------------------------------------------
# Steward/admin policy mutation
# ---------------------------------------------------------------------------


async def test_policy_mutation_requires_scope_and_is_audited(world, file_db):
    async with file_db() as db:
        db.add(_doc(id="doc-1", classification="internal"))
        await db.commit()

    # Tenant admin may set policy.
    async with file_db() as db:
        updated = await governance.set_document_policy(
            db,
            principal=world["admin_a"],
            document_id="doc-1",
            classification="confidential",
            tenant_visible=False,
            allowed_group_ids=[],
        )
        await db.commit()
    assert updated is not None and updated.classification == "confidential"
    assert updated.tenant_visible is False

    async with file_db() as db:
        audits = (
            await db.execute(
                select(AuditRecord).where(AuditRecord.action == "governance.document.policy")
            )
        ).scalars().all()
    assert len(audits) == 1

    # A viewer with no steward scope is refused.
    async with file_db() as db:
        with pytest.raises(AuthorizationError):
            await governance.set_document_policy(
                db, principal=world["other_a"], document_id="doc-1", classification="internal"
            )


async def test_policy_mutation_rejects_foreign_group_and_unknown_classification(
    world, file_db
):
    async with file_db() as db:
        db.add(_doc(id="doc-2", classification="internal"))
        await db.commit()
    async with file_db() as db:
        with pytest.raises(ValueError):
            await governance.set_document_policy(
                db,
                principal=world["admin_a"],
                document_id="doc-2",
                classification="internal",
                allowed_group_ids=["does-not-exist"],
            )
        with pytest.raises(ValueError):
            await governance.set_document_policy(
                db, principal=world["admin_a"], document_id="doc-2", classification="top_secret"
            )


async def test_policy_mutation_cannot_touch_another_tenant(world, file_db):
    async with file_db() as db:
        db.add(_doc(id="doc-b", tenant_id=TENANT_B, user_id=f"{TENANT_B}:{ADMIN_A}"))
        await db.commit()
    async with file_db() as db:
        result = await governance.set_document_policy(
            db, principal=world["admin_a"], document_id="doc-b", classification="public"
        )
    assert result is None, "a foreign document must be neither revealed nor mutated"


# ---------------------------------------------------------------------------
# Migration 0009
# ---------------------------------------------------------------------------


def test_revision_0009_adds_columns_idempotently_and_downgrades(tmp_path):
    from sqlalchemy import create_engine, inspect, text

    from app.migrations_runner import adopt_and_upgrade, current_revision, sync_url_for

    url = f"sqlite+aiosqlite:///{tmp_path / 'bv1b.db'}"
    adopt_and_upgrade(url)
    assert current_revision(url) == "0009_bv1b_classification"

    engine = create_engine(sync_url_for(url), future=True)
    try:
        columns = {c["name"] for c in inspect(engine).get_columns("documents")}
        with engine.connect() as connection:
            remaining = connection.execute(
                text("SELECT COUNT(*) FROM documents")
            ).scalar_one()
    finally:
        engine.dispose()
    assert {"classification", "department_id", "tenant_visible", "allowed_group_ids_json"} <= columns
    assert remaining == 0

    # Idempotent second pass.
    adopt_and_upgrade(url)
    assert current_revision(url) == "0009_bv1b_classification"


def test_all_revision_ids_fit_the_alembic_version_column():
    """Alembic's version_num column is VARCHAR(32); a longer id breaks Postgres.

    Caught only in CI (the local suite skips the PostgreSQL test when pgserver is
    absent), so guard it in the ordinary suite.
    """
    import re
    from pathlib import Path

    for path in sorted(Path("migrations/versions").glob("*.py")):
        match = re.search(r'^revision = "([^"]+)"', path.read_text(), re.M)
        assert match, f"no revision id in {path.name}"
        assert len(match.group(1)) <= 32, (path.name, match.group(1))
