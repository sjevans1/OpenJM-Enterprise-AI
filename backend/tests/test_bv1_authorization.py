"""BV1-A authorization and information-governance foundation acceptance.

Covers the Issue #45 BV1 boundary explicitly:

* RBAC (tenant roles) and platform capabilities are separate axes, and no
  metadata capability implies customer-content access;
* departments, groups and group membership are tenant-scoped and revocable;
* delegated data-steward grants are scoped and revocable;
* every mutation is audited in the correct tenant;
* no control-plane HTTP route is exposed in this increment.

Every test runs against a real (file-backed SQLite) database and the real
service layer. Nothing here reaches a model, a vector store or governed SQL.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.governance import StewardScopeType
from app.core.identity import AuthorizationError, Principal
from app.core.permissions import permissions_for_role
from app.core.platform import (
    PLATFORM_TIER_CAPABILITIES,
    PlatformCapability,
    capabilities_grant_content_access,
    platform_capabilities_for_tier,
)
from app.models import (
    AccessGroup,
    AuditRecord,
    DataSteward,
    Department,
    GroupMembership,
    PlatformOperator,
    PrincipalAccount,
    Tenant,
)
from app.services import access_governance as governance
from app.services import identity as identity_service

TENANT_A = "tnt-bv1-a"
TENANT_B = "tnt-bv1-b"
ADMIN_A = "bv1-admin-a"
MEMBER_A = "bv1-member-a"
MEMBER_B = "bv1-member-b"


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


@pytest.fixture
async def world(file_db):
    """Two tenants; tenant A has an owner and a viewer, tenant B has an owner."""
    async with file_db() as db:
        db.add_all(
            [
                Tenant(id=TENANT_A, slug="bv1-a", name="A", status="active"),
                Tenant(id=TENANT_B, slug="bv1-b", name="B", status="active"),
            ]
        )
        await db.flush()
        for subject, principal_id in (
            ("sub:admin-a", ADMIN_A),
            ("sub:member-a", MEMBER_A),
            ("sub:member-b", MEMBER_B),
        ):
            await identity_service.get_or_create_principal(
                db, subject=subject, principal_id=principal_id
            )
        await identity_service.add_membership(
            db, tenant_id=TENANT_A, principal_id=ADMIN_A, role="owner"
        )
        await identity_service.add_membership(
            db, tenant_id=TENANT_A, principal_id=MEMBER_A, role="viewer"
        )
        await identity_service.add_membership(
            db, tenant_id=TENANT_B, principal_id=MEMBER_B, role="owner"
        )
        await db.commit()
    return {
        "admin_a": _principal(ADMIN_A, TENANT_A, "owner"),
        "member_a": _principal(MEMBER_A, TENANT_A, "viewer"),
        "admin_b": _principal(MEMBER_B, TENANT_B, "owner"),
    }


# ---------------------------------------------------------------------------
# Platform capability is a separate axis from the tenant role
# ---------------------------------------------------------------------------


async def test_tenant_role_grants_no_platform_capability(world, file_db):
    async with file_db() as db:
        caps = await governance.platform_capabilities_for(db, principal_id=ADMIN_A)
    assert caps == frozenset()
    assert capabilities_grant_content_access(caps) is False
    # A tenant owner's own principal carries no platform authority either.
    assert world["admin_a"].platform_capabilities == frozenset()
    with pytest.raises(AuthorizationError) as exc:
        world["admin_a"].require_platform(PlatformCapability.METADATA_READ)
    assert exc.value.code == "missing_platform_capability"


async def test_metadata_capability_never_implies_content_access(world, file_db):
    async with file_db() as db:
        await governance.grant_platform_operator(
            db, principal_id=ADMIN_A, capabilities=[PlatformCapability.METADATA_READ]
        )
        await db.commit()

    async with file_db() as db:
        caps = await governance.platform_capabilities_for(db, principal_id=ADMIN_A)
    assert caps == frozenset({PlatformCapability.METADATA_READ})
    assert capabilities_grant_content_access(caps) is False

    actor = _principal(
        ADMIN_A,
        TENANT_A,
        "owner",
        platform_capabilities=frozenset(c.value for c in caps),
    )
    actor.require_platform(PlatformCapability.METADATA_READ)  # allowed
    with pytest.raises(AuthorizationError):
        actor.require_platform(PlatformCapability.CONTENT_SUPPORT)


async def test_content_support_is_explicit_and_does_not_imply_metadata(world, file_db):
    async with file_db() as db:
        await governance.grant_platform_operator(
            db, principal_id=ADMIN_A, capabilities=[PlatformCapability.CONTENT_SUPPORT]
        )
        await db.commit()
    async with file_db() as db:
        caps = await governance.platform_capabilities_for(db, principal_id=ADMIN_A)
    assert caps == frozenset({PlatformCapability.CONTENT_SUPPORT})
    assert capabilities_grant_content_access(caps) is True
    assert PlatformCapability.METADATA_READ not in caps


def test_platform_tiers_never_include_content_support():
    for capabilities in PLATFORM_TIER_CAPABILITIES.values():
        assert PlatformCapability.CONTENT_SUPPORT not in capabilities
    # Unknown or missing tier is deny, never a default.
    assert platform_capabilities_for_tier("not-a-tier") == frozenset()
    assert platform_capabilities_for_tier(None) == frozenset()


async def test_grant_rejects_unknown_platform_capability(world, file_db):
    async with file_db() as db:
        with pytest.raises(ValueError):
            await governance.grant_platform_operator(
                db, principal_id=ADMIN_A, capabilities=["platform:root"]
            )
        with pytest.raises(ValueError):
            await governance.grant_platform_operator(db, principal_id=ADMIN_A, capabilities=[])


async def test_db_rejects_unknown_platform_capability_value(world, file_db):
    async with file_db() as db:
        db.add(PlatformOperator(principal_id=ADMIN_A, capability="platform:root", status="active"))
        with pytest.raises(IntegrityError):
            await db.flush()
        await db.rollback()


async def test_expired_platform_grant_is_ineffective(world, file_db):
    past = datetime.now(timezone.utc) - timedelta(days=1)
    async with file_db() as db:
        await governance.grant_platform_operator(
            db,
            principal_id=ADMIN_A,
            capabilities=[PlatformCapability.TENANTS_ADMIN],
            expires_at=past,
        )
        await db.commit()
    async with file_db() as db:
        caps = await governance.platform_capabilities_for(db, principal_id=ADMIN_A)
    assert caps == frozenset()


async def test_platform_revocation_is_immediate(world, file_db):
    async with file_db() as db:
        await governance.grant_platform_operator(
            db, principal_id=ADMIN_A, capabilities=[PlatformCapability.METADATA_READ]
        )
        await db.commit()
    async with file_db() as db:
        assert await governance.revoke_platform_operator(
            db, principal_id=ADMIN_A, capability=PlatformCapability.METADATA_READ
        ) == 1
        await db.commit()
    async with file_db() as db:
        caps = await governance.platform_capabilities_for(db, principal_id=ADMIN_A)
    assert caps == frozenset()


def test_migration_capability_literal_matches_vocabulary():
    from pathlib import Path

    text = Path("migrations/versions/0008_bv1_authorization.py").read_text()
    for capability in PlatformCapability:
        assert capability.value in text, capability


# ---------------------------------------------------------------------------
# Departments and groups are tenant-scoped
# ---------------------------------------------------------------------------


async def test_group_membership_resolution_and_revocation(world, file_db):
    async with file_db() as db:
        department = await governance.create_department(
            db, principal=world["admin_a"], slug="hr", name="HR"
        )
        group = await governance.create_group(
            db,
            principal=world["admin_a"],
            slug="hr-leadership",
            name="HR Leadership",
            department_id=department.id,
        )
        await governance.add_group_member(
            db, principal=world["admin_a"], group_id=group.id, member_principal_id=MEMBER_A
        )
        await db.commit()
        department_id, group_id = department.id, group.id

    async with file_db() as db:
        access = await governance.load_principal_access(
            db, principal_id=MEMBER_A, tenant_id=TENANT_A
        )
    assert access.group_ids == frozenset({group_id})
    assert access.department_ids == frozenset({department_id})

    async with file_db() as db:
        changed = await governance.remove_group_member(
            db, principal=world["admin_a"], group_id=group_id, member_principal_id=MEMBER_A
        )
        await db.commit()
    assert changed == 1
    # Repeated removal is a no-op, not an error.
    async with file_db() as db:
        assert (
            await governance.remove_group_member(
                db,
                principal=world["admin_a"],
                group_id=group_id,
                member_principal_id=MEMBER_A,
            )
            == 0
        )

    async with file_db() as db:
        access = await governance.load_principal_access(
            db, principal_id=MEMBER_A, tenant_id=TENANT_A
        )
    assert access.group_ids == frozenset()
    assert access.department_ids == frozenset()


async def test_archived_group_confers_no_scope(world, file_db):
    async with file_db() as db:
        group = await governance.create_group(
            db, principal=world["admin_a"], slug="ops", name="Ops"
        )
        await governance.add_group_member(
            db, principal=world["admin_a"], group_id=group.id, member_principal_id=MEMBER_A
        )
        await db.commit()
        group_id = group.id

    async with file_db() as db:
        await governance.set_group_status(
            db, principal=world["admin_a"], group_id=group_id, status="archived"
        )
        await db.commit()

    async with file_db() as db:
        access = await governance.load_principal_access(
            db, principal_id=MEMBER_A, tenant_id=TENANT_A
        )
    assert group_id not in access.group_ids


async def test_cross_tenant_group_is_never_visible_or_mutable(world, file_db):
    async with file_db() as db:
        group_a = await governance.create_group(
            db, principal=world["admin_a"], slug="finance", name="Finance"
        )
        await db.commit()
        group_a_id = group_a.id

    async with file_db() as db:
        assert await governance.get_group(
            db, principal=world["admin_b"], group_id=group_a_id
        ) is None
        assert await governance.set_group_status(
            db, principal=world["admin_b"], group_id=group_a_id, status="archived"
        ) is None
        assert (
            await governance.add_group_member(
                db,
                principal=world["admin_b"],
                group_id=group_a_id,
                member_principal_id=MEMBER_B,
            )
            is None
        )
        assert (
            await governance.remove_group_member(
                db,
                principal=world["admin_b"],
                group_id=group_a_id,
                member_principal_id=MEMBER_A,
            )
            == 0
        )
        assert await governance.list_group_members(
            db, principal=world["admin_b"], group_id=group_a_id
        ) == []

    async with file_db() as db:
        b_groups = await governance.list_groups(db, principal=world["admin_b"])
    assert all(group.tenant_id == TENANT_B for group in b_groups)


async def test_cross_tenant_department_is_never_visible(world, file_db):
    async with file_db() as db:
        dept_a = await governance.create_department(
            db, principal=world["admin_a"], slug="hr", name="HR"
        )
        await db.commit()
        dept_a_id = dept_a.id
    async with file_db() as db:
        assert await governance.get_department(
            db, principal=world["admin_b"], department_id=dept_a_id
        ) is None
        assert (
            await governance.set_department_status(
                db, principal=world["admin_b"], department_id=dept_a_id, status="archived"
            )
            is None
        )
        assert await governance.list_departments(db, principal=world["admin_b"]) == []


async def test_same_slug_is_allowed_in_different_tenants(world, file_db):
    async with file_db() as db:
        await governance.create_department(db, principal=world["admin_a"], slug="hr", name="HR")
        await governance.create_department(db, principal=world["admin_b"], slug="hr", name="HR")
        await db.commit()
    async with file_db() as db:
        a_slugs = {d.slug for d in await governance.list_departments(db, principal=world["admin_a"])}
        b_slugs = {d.slug for d in await governance.list_departments(db, principal=world["admin_b"])}
    assert a_slugs == {"hr"} and b_slugs == {"hr"}


async def test_duplicate_slug_in_same_tenant_is_rejected(world, file_db):
    async with file_db() as db:
        await governance.create_group(db, principal=world["admin_a"], slug="ops", name="Ops")
        with pytest.raises(ValueError):
            await governance.create_group(db, principal=world["admin_a"], slug="ops", name="Ops")


async def test_group_department_must_belong_to_tenant(world, file_db):
    async with file_db() as db:
        dept_a = await governance.create_department(
            db, principal=world["admin_a"], slug="hr", name="HR"
        )
        await db.commit()
        dept_a_id = dept_a.id
    async with file_db() as db:
        with pytest.raises(ValueError):
            await governance.create_group(
                db,
                principal=world["admin_b"],
                slug="ops",
                name="Ops",
                department_id=dept_a_id,
            )


async def test_foreign_principal_cannot_be_added_to_a_group(world, file_db):
    async with file_db() as db:
        group = await governance.create_group(
            db, principal=world["admin_a"], slug="ops", name="Ops"
        )
        await db.commit()
        group_id = group.id
    async with file_db() as db:
        with pytest.raises(ValueError):
            await governance.add_group_member(
                db,
                principal=world["admin_a"],
                group_id=group_id,
                member_principal_id=MEMBER_B,
            )


# ---------------------------------------------------------------------------
# Delegated data stewardship
# ---------------------------------------------------------------------------


async def test_steward_scope_resolution_and_revocation(world, file_db):
    async with file_db() as db:
        department = await governance.create_department(
            db, principal=world["admin_a"], slug="finance", name="Finance"
        )
        await db.commit()
        department_id = department.id

    async with file_db() as db:
        await governance.grant_steward(
            db,
            principal=world["admin_a"],
            steward_principal_id=MEMBER_A,
            scope_type=StewardScopeType.DEPARTMENT.value,
            scope_id=department_id,
        )
        await db.commit()

    async with file_db() as db:
        access = await governance.load_principal_access(
            db, principal_id=MEMBER_A, tenant_id=TENANT_A
        )
    assert (StewardScopeType.DEPARTMENT.value, department_id) in access.steward_scopes

    async with file_db() as db:
        assert (
            await governance.revoke_steward(
                db,
                principal=world["admin_a"],
                steward_principal_id=MEMBER_A,
                scope_type=StewardScopeType.DEPARTMENT.value,
                scope_id=department_id,
            )
            == 1
        )
        await db.commit()
    async with file_db() as db:
        access = await governance.load_principal_access(
            db, principal_id=MEMBER_A, tenant_id=TENANT_A
        )
    assert access.steward_scopes == frozenset()


async def test_steward_scope_must_be_valid_and_in_tenant(world, file_db):
    async with file_db() as db:
        dept_a = await governance.create_department(
            db, principal=world["admin_a"], slug="hr", name="HR"
        )
        await db.commit()
        dept_a_id = dept_a.id
    async with file_db() as db:
        # Unknown scope type.
        with pytest.raises(ValueError):
            await governance.grant_steward(
                db,
                principal=world["admin_a"],
                steward_principal_id=MEMBER_A,
                scope_type="galaxy",
                scope_id="x",
            )
        # Tenant B actor referencing tenant A's department.
        with pytest.raises(ValueError):
            await governance.grant_steward(
                db,
                principal=world["admin_b"],
                steward_principal_id=MEMBER_B,
                scope_type=StewardScopeType.DEPARTMENT.value,
                scope_id=dept_a_id,
            )
        # Steward must be an active member of the actor's tenant.
        with pytest.raises(ValueError):
            await governance.grant_steward(
                db,
                principal=world["admin_a"],
                steward_principal_id=MEMBER_B,
                scope_type=StewardScopeType.TENANT.value,
                scope_id=TENANT_A,
            )


# ---------------------------------------------------------------------------
# RBAC gate on governance mutations
# ---------------------------------------------------------------------------


async def test_governance_mutations_require_tenant_admin(world, file_db):
    viewer = world["member_a"]
    async with file_db() as db:
        with pytest.raises(AuthorizationError):
            await governance.create_department(db, principal=viewer, slug="x", name="X")
        with pytest.raises(AuthorizationError):
            await governance.create_group(db, principal=viewer, slug="x", name="X")
        with pytest.raises(AuthorizationError):
            await governance.grant_steward(
                db,
                principal=viewer,
                steward_principal_id=MEMBER_A,
                scope_type=StewardScopeType.TENANT.value,
                scope_id=TENANT_A,
            )


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


async def test_governance_mutations_are_audited_per_tenant(world, file_db):
    async with file_db() as db:
        department = await governance.create_department(
            db, principal=world["admin_a"], slug="hr", name="HR"
        )
        group = await governance.create_group(
            db,
            principal=world["admin_a"],
            slug="hr-team",
            name="HR Team",
            department_id=department.id,
        )
        await governance.add_group_member(
            db, principal=world["admin_a"], group_id=group.id, member_principal_id=MEMBER_A
        )
        await governance.grant_steward(
            db,
            principal=world["admin_a"],
            steward_principal_id=MEMBER_A,
            scope_type=StewardScopeType.TENANT.value,
            scope_id=TENANT_A,
        )
        await db.commit()

    async with file_db() as db:
        a_actions = {
            row.action
            for row in (
                await db.execute(select(AuditRecord).where(AuditRecord.tenant_id == TENANT_A))
            )
            .scalars()
            .all()
        }
        b_actions = {
            row.action
            for row in (
                await db.execute(select(AuditRecord).where(AuditRecord.tenant_id == TENANT_B))
            )
            .scalars()
            .all()
        }
    assert {
        "governance.department.create",
        "governance.group.create",
        "governance.group.member.add",
        "governance.steward.grant",
    } <= a_actions
    assert not (a_actions & b_actions), "tenant A audit must not appear in tenant B"


async def test_platform_grant_is_audited(world, file_db):
    async with file_db() as db:
        await governance.grant_platform_operator(
            db,
            principal_id=ADMIN_A,
            capabilities=[PlatformCapability.METADATA_READ],
            granted_by="operator-1",
        )
        await db.commit()
    async with file_db() as db:
        rows = (
            await db.execute(
                select(AuditRecord).where(AuditRecord.action == "platform.operator.grant")
            )
        ).scalars().all()
    assert len(rows) == 1
    assert ADMIN_A in (rows[0].resource_id or "")


# ---------------------------------------------------------------------------
# Per-request identity resolution wires the new context
# ---------------------------------------------------------------------------


async def test_identity_resolution_populates_group_and_platform_context(world, file_db):
    async with file_db() as db:
        department = await governance.create_department(
            db, principal=world["admin_a"], slug="hr", name="HR"
        )
        group = await governance.create_group(
            db,
            principal=world["admin_a"],
            slug="hr-team",
            name="HR Team",
            department_id=department.id,
        )
        await governance.add_group_member(
            db, principal=world["admin_a"], group_id=group.id, member_principal_id=MEMBER_A
        )
        await governance.grant_platform_operator(
            db, principal_id=MEMBER_A, capabilities=[PlatformCapability.METADATA_READ]
        )
        await db.commit()
        department_id, group_id = department.id, group.id

    async with file_db() as db:
        account = await db.get(PrincipalAccount, MEMBER_A)
        principal = await identity_service.principal_for_account(
            db, account, tenant_id=TENANT_A, auth_method="oidc"
        )

    assert principal.group_ids == frozenset({group_id})
    assert principal.department_ids == frozenset({department_id})
    assert principal.has_platform(PlatformCapability.METADATA_READ)
    assert not principal.has_platform(PlatformCapability.CONTENT_SUPPORT)
    assert capabilities_grant_content_access(principal.platform_capability_strings()) is False


async def test_principal_access_context_defaults_empty(world, file_db):
    """An ungoverned principal resolves with empty scopes, and never fails."""
    async with file_db() as db:
        account = await db.get(PrincipalAccount, ADMIN_A)
        principal = await identity_service.principal_for_account(
            db, account, tenant_id=TENANT_A, auth_method="oidc"
        )
    assert principal.group_ids == frozenset()
    assert principal.department_ids == frozenset()
    assert principal.steward_scopes == frozenset()
    assert principal.platform_capabilities == frozenset()


# ---------------------------------------------------------------------------
# No control-plane HTTP surface in this increment (fail closed by absence)
# ---------------------------------------------------------------------------


async def test_control_plane_routes_are_not_exposed(client):
    for path in (
        "/api/admin/departments",
        "/api/admin/groups",
        "/api/admin/platform/operators",
    ):
        assert (await client.get(path)).status_code == 404
        assert (await client.post(path, json={})).status_code == 404


# ---------------------------------------------------------------------------
# Migration 0008
# ---------------------------------------------------------------------------


def test_revision_0008_adds_tables_idempotently_and_downgrades(tmp_path):
    from alembic import command
    from sqlalchemy import create_engine, inspect

    from app.migrations_runner import (
        _alembic_config,
        adopt_and_upgrade,
        current_revision,
        sync_url_for,
    )

    url = f"sqlite+aiosqlite:///{tmp_path / 'bv1.db'}"
    adopt_and_upgrade(url)
    assert current_revision(url) == "0008_bv1_authorization"

    engine = create_engine(sync_url_for(url), future=True)
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    assert {
        "departments",
        "access_groups",
        "group_memberships",
        "platform_operators",
        "data_stewards",
    } <= tables

    # Second pass is a no-op at the same head.
    adopt_and_upgrade(url)
    assert current_revision(url) == "0008_bv1_authorization"

    # Downgrade removes exactly the new tables.
    command.downgrade(_alembic_config(sync_url_for(url)), "0007_vs7_connectors")
    assert current_revision(url) == "0007_vs7_connectors"
    engine = create_engine(sync_url_for(url), future=True)
    try:
        after = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    assert not (
        {"departments", "access_groups", "group_memberships", "platform_operators", "data_stewards"}
        & after
    )

    # Re-upgrade reaches head again and preserves earlier tables.
    adopt_and_upgrade(url)
    assert current_revision(url) == "0008_bv1_authorization"


def test_revision_0008_upgrades_populated_0007_database(tmp_path):
    from alembic import command
    from sqlalchemy import create_engine, inspect, text

    from app.migrations_runner import (
        _alembic_config,
        adopt_and_upgrade,
        current_revision,
        sync_url_for,
    )

    url = f"sqlite+aiosqlite:///{tmp_path / 'populated.db'}"
    command.upgrade(_alembic_config(sync_url_for(url)), "0007_vs7_connectors")

    sync_url = sync_url_for(url)
    engine = create_engine(sync_url, future=True)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO conversations (id, user_id, title, created_at, updated_at) "
                    "VALUES ('c-prev','local-admin','Previous', "
                    "'2026-01-01 00:00:00.000000','2026-01-01 00:00:00.000000')"
                )
            )
    finally:
        engine.dispose()

    adopt_and_upgrade(url)
    assert current_revision(url) == "0008_bv1_authorization"

    engine = create_engine(sync_url, future=True)
    try:
        with engine.connect() as connection:
            rows = connection.execute(text("SELECT id FROM conversations")).fetchall()
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    assert ("c-prev",) in rows, "pre-existing rows must survive the additive revision"
    assert "departments" in tables and "data_stewards" in tables
