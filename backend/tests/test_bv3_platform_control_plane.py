"""BV3-A platform control plane + secure bootstrap acceptance.

Covers the reviewed boundaries: platform authority is separate from tenant roles,
metadata authority never implies customer content, tenant create/suspend/reactivate
and initial owner provisioning work only through platform authority and are
audited, bootstrap cannot be replayed and never grants content support, and
grant/revoke applies on the next authorized request.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.identity import AuthorizationError, Principal
from app.core.permissions import permissions_for_role
from app.core.platform import PlatformCapability, capabilities_grant_content_access
from app.core.tenancy import LEGACY_PRINCIPAL_ID
from app.main import app
from app.models import AuditRecord, SupportDelegation, Tenant
from app.services import access_governance as governance
from app.services import identity as identity_service
from app.services import platform_admin
from app.services import platform_bootstrap
from app.services.platform_bootstrap import BootstrapError


def _operator(*capabilities, principal_id="op-1", tenant_id="tnt-op") -> Principal:
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


async def _grant_local_operator(file_db, *capabilities) -> None:
    """Give the dev-authenticated principal real platform grants.

    The dev identity resolves platform capabilities from the database on every
    request, so this exercises the real route guards end to end.
    """
    async with file_db() as db:
        await governance.grant_platform_operator(
            db, principal_id=LEGACY_PRINCIPAL_ID, capabilities=list(capabilities)
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Authority separation
# ---------------------------------------------------------------------------


async def test_tenant_owner_has_zero_platform_rights(client, file_db):
    # Dev auth resolves the legacy principal as tenant owner, with no platform grant.
    for path in ("/api/platform/tenants", "/api/platform/operators", "/api/platform/status"):
        response = await client.get(path)
        assert response.status_code == 403, (path, response.status_code, response.text)


async def test_metadata_operator_cannot_read_customer_content(client, file_db):
    await _grant_local_operator(file_db, PlatformCapability.METADATA_READ)

    # Metadata reads are allowed.
    assert (await client.get("/api/platform/tenants")).status_code == 200

    # No platform route exposes customer content, and metadata authority adds
    # nothing to a content endpoint.
    paths = {getattr(route, "path", "") for route in app.routes}
    assert not any(
        path.startswith("/api/platform/") and any(
            token in path for token in ("documents", "sources", "reports", "chat", "evidence")
        )
        for path in paths
    )
    assert not capabilities_grant_content_access(
        frozenset({PlatformCapability.METADATA_READ.value})
    )


async def test_content_support_capability_is_separate():
    assert capabilities_grant_content_access(
        frozenset({PlatformCapability.CONTENT_SUPPORT.value})
    ) is True
    for capability in (
        PlatformCapability.METADATA_READ,
        PlatformCapability.TENANTS_ADMIN,
        PlatformCapability.OPERATORS_ADMIN,
    ):
        assert capabilities_grant_content_access(frozenset({capability.value})) is False


# ---------------------------------------------------------------------------
# Tenant administration (privileged mutations)
# ---------------------------------------------------------------------------


async def test_tenant_create_suspend_reactivate_via_platform_authority(client, file_db):
    await _grant_local_operator(
        file_db, PlatformCapability.METADATA_READ, PlatformCapability.TENANTS_ADMIN
    )

    created = await client.post(
        "/api/platform/tenants", json={"slug": "acme", "name": "Acme Corp"}
    )
    assert created.status_code == 200, created.text
    tenant_id = created.json()["id"]
    assert created.json()["status"] == "active"

    # Idempotent by slug: a retry returns the same tenant.
    again = await client.post(
        "/api/platform/tenants", json={"slug": "acme", "name": "Acme Corp"}
    )
    assert again.status_code == 200
    assert again.json()["id"] == tenant_id

    suspended = await client.post(f"/api/platform/tenants/{tenant_id}/suspend")
    assert suspended.status_code == 200 and suspended.json()["status"] == "suspended"

    reactivated = await client.post(f"/api/platform/tenants/{tenant_id}/reactivate")
    assert reactivated.status_code == 200 and reactivated.json()["status"] == "active"

    async with file_db() as db:
        actions = {
            row.action
            for row in (await db.execute(select(AuditRecord))).scalars().all()
        }
    assert {
        "platform.tenant.ensure",
        "platform.tenant.status",
    } <= actions


async def test_tenant_mutation_requires_tenants_admin(client, file_db):
    await _grant_local_operator(file_db, PlatformCapability.METADATA_READ)
    response = await client.post(
        "/api/platform/tenants", json={"slug": "nope", "name": "Nope"}
    )
    assert response.status_code == 403


async def test_initial_owner_provisioning_is_audited(client, file_db):
    await _grant_local_operator(
        file_db, PlatformCapability.METADATA_READ, PlatformCapability.TENANTS_ADMIN
    )
    created = await client.post(
        "/api/platform/tenants", json={"slug": "beta", "name": "Beta"}
    )
    tenant_id = created.json()["id"]

    provisioned = await client.post(
        f"/api/platform/tenants/{tenant_id}/memberships",
        json={"subject": "oidc|beta-owner", "role": "owner", "email": "owner@beta.test"},
    )
    assert provisioned.status_code == 200, provisioned.text
    body = provisioned.json()
    assert body["tenant_id"] == tenant_id and body["role"] == "owner"

    async with file_db() as db:
        audits = (
            await db.execute(
                select(AuditRecord).where(
                    AuditRecord.action == "platform.tenant.provision_membership"
                )
            )
        ).scalars().all()
    assert len(audits) == 1

    # Provisioning is idempotent per subject.
    repeat = await client.post(
        f"/api/platform/tenants/{tenant_id}/memberships",
        json={"subject": "oidc|beta-owner", "role": "owner"},
    )
    assert repeat.status_code == 200
    assert repeat.json()["principal_id"] == body["principal_id"]


async def test_provisioning_rejects_tenant_roles(client, file_db):
    await _grant_local_operator(
        file_db, PlatformCapability.METADATA_READ, PlatformCapability.TENANTS_ADMIN
    )
    created = await client.post(
        "/api/platform/tenants", json={"slug": "gamma", "name": "Gamma"}
    )
    tenant_id = created.json()["id"]
    response = await client.post(
        f"/api/platform/tenants/{tenant_id}/memberships",
        json={"subject": "oidc|viewer", "role": "viewer"},
    )
    # The schema only admits owner/admin.
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Operator grant / revoke applies on the next request
# ---------------------------------------------------------------------------


async def test_operator_grant_and_revoke_apply_on_next_request(client, file_db):
    assert (await client.get("/api/platform/operators")).status_code == 403

    await _grant_local_operator(file_db, PlatformCapability.OPERATORS_ADMIN)
    assert (await client.get("/api/platform/operators")).status_code == 200

    async with file_db() as db:
        await governance.revoke_platform_operator(
            db, principal_id=LEGACY_PRINCIPAL_ID, capability=PlatformCapability.OPERATORS_ADMIN
        )
        await db.commit()

    assert (await client.get("/api/platform/operators")).status_code == 403


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------


async def test_bootstrap_establishes_first_operator_and_cannot_be_replayed(file_db):
    async with file_db() as db:
        result = await platform_bootstrap.bootstrap_first_operator(
            db, subject="oidc|first-operator"
        )
    assert result["capabilities"]
    assert PlatformCapability.OPERATORS_ADMIN.value in result["capabilities"]
    assert PlatformCapability.CONTENT_SUPPORT.value not in result["capabilities"]

    async with file_db() as db:
        audits = (
            await db.execute(
                select(AuditRecord).where(AuditRecord.action == "platform.bootstrap")
            )
        ).scalars().all()
    assert len(audits) == 1

    # Replay is refused: the trust root already exists.
    async with file_db() as db:
        with pytest.raises(BootstrapError):
            await platform_bootstrap.bootstrap_first_operator(
                db, subject="oidc|attacker"
            )


async def test_bootstrap_never_grants_content_support(file_db):
    async with file_db() as db:
        with pytest.raises(BootstrapError):
            await platform_bootstrap.bootstrap_first_operator(
                db,
                subject="oidc|sneaky",
                capabilities=[PlatformCapability.CONTENT_SUPPORT.value],
            )


async def test_bootstrap_has_no_http_endpoint(client):
    paths = {getattr(route, "path", "") for route in app.routes}
    assert not any("bootstrap" in path for path in paths)


# ---------------------------------------------------------------------------
# Support delegation foundation
# ---------------------------------------------------------------------------


async def test_support_delegation_is_scoped_resolved_and_revocable(file_db):
    operator = _operator(PlatformCapability.OPERATORS_ADMIN, PlatformCapability.CONTENT_SUPPORT)
    async with file_db() as db:
        tenant = Tenant(id="tnt-sup", slug="sup", name="Sup", status="active")
        db.add(tenant)
        await db.flush()
        await identity_service.get_or_create_principal(
            db, subject="oidc|support-agent", principal_id="agent-1"
        )
        await db.commit()

    async with file_db() as db:
        row = await governance.grant_support_delegation(
            db,
            actor=operator,
            tenant_id="tnt-sup",
            principal_id="agent-1",
            scope="content",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
        )
        await db.commit()
    assert row.status == "active"

    async with file_db() as db:
        access = await governance.load_principal_access(
            db, principal_id="agent-1", tenant_id="tnt-sup"
        )
    assert access.support_scopes == frozenset({"content"})

    # Revocation takes effect on the next resolution.
    async with file_db() as db:
        assert (
            await governance.revoke_support_delegation(
                db, actor=operator, tenant_id="tnt-sup", principal_id="agent-1", scope="content"
            )
            == 1
        )
        await db.commit()
    async with file_db() as db:
        access = await governance.load_principal_access(
            db, principal_id="agent-1", tenant_id="tnt-sup"
        )
    assert access.support_scopes == frozenset()


async def test_expired_support_delegation_is_not_resolved(file_db):
    operator = _operator(PlatformCapability.OPERATORS_ADMIN)
    async with file_db() as db:
        db.add(Tenant(id="tnt-sup2", slug="sup2", name="Sup2", status="active"))
        await db.flush()
        await identity_service.get_or_create_principal(
            db, subject="oidc|agent-2", principal_id="agent-2"
        )
        await db.commit()
    async with file_db() as db:
        await governance.grant_support_delegation(
            db,
            actor=operator,
            tenant_id="tnt-sup2",
            principal_id="agent-2",
            scope="metadata",
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=1),
        )
        await db.commit()
    async with file_db() as db:
        row = (
            await db.execute(select(SupportDelegation))
        ).scalars().first()
        row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await db.commit()
    async with file_db() as db:
        access = await governance.load_principal_access(
            db, principal_id="agent-2", tenant_id="tnt-sup2"
        )
    assert access.support_scopes == frozenset()


async def test_content_support_cannot_be_delegated_without_holding_it(file_db):
    operator = _operator(PlatformCapability.OPERATORS_ADMIN)  # no CONTENT_SUPPORT
    async with file_db() as db:
        db.add(Tenant(id="tnt-sup3", slug="sup3", name="Sup3", status="active"))
        await db.flush()
        await identity_service.get_or_create_principal(
            db, subject="oidc|agent-3", principal_id="agent-3"
        )
        await db.commit()
    async with file_db() as db:
        with pytest.raises(AuthorizationError):
            await governance.grant_support_delegation(
                db,
                actor=operator,
                tenant_id="tnt-sup3",
                principal_id="agent-3",
                scope="content",
            )


# ---------------------------------------------------------------------------
# Cross-tenant and migration
# ---------------------------------------------------------------------------


async def test_platform_metadata_lists_all_tenants_without_content(file_db):
    operator = _operator(PlatformCapability.METADATA_READ)
    async with file_db() as db:
        db.add_all(
            [
                Tenant(id="tnt-x", slug="x", name="X", status="active"),
                Tenant(id="tnt-y", slug="y", name="Y", status="active"),
            ]
        )
        await db.commit()
    async with file_db() as db:
        tenants = await platform_admin.list_tenants(db, actor=operator)
        one = await platform_admin.get_tenant(db, actor=operator, tenant_id="tnt-x")
    assert {t.id for t in tenants} >= {"tnt-x", "tnt-y"}
    assert one is not None and one.id == "tnt-x"
    # The control-plane view carries no content fields at all.
    assert not hasattr(one, "documents")


def test_migration_0012_adds_support_delegations(tmp_path):
    from sqlalchemy import create_engine, inspect

    from app.migrations_runner import adopt_and_upgrade, current_revision, sync_url_for

    url = f"sqlite+aiosqlite:///{tmp_path / 'bv3a.db'}"
    adopt_and_upgrade(url)
    head = current_revision(url)
    assert head == "0012_support_delegations"
    engine = create_engine(sync_url_for(url), future=True)
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    assert "support_delegations" in tables
