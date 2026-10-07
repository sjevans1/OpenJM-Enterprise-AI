"""BV1-C governed structured Data source policy and report revalidation.

Proves that an unauthorized Data source is removed before schema/planning/SQL
exposure, and that a saved report whose evidence is no longer authorized fails
closed on open/list/export.
"""

import json

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.identity import AuthorizationError, Principal
from app.core.permissions import permissions_for_role
from app.core.tenancy import LEGACY_TENANT_ID
from app.models import AuditRecord, DataSource, Tenant
from app.services import access_governance as governance
from app.services import identity as identity_service
from app.services.document_policy import (
    DocumentAccess,
    data_source_is_visible,
    visible_data_sources,
)

TENANT_A = "tnt-bv1c-a"
TENANT_B = "tnt-bv1c-b"
ADMIN_A = "bv1c-admin-a"
VIEWER_A = "bv1c-viewer-a"
settings = get_settings()


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
        "principal_id": ADMIN_A,
        "owner_key": f"{TENANT_A}:{ADMIN_A}",
    }
    base.update(kwargs)
    return DocumentAccess(**base)


def _source(**kwargs) -> DataSource:
    base = {
        "id": "src",
        "tenant_id": TENANT_A,
        "user_id": f"{TENANT_A}:{ADMIN_A}",
        "name": "Finance",
        "engine": "sqlite",
        "connection_secret": "x",
        "status": "connected",
        "enabled": True,
        "schema_json": json.dumps([]),
        "classification": "internal",
        "tenant_visible": True,
    }
    base.update(kwargs)
    return DataSource(**base)


@pytest.fixture
async def world(file_db):
    async with file_db() as db:
        db.add_all(
            [
                Tenant(id=TENANT_A, slug="bv1c-a", name="A", status="active"),
                Tenant(id=TENANT_B, slug="bv1c-b", name="B", status="active"),
            ]
        )
        await db.flush()
        for subject, pid in (("sub:admin-a", ADMIN_A), ("sub:viewer-a", VIEWER_A)):
            await identity_service.get_or_create_principal(db, subject=subject, principal_id=pid)
        await identity_service.add_membership(db, tenant_id=TENANT_A, principal_id=ADMIN_A, role="owner")
        await identity_service.add_membership(db, tenant_id=TENANT_A, principal_id=VIEWER_A, role="viewer")
        await db.commit()
    return {
        "admin_a": _principal(ADMIN_A, TENANT_A, "owner"),
        "viewer_a": _principal(VIEWER_A, TENANT_A, "viewer"),
    }


# ---------------------------------------------------------------------------
# Predicate
# ---------------------------------------------------------------------------


def test_data_source_default_internal_visible():
    assert data_source_is_visible(_access(), _source(classification="internal"))


def test_data_source_highly_restricted_requires_grant():
    source = _source(classification="highly_restricted")
    assert not data_source_is_visible(_access(), source)
    granted = _source(
        classification="highly_restricted",
        allowed_group_ids_json=json.dumps(["hr-leadership"]),
    )
    assert data_source_is_visible(_access(group_ids=frozenset({"hr-leadership"})), granted)
    assert not data_source_is_visible(_access(group_ids=frozenset({"finance"})), granted)


def test_data_source_cross_tenant_never_visible():
    assert not data_source_is_visible(_access(), _source(tenant_id=TENANT_B))


def test_data_source_department_steward_visibility():
    source = _source(classification="confidential", department_id="dept-fin")
    assert data_source_is_visible(_access(department_ids=frozenset({"dept-fin"})), source)
    assert data_source_is_visible(
        _access(steward_scopes=frozenset({("department", "dept-fin")})), source
    )
    assert not data_source_is_visible(_access(), source)


def test_visible_data_sources_filters_the_set():
    sources = [
        _source(id="open", classification="internal"),
        _source(id="secret", classification="highly_restricted"),
    ]
    assert [s.id for s in visible_data_sources(_access(), sources)] == ["open"]


# ---------------------------------------------------------------------------
# Pre-planner enforcement through the orchestrator
# ---------------------------------------------------------------------------


async def test_orchestrator_removes_unauthorized_source_before_planning(world, file_db):
    from app.services.orchestrator import orchestrator

    async with file_db() as db:
        db.add(_source(id="src-open", classification="internal"))
        db.add(_source(id="src-secret", classification="highly_restricted"))
        await db.commit()

    async with file_db() as db:
        sources = await orchestrator._structured_sources(
            db, f"{TENANT_A}:{ADMIN_A}", access=_access()
        )
    assert [s.id for s in sources] == ["src-open"]

    # Without an access context (legacy/internal path) nothing is filtered.
    async with file_db() as db:
        legacy = await orchestrator._structured_sources(db, f"{TENANT_A}:{ADMIN_A}")
    assert {s.id for s in legacy} == {"src-open", "src-secret"}


# ---------------------------------------------------------------------------
# Report revalidation
# ---------------------------------------------------------------------------


def _structured_evidence(source_id: str):
    from app.schemas import Evidence

    return Evidence(
        source_type="structured_query",
        source_id=source_id,
        title="Finance",
        passage='{"columns":["x"],"rows":[[1]],"row_count":1}',
        metadata={"tables": ["finance"]},
    )


async def test_saved_report_becomes_unavailable_after_policy_revocation(world, file_db):
    from app.api.reports import _sources_available

    async with file_db() as db:
        db.add(
            DataSource(
                id="rep-src",
                tenant_id=LEGACY_TENANT_ID,
                user_id=settings.dev_user_id,
                name="Finance",
                engine="sqlite",
                connection_secret="x",
                status="connected",
                enabled=True,
                schema_json=json.dumps([]),
                authorized_objects_json=json.dumps(["finance"]),
                classification="internal",
            )
        )
        await db.commit()

    async with file_db() as db:
        assert await _sources_available(db, [_structured_evidence("rep-src")]) is True

    async with file_db() as db:
        source = await db.get(DataSource, "rep-src")
        source.classification = "highly_restricted"
        await db.commit()

    async with file_db() as db:
        assert await _sources_available(db, [_structured_evidence("rep-src")]) is False


async def test_saved_report_document_revalidation(world, file_db):
    from app.api.reports import _sources_available
    from app.models import Document
    from app.schemas import Evidence

    async with file_db() as db:
        db.add(
            Document(
                id="rep-doc",
                tenant_id=LEGACY_TENANT_ID,
                user_id=settings.dev_user_id,
                original_name="p.md",
                stored_path="/tmp/p.md",
                size_bytes=1,
                status="ready",
                indexed=True,
                lifecycle_state="ready",
                classification="internal",
            )
        )
        await db.commit()

    evidence = [
        Evidence(source_type="document", source_id="rep-doc", title="p", passage="x")
    ]
    async with file_db() as db:
        assert await _sources_available(db, evidence) is True
    async with file_db() as db:
        document = await db.get(Document, "rep-doc")
        document.classification = "highly_restricted"
        await db.commit()
    async with file_db() as db:
        assert await _sources_available(db, evidence) is False


# ---------------------------------------------------------------------------
# Steward/admin mutation
# ---------------------------------------------------------------------------


async def test_data_source_policy_mutation_requires_scope_and_audits(world, file_db):
    async with file_db() as db:
        db.add(_source(id="src-1", classification="internal"))
        await db.commit()

    async with file_db() as db:
        updated = await governance.set_data_source_policy(
            db,
            principal=world["admin_a"],
            source_id="src-1",
            classification="highly_restricted",
            tenant_visible=False,
        )
        await db.commit()
    assert updated is not None and updated.classification == "highly_restricted"

    async with file_db() as db:
        audits = (
            await db.execute(
                select(AuditRecord).where(
                    AuditRecord.action == "governance.data_source.policy"
                )
            )
        ).scalars().all()
    assert len(audits) == 1

    async with file_db() as db:
        with pytest.raises(AuthorizationError):
            await governance.set_data_source_policy(
                db, principal=world["viewer_a"], source_id="src-1", classification="internal"
            )


async def test_data_source_policy_cannot_touch_another_tenant(world, file_db):
    async with file_db() as db:
        db.add(_source(id="src-b", tenant_id=TENANT_B, user_id=f"{TENANT_B}:{ADMIN_A}"))
        await db.commit()
    async with file_db() as db:
        assert (
            await governance.set_data_source_policy(
                db, principal=world["admin_a"], source_id="src-b", classification="public"
            )
            is None
        )


# ---------------------------------------------------------------------------
# Migration 0010
# ---------------------------------------------------------------------------


def test_revision_0010_adds_columns(tmp_path):
    from sqlalchemy import create_engine, inspect

    from app.migrations_runner import adopt_and_upgrade, current_revision, sync_url_for

    url = f"sqlite+aiosqlite:///{tmp_path / 'bv1c.db'}"
    adopt_and_upgrade(url)
    head = current_revision(url)
    assert head is not None
    engine = create_engine(sync_url_for(url), future=True)
    try:
        columns = {c["name"] for c in inspect(engine).get_columns("data_sources")}
    finally:
        engine.dispose()
    assert {"classification", "department_id", "tenant_visible", "allowed_group_ids_json"} <= columns
