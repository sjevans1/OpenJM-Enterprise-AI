"""Lane K: delegated OpenJM support-content read path acceptance.

The support-delegation *records* already existed (migration 0012). This lane
adds the missing application read-path consumer and pins its boundaries:

* platform-operator status alone never grants customer-content access;
* a tenant-created, time-bound, explicitly ``content``-scoped delegation is the
  only thing that admits a support read;
* a ``metadata`` delegation grants no content, and a ``content`` delegation
  grants no tenant-metadata read;
* an expired or revoked delegation fails closed on the next request;
* a delegation for tenant A can never reach tenant B;
* every resolved support read (allow and deny) writes an audit row;
* a support content read does not mutate customer content.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.identity import Principal
from app.core.platform import PlatformCapability
from app.core.tenancy import LEGACY_PRINCIPAL_ID
from app.models import (
    AccessGroup,
    AuditRecord,
    Department,
    Document,
    SupportDelegation,
    Tenant,
)
from app.services import access_governance as governance
from app.services import support_access
from app.services.support_access import SupportAccessError

TENANT_A = "tnt-k-a"
TENANT_B = "tnt-k-b"
GROUP_HR = "grp-k-hr"
DEPT_HR = "dept-k-hr"


def _operator(*capabilities, principal_id="op-k", tenant_id="tnt-op") -> Principal:
    """A platform operator with only explicit platform capabilities."""
    return Principal(
        principal_id=principal_id,
        tenant_id=tenant_id,
        subject=f"sub:{principal_id}",
        role="",
        membership_id="m",
        auth_method="oidc",
        permissions=frozenset(),
        platform_capabilities=frozenset(c.value for c in capabilities),
    )


def _document(document_id: str, tenant_id: str, name: str) -> Document:
    return Document(
        id=document_id,
        tenant_id=tenant_id,
        user_id=f"{tenant_id}:owner",
        original_name=name,
        stored_path=f"/tmp/{document_id}",
        size_bytes=10,
        status="ready",
        indexed=True,
        lifecycle_state="ready",
        classification="internal",
        tenant_visible=True,
    )


@pytest.fixture
async def world(file_db):
    """Two tenants with one retrievable document each; the delegated actor is
    the dev-authenticated legacy principal (so the API path resolves a real
    server-derived identity)."""
    async with file_db() as db:
        db.add_all(
            [
                Tenant(id=TENANT_A, slug="k-a", name="A", status="active"),
                Tenant(id=TENANT_B, slug="k-b", name="B", status="active"),
            ]
        )
        await db.flush()
        db.add_all(
            [
                _document("doc-k-a", TENANT_A, "a-policy.txt"),
                _document("doc-k-b", TENANT_B, "b-policy.txt"),
            ]
        )
        await db.commit()
    return {"actor": LEGACY_PRINCIPAL_ID}


async def _grant_content(
    file_db,
    *,
    tenant_id,
    scope="content",
    expires_at=None,
    principal_id=LEGACY_PRINCIPAL_ID,
    classification_ceiling=None,
    allowed_group_ids=None,
    department_id=None,
):
    operator = _operator(PlatformCapability.OPERATORS_ADMIN, PlatformCapability.CONTENT_SUPPORT)
    kwargs = {}
    if classification_ceiling is not None:
        kwargs["classification_ceiling"] = classification_ceiling
    if allowed_group_ids is not None:
        kwargs["allowed_group_ids"] = allowed_group_ids
    if department_id is not None:
        kwargs["department_id"] = department_id
    async with file_db() as db:
        row = await governance.grant_support_delegation(
            db,
            actor=operator,
            tenant_id=tenant_id,
            principal_id=principal_id,
            scope=scope,
            expires_at=expires_at,
            **kwargs,
        )
        await db.commit()
        return row


# ---------------------------------------------------------------------------
# Operator status alone never grants content
# ---------------------------------------------------------------------------


async def test_operator_without_delegation_is_denied(world, file_db):
    actor = _operator(PlatformCapability.CONTENT_SUPPORT, principal_id=LEGACY_PRINCIPAL_ID)
    async with file_db() as db:
        with pytest.raises(SupportAccessError):
            await support_access.resolve_support_read(
                db, principal=actor, tenant_id=TENANT_A, scope="content"
            )
        await db.commit()
    async with file_db() as db:
        denied = (
            await db.execute(
                select(AuditRecord).where(
                    AuditRecord.action == "support.content.read",
                    AuditRecord.decision == "deny",
                )
            )
        ).scalars().all()
    assert denied, "a denied support read must be audited"


async def test_metadata_delegation_grants_no_content(world, file_db):
    await _grant_content(file_db, tenant_id=TENANT_A, scope="metadata")
    actor = _operator(principal_id=LEGACY_PRINCIPAL_ID)
    async with file_db() as db:
        with pytest.raises(SupportAccessError):
            await support_access.resolve_support_read(
                db, principal=actor, tenant_id=TENANT_A, scope="content"
            )


# ---------------------------------------------------------------------------
# Scope discipline
# ---------------------------------------------------------------------------


async def test_content_delegation_allows_content_but_not_metadata(world, file_db):
    await _grant_content(file_db, tenant_id=TENANT_A)
    actor = _operator(principal_id=LEGACY_PRINCIPAL_ID)
    async with file_db() as db:
        context = await support_access.resolve_support_read(
            db, principal=actor, tenant_id=TENANT_A, scope="content"
        )
        docs = await support_access.read_tenant_content(db, context=context)
        await db.commit()
    assert {d.id for d in docs} == {"doc-k-a"}
    async with file_db() as db:
        with pytest.raises(SupportAccessError):
            await support_access.resolve_support_read(
                db, principal=actor, tenant_id=TENANT_A, scope="metadata"
            )


async def test_metadata_delegation_allows_metadata_but_not_content(world, file_db):
    await _grant_content(file_db, tenant_id=TENANT_A, scope="metadata")
    actor = _operator(principal_id=LEGACY_PRINCIPAL_ID)
    async with file_db() as db:
        context = await support_access.resolve_support_read(
            db, principal=actor, tenant_id=TENANT_A, scope="metadata"
        )
        meta = await support_access.read_tenant_metadata(db, context=context)
        await db.commit()
    assert meta["id"] == TENANT_A
    async with file_db() as db:
        with pytest.raises(SupportAccessError):
            await support_access.resolve_support_read(
                db, principal=actor, tenant_id=TENANT_A, scope="content"
            )


# ---------------------------------------------------------------------------
# Time-bounded and revocable
# ---------------------------------------------------------------------------


async def test_expired_delegation_fails_closed(world, file_db):
    await _grant_content(
        file_db,
        tenant_id=TENANT_A,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=1),
    )
    async with file_db() as db:
        row = (await db.execute(select(SupportDelegation))).scalars().first()
        row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await db.commit()
    actor = _operator(principal_id=LEGACY_PRINCIPAL_ID)
    async with file_db() as db:
        with pytest.raises(SupportAccessError):
            await support_access.resolve_support_read(
                db, principal=actor, tenant_id=TENANT_A, scope="content"
            )


async def test_revoked_delegation_takes_effect_immediately(world, file_db):
    await _grant_content(file_db, tenant_id=TENANT_A)
    actor = _operator(principal_id=LEGACY_PRINCIPAL_ID)
    async with file_db() as db:
        await support_access.resolve_support_read(
            db, principal=actor, tenant_id=TENANT_A, scope="content"
        )
        await db.commit()
    operator = _operator(PlatformCapability.OPERATORS_ADMIN, PlatformCapability.CONTENT_SUPPORT)
    async with file_db() as db:
        assert await governance.revoke_support_delegation(
            db, actor=operator, tenant_id=TENANT_A, principal_id=LEGACY_PRINCIPAL_ID, scope="content"
        ) == 1
        await db.commit()
    async with file_db() as db:
        with pytest.raises(SupportAccessError):
            await support_access.resolve_support_read(
                db, principal=actor, tenant_id=TENANT_A, scope="content"
            )


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


async def test_tenant_a_delegation_cannot_reach_tenant_b(world, file_db):
    await _grant_content(file_db, tenant_id=TENANT_A)
    actor = _operator(principal_id=LEGACY_PRINCIPAL_ID)
    async with file_db() as db:
        with pytest.raises(SupportAccessError):
            await support_access.resolve_support_read(
                db, principal=actor, tenant_id=TENANT_B, scope="content"
            )


# ---------------------------------------------------------------------------
# Audit + no mutation
# ---------------------------------------------------------------------------


async def test_support_read_is_audited_and_does_not_mutate(world, file_db):
    await _grant_content(file_db, tenant_id=TENANT_A)
    actor = _operator(principal_id=LEGACY_PRINCIPAL_ID)
    async with file_db() as db:
        context = await support_access.resolve_support_read(
            db, principal=actor, tenant_id=TENANT_A, scope="content"
        )
        docs = await support_access.read_tenant_content(db, context=context)
        await db.commit()
    async with file_db() as db:
        audit = (
            await db.execute(
                select(AuditRecord).where(
                    AuditRecord.action == "support.content.read",
                    AuditRecord.decision == "allow",
                )
            )
        ).scalars().all()
        assert audit and audit[0].tenant_id == TENANT_A

        stored = (await db.execute(select(Document).where(Document.id == "doc-k-a"))).scalar_one()
    assert len(audit) >= 1
    # Read-only: the document is byte-for-byte unchanged.
    assert stored.original_name == "a-policy.txt"
    assert stored.lifecycle_state == "ready"
    assert stored.indexed is True
    assert stored.deleted_at is None


# ---------------------------------------------------------------------------
# End-to-end API path
# ---------------------------------------------------------------------------


async def test_api_support_content_read_end_to_end(client, world, file_db):
    await _grant_content(file_db, tenant_id=TENANT_A)
    # Delegated content read succeeds.
    ok = await client.get(f"/api/support/tenants/{TENANT_A}/content")
    assert ok.status_code == 200, ok.text
    assert [d["id"] for d in ok.json()] == ["doc-k-a"]
    # No delegation for tenant B -> denied.
    denied = await client.get(f"/api/support/tenants/{TENANT_B}/content")
    assert denied.status_code == 403


async def test_api_support_metadata_read_is_scoped(client, world, file_db):
    await _grant_content(file_db, tenant_id=TENANT_A, scope="metadata")
    ok = await client.get(f"/api/support/tenants/{TENANT_A}/metadata")
    assert ok.status_code == 200, ok.text
    assert ok.json()["id"] == TENANT_A
    # No content delegation -> content read denied.
    denied = await client.get(f"/api/support/tenants/{TENANT_A}/content")
    assert denied.status_code == 403


# ---------------------------------------------------------------------------
# Hardening: the delegation is BOUNDED; it never widens classification/source
# policy. A returned row must satisfy BOTH the delegation's bounds AND policy.
# ---------------------------------------------------------------------------


def _scoped_document(
    document_id: str,
    name: str,
    *,
    tenant_id: str = TENANT_A,
    classification: str = "internal",
    tenant_visible: bool = True,
    allowed_group_ids=None,
    department_id=None,
) -> Document:
    return Document(
        id=document_id,
        tenant_id=tenant_id,
        user_id=f"{tenant_id}:owner",
        original_name=name,
        stored_path=f"/tmp/{document_id}",
        size_bytes=10,
        status="ready",
        indexed=True,
        lifecycle_state="ready",
        classification=classification,
        tenant_visible=tenant_visible,
        allowed_group_ids_json=(
            json.dumps(allowed_group_ids) if allowed_group_ids else None
        ),
        department_id=department_id,
    )


@pytest.fixture
async def scoped_world(file_db):
    """Tenant A with a mix of governed documents plus one foreign-tenant doc.

    * ``doc-open``       internal, tenant-visible  -> policy reachable.
    * ``doc-tenant-hidden`` internal, not tenant-visible, no grant -> policy denies.
    * ``doc-conf``       confidential, granted to GROUP_HR -> policy needs the group.
    * ``doc-dept``       highly_restricted, owned by DEPT_HR -> needs the department.
    * ``doc-k-b``        a different tenant -> never reachable.
    """
    async with file_db() as db:
        db.add_all(
            [
                Tenant(id=TENANT_A, slug="k-a", name="A", status="active"),
                Tenant(id=TENANT_B, slug="k-b", name="B", status="active"),
            ]
        )
        await db.flush()
        db.add_all(
            [
                Department(id=DEPT_HR, tenant_id=TENANT_A, slug="hr", name="HR", status="active"),
                AccessGroup(id=GROUP_HR, tenant_id=TENANT_A, slug="hr", name="HR", status="active"),
            ]
        )
        await db.flush()
        db.add_all(
            [
                _scoped_document("doc-open", "open.md"),
                _scoped_document("doc-tenant-hidden", "hidden.md", tenant_visible=False),
                _scoped_document(
                    "doc-conf", "conf.md", classification="confidential", allowed_group_ids=[GROUP_HR]
                ),
                _scoped_document(
                    "doc-dept", "dept.md", classification="highly_restricted", department_id=DEPT_HR
                ),
                _scoped_document("doc-k-b", "b.md", tenant_id=TENANT_B),
            ]
        )
        await db.commit()
    return {"actor": LEGACY_PRINCIPAL_ID}


async def _read_content(file_db, *, scope="content"):
    actor = _operator(principal_id=LEGACY_PRINCIPAL_ID)
    async with file_db() as db:
        context = await support_access.resolve_support_read(
            db, principal=actor, tenant_id=TENANT_A, scope=scope
        )
        docs = await support_access.read_tenant_content(db, context=context)
        await db.commit()
    return docs


async def test_bounded_delegation_applies_both_policy_and_ceiling(scoped_world, file_db):
    """A content delegation bounded to internal+HR group+HR dept returns only the
    document that BOTH policy and the ceiling permit."""
    await _grant_content(
        file_db,
        tenant_id=TENANT_A,
        classification_ceiling="internal",
        allowed_group_ids=[GROUP_HR],
        department_id=DEPT_HR,
    )
    ids = {d.id for d in await _read_content(file_db)}
    assert ids == {"doc-open"}
    # The others are absent ENTIRELY, not merely redacted.
    assert "doc-conf" not in ids  # policy reachable via the group, but above the ceiling
    assert "doc-dept" not in ids  # policy reachable via the dept, but above the ceiling
    assert "doc-tenant-hidden" not in ids  # policy denies (not tenant-visible)
    assert "doc-k-b" not in ids  # foreign tenant


async def test_ceiling_blocks_group_authorized_document(scoped_world, file_db):
    """The classification ceiling is an independent gate over a policy-permitted row."""
    await _grant_content(
        file_db,
        tenant_id=TENANT_A,
        classification_ceiling="internal",
        allowed_group_ids=[GROUP_HR],
    )
    assert "doc-conf" not in {d.id for d in await _read_content(file_db)}

    # Raise the ceiling on the same (tenant, principal, scope): the group grant now
    # admits the confidential document.
    await _grant_content(
        file_db,
        tenant_id=TENANT_A,
        classification_ceiling="confidential",
        allowed_group_ids=[GROUP_HR],
    )
    assert {d.id for d in await _read_content(file_db)} == {"doc-open", "doc-conf"}


async def test_group_grant_is_required_for_group_scoped_document(scoped_world, file_db):
    """A high ceiling alone never substitutes for the source authorization."""
    await _grant_content(file_db, tenant_id=TENANT_A, classification_ceiling="highly_restricted")
    ids = {d.id for d in await _read_content(file_db)}
    assert "doc-conf" not in ids  # group-gated document still not reachable
    assert "doc-dept" not in ids  # department-gated document still not reachable
    assert ids == {"doc-open"}


async def test_department_scoped_delegation_reaches_only_its_department(scoped_world, file_db):
    await _grant_content(
        file_db,
        tenant_id=TENANT_A,
        classification_ceiling="highly_restricted",
        department_id=DEPT_HR,
    )
    ids = {d.id for d in await _read_content(file_db)}
    assert "doc-dept" in ids  # the department-confined grant permits the HR document
    assert "doc-conf" not in ids  # group-scoped document is not in the department scope
    assert "doc-tenant-hidden" not in ids
    assert "doc-k-b" not in ids


async def test_corrupt_ceiling_fails_closed(scoped_world, file_db):
    """An unknown ceiling is not a licence to read everything: deny all content.

    The DB check constraint refuses to store an unknown ceiling, so this drives
    the read-path defence-in-depth guard directly with a corrupt context.
    """
    from app.services.support_access import SupportReadContext

    actor = _operator(principal_id=LEGACY_PRINCIPAL_ID)
    context = SupportReadContext(
        principal=actor,
        tenant_id=TENANT_A,
        scope="content",
        delegation_id="forged",
        classification_ceiling="top_secret",
    )
    async with file_db() as db:
        assert await support_access.read_tenant_content(db, context=context) == []


async def test_grant_rejects_unknown_ceiling_and_foreign_scope(scoped_world, file_db):
    operator = _operator(PlatformCapability.OPERATORS_ADMIN, PlatformCapability.CONTENT_SUPPORT)
    for kwargs in (
        {"classification_ceiling": "top_secret"},
        {"allowed_group_ids": ["does-not-exist"]},
        {"department_id": "does-not-exist"},
    ):
        async with file_db() as db:
            with pytest.raises(ValueError):
                await governance.grant_support_delegation(
                    db,
                    actor=operator,
                    tenant_id=TENANT_A,
                    principal_id=LEGACY_PRINCIPAL_ID,
                    scope="content",
                    **kwargs,
                )


async def test_api_bounded_content_read_returns_only_permitted(client, scoped_world, file_db):
    await _grant_content(
        file_db,
        tenant_id=TENANT_A,
        classification_ceiling="internal",
        allowed_group_ids=[GROUP_HR],
        department_id=DEPT_HR,
    )
    ok = await client.get(f"/api/support/tenants/{TENANT_A}/content")
    assert ok.status_code == 200, ok.text
    assert {d["id"] for d in ok.json()} == {"doc-open"}


# ---------------------------------------------------------------------------
# Migration 0021 (head-agnostic)
# ---------------------------------------------------------------------------


def test_revision_0021_is_in_head_chain_and_adds_bounded_columns(tmp_path):
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine, inspect

    from app.migrations_runner import (
        _alembic_config,
        adopt_and_upgrade,
        current_revision,
        sync_url_for,
    )

    url = f"sqlite+aiosqlite:///{tmp_path / 'lane_k_0021.db'}"
    adopt_and_upgrade(url)
    head = current_revision(url)
    assert head is not None

    script = ScriptDirectory.from_config(_alembic_config(sync_url_for(url)))
    chain: set[str] = set()
    cursor: str | None = script.get_current_head()
    while cursor:
        chain.add(cursor)
        revision = script.get_revision(cursor)
        cursor = revision.down_revision if revision else None
    assert head in chain
    assert "0021_support_content_scope" in chain
    assert "0017_inf1b_admission" in chain

    engine = create_engine(sync_url_for(url), future=True)
    try:
        columns = {c["name"] for c in inspect(engine).get_columns("support_delegations")}
    finally:
        engine.dispose()
    assert {"classification_ceiling", "allowed_group_ids_json", "department_id"} <= columns

    # Idempotent second pass.
    adopt_and_upgrade(url)
    assert current_revision(url) == head
