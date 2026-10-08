"""BV3-B client administration acceptance.

Covers the reviewed boundaries: the client-administration plane is governed by
the tenant permission model (never a platform capability), a data steward can
manage their governed sources without becoming a tenant admin, viewer/editor
cannot mutate tenant governance, revocation takes effect on the next request,
and there is no cross-tenant enumeration.
"""

import pytest
from sqlalchemy import select

from app.core.identity import Principal
from app.core.permissions import Permission, permissions_for_role
from app.core.platform import PlatformCapability
from app.models import AuditRecord, PrincipalAccount, Tenant, TenantMembership
from app.services import access_governance as governance
from app.services import client_admin
from app.services import identity as identity_service


async def _resolve(file_db, *, principal_id: str, tenant_id: str):
    """Re-resolve a principal exactly the way a real request does.

    Returns None when the membership is no longer usable, which is how a
    revocation becomes visible on the very next request.
    """
    async with file_db() as db:
        account = await db.get(PrincipalAccount, principal_id)
        if account is None:
            return None
        try:
            return await identity_service.principal_for_account(
                db, account, tenant_id=tenant_id, auth_method="oidc"
            )
        except Exception:  # noqa: BLE001 - any denial is "not usable"
            return None


async def _principal_for(file_db, *, subject: str, role: str, tenant_id: str) -> Principal:
    """Create/ensure a membership and resolve the real Principal for it."""
    async with file_db() as db:
        account = await identity_service.get_or_create_principal(db, subject=subject)
        await identity_service.add_membership(
            db, tenant_id=tenant_id, principal_id=account.id, role=role
        )
        await db.commit()
        principal_id = account.id
    resolved = await _resolve(file_db, principal_id=principal_id, tenant_id=tenant_id)
    assert resolved is not None
    return resolved


# ---------------------------------------------------------------------------
# Authority: tenant permission model, never platform capability
# ---------------------------------------------------------------------------


async def test_platform_capability_does_not_grant_client_admin(file_db):
    """A platform operator without a tenant membership admin role is refused."""
    operator = Principal(
        principal_id="op-9",
        tenant_id="tnt-1",
        subject="sub:op-9",
        role="viewer",
        membership_id="m",
        auth_method="oidc",
        permissions=permissions_for_role("viewer"),
        platform_capabilities=frozenset(
            {PlatformCapability.TENANTS_ADMIN.value, PlatformCapability.METADATA_READ.value}
        ),
    )
    async with file_db() as db:
        with pytest.raises(Exception) as excinfo:
            await client_admin.list_members(db, principal=operator)
    assert "tenant administration" in str(excinfo.value).lower()
    assert not operator.has(Permission.TENANT_ADMIN)


async def test_viewer_and_editor_cannot_mutate_tenant_governance(file_db):
    for role in ("viewer", "editor"):
        principal = await _principal_for(
            file_db, subject=f"oidc|{role}-1", role=role, tenant_id="tnt-local"
        )
        assert not principal.has(Permission.TENANT_ADMIN)
        async with file_db() as db:
            with pytest.raises(Exception):
                await client_admin.list_members(db, principal=principal)


async def test_owner_can_list_members(file_db):
    owner = await _principal_for(
        file_db, subject="oidc|owner-1", role="owner", tenant_id="tnt-local"
    )
    assert owner.has(Permission.TENANT_ADMIN)
    async with file_db() as db:
        members = await client_admin.list_members(db, principal=owner)
    assert any(m.principal_id == owner.principal_id for m in members)


# ---------------------------------------------------------------------------
# Membership lifecycle
# ---------------------------------------------------------------------------


async def test_provision_role_change_and_revoke(file_db):
    owner = await _principal_for(
        file_db, subject="oidc|owner-life", role="owner", tenant_id="tnt-local"
    )
    async with file_db() as db:
        created = await client_admin.provision_member(
            db,
            principal=owner,
            subject="oidc|hr-admin",
            role="admin",
            email="hr@acme.test",
        )
        await db.commit()
    assert created.role == "admin" and created.status == "active"

    # Idempotent per subject: re-provisioning reuses the same principal.
    async with file_db() as db:
        again = await client_admin.provision_member(
            db, principal=owner, subject="oidc|hr-admin", role="editor"
        )
        await db.commit()
    assert again.principal_id == created.principal_id

    async with file_db() as db:
        promoted = await client_admin.change_role(
            db, principal=owner, member_principal_id=created.principal_id, role="admin"
        )
        await db.commit()
    assert promoted is not None and promoted.role == "admin"

    async with file_db() as db:
        revoked = await client_admin.set_membership_status(
            db, principal=owner, member_principal_id=created.principal_id, status="revoked"
        )
        await db.commit()
    assert revoked is not None and revoked.status == "revoked"

    async with file_db() as db:
        actions = {
            row.action for row in (await db.execute(select(AuditRecord))).scalars().all()
        }
    assert {
        "client_admin.member.provision",
        "client_admin.member.role",
        "client_admin.member.status",
    } <= actions


async def test_revocation_takes_effect_on_next_request(file_db):
    owner = await _principal_for(
        file_db, subject="oidc|owner-eff", role="owner", tenant_id="tnt-local"
    )
    member = await _principal_for(
        file_db, subject="oidc|temp-member", role="admin", tenant_id="tnt-local"
    )
    assert member.has(Permission.TENANT_ADMIN)

    async with file_db() as db:
        await client_admin.set_membership_status(
            db, principal=owner, member_principal_id=member.principal_id, status="revoked"
        )
        await db.commit()

    # Re-resolving from the database (as a real request does) immediately fails.
    re_resolved = await _resolve(file_db, principal_id=member.principal_id, tenant_id="tnt-local")
    assert re_resolved is None


async def test_self_mutation_and_owner_escalation_are_refused(file_db):
    owner = await _principal_for(
        file_db, subject="oidc|owner-guard", role="owner", tenant_id="tnt-local"
    )
    admin = await _principal_for(
        file_db, subject="oidc|admin-guard", role="admin", tenant_id="tnt-local"
    )
    async with file_db() as db:
        with pytest.raises(Exception):
            await client_admin.change_role(
                db, principal=owner, member_principal_id=owner.principal_id, role="editor"
            )
    async with file_db() as db:
        with pytest.raises(Exception) as excinfo:
            await client_admin.provision_member(
                db, principal=admin, subject="oidc|sneaky-owner", role="owner"
            )
    assert "owner" in str(excinfo.value).lower()


async def test_last_owner_cannot_be_demoted_or_revoked(file_db):
    # A fresh tenant with exactly one owner isolates the last-owner guard.
    async with file_db() as db:
        db.add(Tenant(id="tnt-solo", slug="solo", name="Solo", status="active"))
        await db.commit()

    owner = await _principal_for(
        file_db, subject="oidc|solo-owner", role="owner", tenant_id="tnt-solo"
    )
    admin = await _principal_for(
        file_db, subject="oidc|solo-admin", role="admin", tenant_id="tnt-solo"
    )

    async with file_db() as db:
        with pytest.raises(ValueError):
            await client_admin.change_role(
                db, principal=admin, member_principal_id=owner.principal_id, role="editor"
            )
    async with file_db() as db:
        with pytest.raises(ValueError):
            await client_admin.set_membership_status(
                db, principal=admin, member_principal_id=owner.principal_id, status="revoked"
            )

    # With a second active owner the same demotion is allowed.
    async with file_db() as db:
        second = await identity_service.get_or_create_principal(db, subject="oidc|solo-owner-2")
        await identity_service.add_membership(
            db, tenant_id="tnt-solo", principal_id=second.id, role="owner"
        )
        await db.commit()
    async with file_db() as db:
        demoted = await client_admin.change_role(
            db, principal=admin, member_principal_id=owner.principal_id, role="editor"
        )
        await db.commit()
    assert demoted is not None and demoted.role == "editor"


# ---------------------------------------------------------------------------
# Departments, groups, stewards
# ---------------------------------------------------------------------------


async def test_hr_admin_creates_department_group_and_assigns_members(file_db):
    owner = await _principal_for(
        file_db, subject="oidc|hr-owner", role="owner", tenant_id="tnt-local"
    )
    member = await _principal_for(
        file_db, subject="oidc|hr-staff", role="editor", tenant_id="tnt-local"
    )
    async with file_db() as db:
        department = await governance.create_department(db, principal=owner, slug="hr", name="Human Resources")
        await db.commit()
        department_id = department.id

    async with file_db() as db:
        group = await governance.create_group(
            db, principal=owner, slug="hr-team", name="HR Team", department_id=department_id
        )
        await db.commit()
        group_id = group.id

    async with file_db() as db:
        added = await governance.add_group_member(
            db, principal=owner, group_id=group_id, member_principal_id=member.principal_id
        )
        await db.commit()
    assert added is not None

    # The member now resolves into the group on their next request.
    resolved = await _resolve(file_db, principal_id=member.principal_id, tenant_id="tnt-local")
    assert group_id in resolved.group_ids


async def test_group_revocation_takes_effect_on_next_request(file_db):
    owner = await _principal_for(
        file_db, subject="oidc|grp-owner", role="owner", tenant_id="tnt-local"
    )
    member = await _principal_for(
        file_db, subject="oidc|grp-member", role="editor", tenant_id="tnt-local"
    )
    async with file_db() as db:
        group = await governance.create_group(
            db, principal=owner, slug="grp", name="Group"
        )
        await db.commit()
        group_id = group.id
    async with file_db() as db:
        await governance.add_group_member(
            db, principal=owner, group_id=group_id, member_principal_id=member.principal_id
        )
        await db.commit()
    async with file_db() as db:
        assert (
            await governance.remove_group_member(
                db, principal=owner, group_id=group_id, member_principal_id=member.principal_id
            )
            == 1
        )
        await db.commit()
    resolved = await _resolve(file_db, principal_id=member.principal_id, tenant_id="tnt-local")
    assert group_id not in resolved.group_ids


async def test_steward_manages_scoped_source_without_tenant_admin(file_db):
    owner = await _principal_for(
        file_db, subject="oidc|st-owner", role="owner", tenant_id="tnt-local"
    )
    steward = await _principal_for(
        file_db, subject="oidc|hr-steward", role="editor", tenant_id="tnt-local"
    )
    async with file_db() as db:
        department = await governance.create_department(
            db, principal=owner, slug="hr2", name="HR Two"
        )
        await db.commit()
        department_id = department.id

    async with file_db() as db:
        await governance.grant_steward(
            db,
            principal=owner,
            steward_principal_id=steward.principal_id,
            scope_type="department",
            scope_id=department_id,
        )
        await db.commit()

    # The steward keeps their editor role (no tenant admin) but gains the scope.
    resolved = await _resolve(file_db, principal_id=steward.principal_id, tenant_id="tnt-local")
    assert not resolved.has(Permission.TENANT_ADMIN)
    assert ("department", department_id) in resolved.steward_scopes

    # ...and still cannot administer tenant membership.
    async with file_db() as db:
        with pytest.raises(Exception):
            await client_admin.list_members(db, principal=resolved)


# ---------------------------------------------------------------------------
# No cross-tenant enumeration
# ---------------------------------------------------------------------------


async def test_no_cross_tenant_enumeration(file_db):
    other_tenant = Tenant(id="tnt-other", slug="other", name="Other", status="active")
    async with file_db() as db:
        db.add(other_tenant)
        await db.commit()
    await _principal_for(
        file_db, subject="oidc|other-owner", role="owner", tenant_id="tnt-other"
    )

    owner = await _principal_for(
        file_db, subject="oidc|mine-owner", role="owner", tenant_id="tnt-local"
    )
    async with file_db() as db:
        members = await client_admin.list_members(db, principal=owner)
    assert all(m.subject != "oidc|other-owner" for m in members)

    # Reading preferences and usage stays inside the caller's tenant.
    async with file_db() as db:
        prefs = await client_admin.read_preferences(db, principal=owner)
        usage = await client_admin.usage_summary(db, principal=owner)
    assert prefs is not None and usage["tenant_id"] == "tnt-local"


# ---------------------------------------------------------------------------
# Preferences and usage placeholder
# ---------------------------------------------------------------------------


async def test_preferences_whitelist_and_persistence(file_db):
    owner = await _principal_for(
        file_db, subject="oidc|pref-owner", role="owner", tenant_id="tnt-local"
    )
    async with file_db() as db:
        updated = await client_admin.update_preferences(
            db,
            principal=owner,
            changes={"default_report_timezone": "America/Jamaica", "notify_on_run_failure": True},
        )
        await db.commit()
    assert updated["default_report_timezone"] == "America/Jamaica"
    assert updated["notify_on_run_failure"] is True

    async with file_db() as db:
        stored = await client_admin.read_preferences(db, principal=owner)
    assert stored["default_report_timezone"] == "America/Jamaica"

    # Unknown keys and wrong types are refused, so this is not a config back door.
    async with file_db() as db:
        with pytest.raises(ValueError):
            await client_admin.update_preferences(
                db, principal=owner, changes={"model_api_key": "x"}
            )
    async with file_db() as db:
        with pytest.raises(ValueError):
            await client_admin.update_preferences(
                db, principal=owner, changes={"notify_on_run_failure": "yes"}
            )


async def test_usage_summary_records_plan_placeholder(file_db):
    owner = await _principal_for(
        file_db, subject="oidc|usage-owner", role="owner", tenant_id="tnt-local"
    )
    async with file_db() as db:
        summary = await client_admin.usage_summary(db, principal=owner)
    assert summary["tenant_id"] == "tnt-local"
    assert "usage" in summary
    assert summary["plan"] is None and summary["entitlements"] is None


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def test_migration_0013_adds_tenant_preferences(tmp_path):
    from sqlalchemy import create_engine, inspect

    from app.migrations_runner import adopt_and_upgrade, current_revision, sync_url_for

    url = f"sqlite+aiosqlite:///{tmp_path / 'bv3b.db'}"
    adopt_and_upgrade(url)
    assert current_revision(url) == "0013_tenant_preferences"
    engine = create_engine(sync_url_for(url), future=True)
    try:
        columns = {col["name"] for col in inspect(engine).get_columns("tenants")}
    finally:
        engine.dispose()
    assert "settings_json" in columns
