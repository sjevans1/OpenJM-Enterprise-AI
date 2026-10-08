"""BV3-C control-plane relocation of Connectors and Operations.

The raw connector and operations surfaces move behind explicit OpenJM platform
authority. These tests prove the boundary is enforced by the backend (not by
hiding a menu), that the new capability is not implied by any other grant or
tier, and that the end-user retrieval path and scheduled execution are untouched.

Functional regression for retrieval, authorization and execution lives in the
accepted VS7 suites (``test_vs7_connector_security.py``, ``test_vs7_scheduler.py``,
``test_vs7_notifications.py``, ``test_vs7_action_schedule_security.py``), which
drive the service layer and are re-run against this head.
"""

import pytest
from sqlalchemy import func, select, text

from app.core.permissions import permissions_for_role
from app.core.platform import (
    PLATFORM_TIERS,
    PlatformCapability,
    capabilities_grant_content_access,
    is_known_platform_capability,
    platform_capabilities_for_tier,
)
from app.core.tenancy import LEGACY_PRINCIPAL_ID
from app.models import AuditRecord
from app.services import access_governance as governance

RAW_CONNECTOR_ROUTES = ("/api/connectors", "/api/connectors/types")
RAW_OPERATIONS_ROUTES = (
    "/api/operations/schedules",
    "/api/operations/schedules/operations",
    "/api/operations/notification-channels",
    "/api/operations/notifications",
)


async def _grant_local(file_db, *capabilities) -> None:
    """Grant platform capabilities to the dev-authenticated principal.

    The dev identity resolves platform capabilities from the database on every
    request, so this exercises the real route guards end to end.
    """
    async with file_db() as db:
        await governance.grant_platform_operator(
            db, principal_id=LEGACY_PRINCIPAL_ID, capabilities=list(capabilities)
        )
        await db.commit()


# ---------------------------------------------------------------------------
# The boundary: tenant roles are rejected on raw machinery
# ---------------------------------------------------------------------------


async def test_tenant_owner_is_rejected_on_raw_connector_routes(client, file_db):
    """The dev principal is a tenant owner and holds no platform capability."""
    for path in RAW_CONNECTOR_ROUTES:
        response = await client.get(path)
        assert response.status_code == 403, (path, response.status_code)

    created = await client.post(
        "/api/connectors", json={"name": "nope", "connector_type": "workspace", "version": "1.0.0"}
    )
    assert created.status_code == 403


async def test_tenant_owner_is_rejected_on_raw_operations_routes(client, file_db):
    for path in RAW_OPERATIONS_ROUTES:
        response = await client.get(path)
        assert response.status_code == 403, (path, response.status_code)

    assert (await client.post("/api/operations/schedules", json={})).status_code == 403
    assert (
        await client.post("/api/operations/notification-channels", json={})
    ).status_code == 403


async def test_tenant_admin_role_alone_is_not_enough(file_db):
    """A tenant admin permission set contains no platform capability at all."""
    admin = permissions_for_role("admin")
    owner = permissions_for_role("owner")
    from app.core.permissions import Permission

    # The legacy tenant permissions still exist for the end-user action runtime...
    assert Permission.CONNECTOR_READ in admin
    assert Permission.SCHEDULES_WRITE in admin
    assert Permission.CONNECTOR_READ in owner
    # ...but they are tenant permissions, and the platform plane never consults them.
    assert not is_known_platform_capability(Permission.CONNECTOR_READ.value)
    assert not is_known_platform_capability(Permission.SCHEDULES_WRITE.value)


# ---------------------------------------------------------------------------
# Platform authority is accepted, and only the explicit capability
# ---------------------------------------------------------------------------


async def test_platform_operator_with_operations_admin_is_authorized(client, file_db):
    await _grant_local(file_db, PlatformCapability.OPERATIONS_ADMIN)

    connectors = await client.get("/api/connectors")
    assert connectors.status_code == 200, connectors.text
    assert connectors.json() == {"connectors": []}
    assert (await client.get("/api/connectors/types")).status_code == 200

    for path in RAW_OPERATIONS_ROUTES:
        response = await client.get(path)
        assert response.status_code == 200, (path, response.status_code)


async def test_metadata_and_tenant_capabilities_do_not_authorize_machinery(client, file_db):
    await _grant_local(
        file_db, PlatformCapability.METADATA_READ, PlatformCapability.TENANTS_ADMIN
    )

    # The platform plane is reachable...
    assert (await client.get("/api/platform/tenants")).status_code == 200
    # ...but it grants no raw machinery authority.
    for path in RAW_CONNECTOR_ROUTES + RAW_OPERATIONS_ROUTES:
        assert (await client.get(path)).status_code == 403, path


async def test_content_support_does_not_authorize_machinery(client, file_db):
    await _grant_local(file_db, PlatformCapability.CONTENT_SUPPORT)
    assert (await client.get("/api/connectors")).status_code == 403
    assert (await client.get("/api/operations/schedules")).status_code == 403


async def test_operations_admin_is_never_implied_by_a_tier():
    assert is_known_platform_capability(PlatformCapability.OPERATIONS_ADMIN.value)
    for tier in PLATFORM_TIERS:
        assert PlatformCapability.OPERATIONS_ADMIN not in platform_capabilities_for_tier(tier)
    # Machinery authority is not content authority either.
    assert not capabilities_grant_content_access(
        frozenset({PlatformCapability.OPERATIONS_ADMIN.value})
    )


async def test_platform_operator_grant_accepts_the_new_capability(client, file_db):
    from app.services import identity as identity_service

    async with file_db() as db:
        account = await identity_service.get_or_create_principal(db, subject="oidc|op-machinery")
        await db.commit()
        account_id = account.id

    await _grant_local(file_db, PlatformCapability.OPERATORS_ADMIN)

    granted = await client.post(
        "/api/platform/operators",
        json={
            "principal_id": account_id,
            "capabilities": [PlatformCapability.OPERATIONS_ADMIN.value],
        },
    )
    assert granted.status_code == 200, granted.text
    assert granted.json()[0]["capability"] == PlatformCapability.OPERATIONS_ADMIN.value


# ---------------------------------------------------------------------------
# Privileged mutations stay audited
# ---------------------------------------------------------------------------


async def test_raw_scheduler_administration_is_still_audited(client, file_db):
    await _grant_local(file_db, PlatformCapability.OPERATIONS_ADMIN)

    created = await client.post(
        "/api/operations/schedules",
        json={
            "name": "Nightly report rerun",
            "schedule_type": "report_rerun",
            "operation": "report.rerun",
            "interval_seconds": 900,
            "timezone_name": "UTC",
        },
    )
    assert created.status_code == 201, created.text

    async with file_db() as db:
        audits = (
            await db.execute(select(AuditRecord).where(AuditRecord.action.like("schedule.%")))
        ).scalars().all()
    assert any(row.action == "schedule.create" for row in audits)


# ---------------------------------------------------------------------------
# Preservation: retrieval and scheduled execution do not need platform authority
# ---------------------------------------------------------------------------


async def test_connector_backed_retrieval_does_not_require_platform_authority(file_db):
    """The retrieval gate is tenant/principal scoped and fails closed without it."""
    from app.services.connectors import authorization as authorization_service

    async with file_db() as db:
        allowed, denials = await authorization_service.authorized_connector_document_ids(
            db, tenant_id="tnt-local", principal_id=LEGACY_PRINCIPAL_ID
        )
    # No connectors configured: the gate yields nothing and raises nothing, so the
    # retrieval path is unaffected by the route relocation.
    assert allowed == set()
    assert denials == {}


async def test_scheduled_execution_registry_is_intact(file_db):
    """The scheduler still knows its operations, so schedules can still execute."""
    from app.services import scheduler as scheduler_service

    operations = scheduler_service.registered_operations()
    assert operations
    assert all(scheduler_service.operation_supported(op) for op in operations)


# ---------------------------------------------------------------------------
# Migration: the capability vocabulary is widened, not merely redefined
# ---------------------------------------------------------------------------


def test_migration_0014_widens_the_platform_operator_constraint(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.exc import IntegrityError

    from app.migrations_runner import adopt_and_upgrade, current_revision, sync_url_for

    url = f"sqlite+aiosqlite:///{tmp_path / 'bv3c.db'}"
    adopt_and_upgrade(url)
    assert current_revision(url) is not None

    engine = create_engine(sync_url_for(url), future=True)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO principal_accounts "
                    "(id, subject, status, created_at, updated_at) "
                    "VALUES ('p-1', 'oidc|p1', 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            # The widened vocabulary accepts the relocated machinery capability.
            conn.execute(
                text(
                    "INSERT INTO platform_operators "
                    "(id, principal_id, capability, status, created_at) "
                    "VALUES ('po-1', 'p-1', 'platform:operations:admin', 'active', "
                    "CURRENT_TIMESTAMP)"
                )
            )
            stored = conn.execute(
                text("SELECT capability FROM platform_operators WHERE id = 'po-1'")
            ).scalar_one()
            assert stored == "platform:operations:admin"

        # The constraint still rejects an undefined capability.
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO platform_operators "
                        "(id, principal_id, capability, status, created_at) "
                        "VALUES ('po-2', 'p-1', 'platform:root:everything', 'active', "
                        "CURRENT_TIMESTAMP)"
                    )
                )
    finally:
        engine.dispose()


async def test_grant_platform_operator_accepts_machinery_capability(file_db):
    from app.services import identity as identity_service

    async with file_db() as db:
        account = await identity_service.get_or_create_principal(db, subject="oidc|op-mach")
        rows = await governance.grant_platform_operator(
            db,
            principal_id=account.id,
            capabilities=[PlatformCapability.OPERATIONS_ADMIN],
        )
        await db.commit()
    assert rows[0].capability == PlatformCapability.OPERATIONS_ADMIN.value

    async with file_db() as db:
        resolved = await governance.platform_capabilities_for(db, principal_id=account.id)
        count = await db.scalar(select(func.count()).select_from(AuditRecord))
    assert PlatformCapability.OPERATIONS_ADMIN in resolved
    assert count and count > 0


def test_migration_0014_downgrade_revokes_without_deleting_and_re_upgrades(tmp_path):
    """The downgrade path, which the original revision got wrong.

    Revoking a row does not remove the capability value it holds, so a plain
    narrowed ``capability IN`` constraint fails while any operations:admin row
    remains. The constraint is therefore ``status = 'revoked' OR capability IN``:
    active grants are bounded by the vocabulary, revoked rows are history.
    """
    from alembic import command
    from sqlalchemy import create_engine
    from sqlalchemy.exc import IntegrityError

    from app.migrations_runner import (
        _alembic_config,
        adopt_and_upgrade,
        current_revision,
        sync_url_for,
    )

    url = f"sqlite+aiosqlite:///{tmp_path / 'bv3c_downgrade.db'}"
    adopt_and_upgrade(url)
    assert current_revision(url) == "0014_operations_admin_capability"
    sync_url = sync_url_for(url)
    config = _alembic_config(sync_url)

    engine = create_engine(sync_url, future=True)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO principal_accounts "
                    "(id, subject, status, created_at, updated_at) "
                    "VALUES ('p-dg', 'oidc|dg', 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO platform_operators "
                    "(id, principal_id, capability, status, created_at) "
                    "VALUES ('po-dg', 'p-dg', 'platform:operations:admin', 'active', "
                    "CURRENT_TIMESTAMP)"
                )
            )

        command.downgrade(config, "0013_tenant_preferences")
        assert current_revision(url) == "0013_tenant_preferences"

        with engine.begin() as conn:
            row = conn.execute(
                text(
                    "SELECT status, revoked_at FROM platform_operators WHERE id = 'po-dg'"
                )
            ).first()
        assert row is not None, "the grant row must survive the downgrade"
        assert row[0] == "revoked"
        assert row[1] is not None

        # An active grant of the retired capability is refused again.
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO platform_operators "
                        "(id, principal_id, capability, status, created_at) "
                        "VALUES ('po-dg2', 'p-dg', 'platform:operations:admin', 'active', "
                        "CURRENT_TIMESTAMP)"
                    )
                )

        # A revoked row keeping the historical value is legal...
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO principal_accounts "
                    "(id, subject, status, created_at, updated_at) "
                    "VALUES ('p-dg2', 'oidc|dg2', 'active', CURRENT_TIMESTAMP, "
                    "CURRENT_TIMESTAMP)"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO platform_operators "
                    "(id, principal_id, capability, status, created_at) "
                    "VALUES ('po-dg3', 'p-dg2', 'platform:operations:admin', 'revoked', "
                    "CURRENT_TIMESTAMP)"
                )
            )

        # ...and an active grant of a still-valid capability is unaffected.
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO platform_operators "
                    "(id, principal_id, capability, status, created_at) "
                    "VALUES ('po-dg4', 'p-dg', 'platform:metadata:read', 'active', "
                    "CURRENT_TIMESTAMP)"
                )
            )

        command.upgrade(config, "0014_operations_admin_capability")
        assert current_revision(url) == "0014_operations_admin_capability"

        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO principal_accounts "
                    "(id, subject, status, created_at, updated_at) "
                    "VALUES ('p-dg3', 'oidc|dg3', 'active', CURRENT_TIMESTAMP, "
                    "CURRENT_TIMESTAMP)"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO platform_operators "
                    "(id, principal_id, capability, status, created_at) "
                    "VALUES ('po-dg5', 'p-dg3', 'platform:operations:admin', 'active', "
                    "CURRENT_TIMESTAMP)"
                )
            )
            statuses = dict(
                conn.execute(
                    text("SELECT id, status FROM platform_operators")
                ).all()
            )
        assert statuses["po-dg"] == "revoked", "re-upgrade must not resurrect a revoked grant"
        assert statuses["po-dg5"] == "active"
    finally:
        engine.dispose()
