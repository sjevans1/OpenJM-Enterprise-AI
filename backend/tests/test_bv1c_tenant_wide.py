"""Tenant-wide `internal` semantics for Data sources and report revalidation.

Same resolved product decision as BV1-B: a governed native Data source
classified public/internal is available to every active member of the tenant;
confidential/highly-restricted need explicit authorization. Report open/list/
export revalidates the same current policy.
"""

import json

import pytest

from app.core.context import set_principal
from app.core.identity import Principal
from app.core.permissions import permissions_for_role
from app.models import DataSource, Document, Tenant
from app.services import identity as identity_service
from app.services.document_policy import DocumentAccess

TENANT_A = "tnt-widec-a"
TENANT_B = "tnt-widec-b"
UPLOADER = "widec-uploader"
MEMBER = "widec-member"


def _key(tenant: str, principal: str) -> str:
    return f"{tenant}:{principal}"


def _access(principal_id: str, tenant_id: str = TENANT_A, **kwargs) -> DocumentAccess:
    return DocumentAccess(
        tenant_id=tenant_id,
        principal_id=principal_id,
        owner_key=_key(tenant_id, principal_id),
        **kwargs,
    )


def _source(**kwargs) -> DataSource:
    base = {
        "id": "src",
        "tenant_id": TENANT_A,
        "user_id": _key(TENANT_A, UPLOADER),
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
        db.add(Tenant(id=TENANT_A, slug="widec-a", name="A", status="active"))
        await db.flush()
        for subject, pid in (("sub:uploader", UPLOADER), ("sub:member", MEMBER)):
            await identity_service.get_or_create_principal(db, subject=subject, principal_id=pid)
        for pid, role in ((UPLOADER, "editor"), (MEMBER, "viewer")):
            await identity_service.add_membership(
                db, tenant_id=TENANT_A, principal_id=pid, role=role
            )
        await db.commit()
    return {}


async def test_non_owner_member_can_query_internal_source(world, file_db):
    from app.services.orchestrator import orchestrator

    async with file_db() as db:
        db.add(_source(id="src-open", classification="internal"))
        db.add(_source(id="src-secret", classification="highly_restricted"))
        await db.commit()

    async with file_db() as db:
        sources = await orchestrator._structured_sources(
            db, _key(TENANT_A, MEMBER), access=_access(MEMBER)
        )
    assert [s.id for s in sources] == ["src-open"]


async def test_member_cannot_query_highly_restricted_source(world, file_db):
    from app.services.orchestrator import orchestrator

    async with file_db() as db:
        db.add(_source(id="src-secret", classification="highly_restricted"))
        await db.commit()
    async with file_db() as db:
        sources = await orchestrator._structured_sources(
            db, _key(TENANT_A, MEMBER), access=_access(MEMBER)
        )
    assert sources == []


async def test_report_revalidation_is_tenant_wide(world, file_db):
    from app.api.reports import _sources_available
    from app.schemas import Evidence

    async with file_db() as db:
        db.add(
            Document(
                id="rep-handbook",
                tenant_id=TENANT_A,
                user_id=_key(TENANT_A, UPLOADER),
                original_name="handbook.md",
                stored_path="/tmp/handbook.md",
                size_bytes=1,
                status="ready",
                indexed=True,
                lifecycle_state="ready",
                classification="internal",
            )
        )
        await db.commit()

    principal = Principal(
        principal_id=MEMBER,
        tenant_id=TENANT_A,
        subject="sub:member",
        role="viewer",
        membership_id="m",
        auth_method="local-dev",
        permissions=permissions_for_role("viewer"),
    )
    evidence = [
        Evidence(source_type="document", source_id="rep-handbook", title="handbook", passage="x")
    ]

    set_principal(principal)
    async with file_db() as db:
        assert await _sources_available(db, evidence) is True

    async with file_db() as db:
        document = await db.get(Document, "rep-handbook")
        document.classification = "highly_restricted"
        await db.commit()
    set_principal(principal)
    async with file_db() as db:
        assert await _sources_available(db, evidence) is False
