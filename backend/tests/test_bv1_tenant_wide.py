"""Tenant-wide `internal` semantics with the connector intersecting gate.

Implements the resolved product decision (Issue #45 / PR #48): a native governed
Knowledge source classified public/internal with tenant_visible is available to
every active member of the tenant, not only its uploader. Connector-origin
content must additionally satisfy the current connector authorization gate;
classification never widens provider-side authorization.
"""

import json

import pytest

from app.models import Document, Tenant
from app.services import document_policy
from app.services import identity as identity_service
from app.services.connectors import authorization as connector_authorization
from app.services.document_policy import DocumentAccess, governed_tenant_documents

TENANT_A = "tnt-wide-a"
TENANT_B = "tnt-wide-b"
UPLOADER = "wide-uploader"
MEMBER = "wide-member"
ADMIN = "wide-admin"


def _key(tenant: str, principal: str) -> str:
    return f"{tenant}:{principal}"


def _access(principal_id: str, tenant_id: str = TENANT_A, **kwargs) -> DocumentAccess:
    return DocumentAccess(
        tenant_id=tenant_id,
        principal_id=principal_id,
        owner_key=_key(tenant_id, principal_id),
        **kwargs,
    )


def _doc(**kwargs) -> Document:
    base = {
        "id": "doc",
        "tenant_id": TENANT_A,
        "user_id": _key(TENANT_A, UPLOADER),
        "original_name": "handbook.md",
        "stored_path": "/tmp/handbook.md",
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
                Tenant(id=TENANT_A, slug="wide-a", name="A", status="active"),
                Tenant(id=TENANT_B, slug="wide-b", name="B", status="active"),
            ]
        )
        await db.flush()
        for subject, pid in (
            ("sub:uploader", UPLOADER),
            ("sub:member", MEMBER),
            ("sub:admin", ADMIN),
        ):
            await identity_service.get_or_create_principal(db, subject=subject, principal_id=pid)
        for pid, role in ((UPLOADER, "editor"), (MEMBER, "viewer"), (ADMIN, "owner")):
            await identity_service.add_membership(
                db, tenant_id=TENANT_A, principal_id=pid, role=role
            )
        await db.commit()
    return {}


async def test_non_owner_member_retrieves_internal_document(world, file_db):
    """The HR handbook example: an ordinary member retrieves what they did not upload."""
    async with file_db() as db:
        db.add(_doc(id="handbook", classification="internal"))
        await db.commit()

    async with file_db() as db:
        member_documents = await governed_tenant_documents(db, _access(MEMBER))
    assert {d.id for d in member_documents} == {"handbook"}


async def test_non_owner_member_cannot_retrieve_highly_restricted(world, file_db):
    async with file_db() as db:
        db.add(_doc(id="compensation", classification="highly_restricted"))
        await db.commit()

    async with file_db() as db:
        member_documents = await governed_tenant_documents(db, _access(MEMBER))
    assert member_documents == []


async def test_confidential_needs_explicit_group_even_for_members(world, file_db):
    async with file_db() as db:
        db.add(
            _doc(
                id="hr-pay",
                classification="confidential",
                allowed_group_ids_json=json.dumps(["hr-leadership"]),
            )
        )
        await db.commit()

    async with file_db() as db:
        assert await governed_tenant_documents(db, _access(MEMBER)) == []
    async with file_db() as db:
        granted = await governed_tenant_documents(
            db, _access(MEMBER, group_ids=frozenset({"hr-leadership"}))
        )
    assert {d.id for d in granted} == {"hr-pay"}


async def test_tenant_wide_never_crosses_the_tenant_boundary(world, file_db):
    async with file_db() as db:
        db.add(_doc(id="other-tenant", tenant_id=TENANT_B, user_id=_key(TENANT_B, UPLOADER)))
        await db.commit()
    async with file_db() as db:
        member_documents = await governed_tenant_documents(db, _access(MEMBER))
    assert member_documents == []


async def test_uploader_still_retrieves_their_own_internal_document(world, file_db):
    async with file_db() as db:
        db.add(_doc(id="mine", classification="internal"))
        await db.commit()
    async with file_db() as db:
        documents = await governed_tenant_documents(db, _access(UPLOADER))
    assert {d.id for d in documents} == {"mine"}


async def test_connector_origin_document_requires_connector_gate(
    world, file_db, monkeypatch
):
    """Classification must not broaden provider authorization."""
    async with file_db() as db:
        db.add(_doc(id="conn-doc", classification="internal"))
        await db.commit()

    async def origin(db, *, tenant_id):
        return frozenset({"conn-doc"})

    monkeypatch.setattr(document_policy, "connector_origin_document_ids", origin)

    async def denied(db):
        return set()

    monkeypatch.setattr(
        connector_authorization, "authorized_connector_document_ids_for_context", denied
    )
    async with file_db() as db:
        assert await governed_tenant_documents(db, _access(MEMBER)) == []

    async def allowed(db):
        return {"conn-doc"}

    monkeypatch.setattr(
        connector_authorization, "authorized_connector_document_ids_for_context", allowed
    )
    async with file_db() as db:
        documents = await governed_tenant_documents(db, _access(MEMBER))
    assert {d.id for d in documents} == {"conn-doc"}


async def test_connector_origin_highly_restricted_still_denied_when_gate_allows(
    world, file_db, monkeypatch
):
    """A connector allow must not widen the classification."""
    async with file_db() as db:
        db.add(_doc(id="conn-secret", classification="highly_restricted"))
        await db.commit()

    async def origin(db, *, tenant_id):
        return frozenset({"conn-secret"})

    async def allowed(db):
        return {"conn-secret"}

    monkeypatch.setattr(document_policy, "connector_origin_document_ids", origin)
    monkeypatch.setattr(
        connector_authorization, "authorized_connector_document_ids_for_context", allowed
    )
    async with file_db() as db:
        assert await governed_tenant_documents(db, _access(MEMBER)) == []
