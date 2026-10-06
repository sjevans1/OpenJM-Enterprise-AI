"""PostgreSQL application-metadata compatibility (#7).

The application's own metadata database must work on PostgreSQL, not only
SQLite. These tests start a real PostgreSQL server (the ``pgserver`` package
bundles the binaries), migrate it with the same revisions, and then exercise the
application's identity layer against it.

If the bundled server cannot start in this environment the tests skip with an
explicit reason, so "PostgreSQL support" can never be claimed untested.
"""

import os
import shutil

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text

from app.migrations_runner import (
    BASELINE_REVISION,
    _alembic_config,
    adopt_and_upgrade,
    current_revision,
    sync_url_for,
)

pgserver = pytest.importorskip("pgserver", reason="pgserver not installed")

@pytest.fixture(scope="module")
def postgres(tmp_path_factory):
    root = tmp_path_factory.mktemp("pgdata")
    try:
        server = pgserver.get_server(str(root))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL could not be started in this environment: {exc}")
    try:
        yield server
    finally:
        try:
            server.cleanup()
        except Exception:  # noqa: BLE001
            pass


@pytest.fixture
def pg_url(postgres):
    base = postgres.get_uri()  # postgresql://postgres:@/postgres?host=/...
    return base.replace("postgresql://", "postgresql+asyncpg://", 1)


def _reset(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()


def test_postgres_fresh_upgrade_creates_full_schema(postgres, pg_url):
    sync = sync_url_for(pg_url)
    _reset(sync)
    result = adopt_and_upgrade(pg_url)
    assert result["adopted_baseline"] is False
    assert current_revision(pg_url)

    engine = create_engine(sync)
    names = set(inspect(engine).get_table_names())
    with engine.connect() as conn:
        triggers = {
            row[0]
            for row in conn.execute(
                text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal")
            )
        }
        tenant = conn.execute(text("SELECT id, slug FROM tenants")).fetchall()
    engine.dispose()

    for expected in (
        "alembic_version",
        "tenants",
        "principal_accounts",
        "tenant_memberships",
        "auth_sessions",
        "audit_records",
        "action_plans",
        "action_approvals",
        "action_executions",
        "document_leases",
        "conversations",
        "messages",
        "documents",
        "data_sources",
        "execution_traces",
        "saved_reports",
        "report_definition_versions",
        "report_runs",
        # VS7: the connector, scheduling and notification tables must exist on
        # PostgreSQL too, not only on SQLite.
        "connector_instances",
        "connector_credentials",
        "external_resources",
        "connector_cursors",
        "connector_sync_runs",
        "workspace_user_mappings",
        "schedules",
        "schedule_runs",
        "notification_channels",
        "notifications",
    ):
        assert expected in names, f"{expected} missing on PostgreSQL"
    assert {
        "trg_report_runs_protect_identity",
        "trg_report_runs_terminal_immutable",
        "trg_documents_no_resurrection",
    } <= triggers
    assert tenant == [("tnt-local", "local")]


def test_postgres_repeated_startup_is_idempotent(postgres, pg_url):
    sync = sync_url_for(pg_url)
    _reset(sync)
    adopt_and_upgrade(pg_url)
    revision = current_revision(pg_url)
    for _ in range(2):
        assert adopt_and_upgrade(pg_url)["adopted_baseline"] is False
        assert current_revision(pg_url) == revision


def test_postgres_legacy_adoption_preserves_rows(postgres, pg_url):
    sync = sync_url_for(pg_url)
    _reset(sync)
    command.upgrade(_alembic_config(sync), BASELINE_REVISION)
    engine = create_engine(sync)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO conversations (id,user_id,title,created_at,updated_at) "
                "VALUES ('c1','local-admin','Legacy','2026-01-01','2026-01-01')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO messages (id,conversation_id,role,content,created_at) "
                "VALUES ('m1','c1','user','legacy question','2026-01-01')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO documents (id,user_id,original_name,stored_path,size_bytes,status,indexed,created_at) "
                "VALUES ('d1','local-admin','policy.md','/tmp/p.md',10,'ready',true,'2026-01-01')"
            )
        )
        conn.execute(text("DELETE FROM alembic_version"))
    engine.dispose()

    result = adopt_and_upgrade(pg_url)
    assert result["adopted_baseline"] is True
    engine = create_engine(sync)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT content FROM messages")).scalar() == "legacy question"
        assert conn.execute(text("SELECT tenant_id FROM documents")).scalar() == "tnt-local"
        assert conn.execute(text("SELECT lifecycle_state FROM documents")).scalar() == "ready"
    engine.dispose()


def test_postgres_document_resurrection_guard_is_enforced(postgres, pg_url):
    sync = sync_url_for(pg_url)
    _reset(sync)
    adopt_and_upgrade(pg_url)
    engine = create_engine(sync)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO documents (id,user_id,tenant_id,original_name,stored_path,size_bytes,status,indexed,"
                "lifecycle_state,lifecycle_version,created_at) "
                "VALUES ('d2','local-admin','tnt-local','p.md','/tmp/p.md',10,'deleted',false,'deleted',2,'2026-01-01')"
            )
        )
    with engine.begin() as conn:
        with pytest.raises(Exception):
            conn.execute(
                text("UPDATE documents SET lifecycle_state='ready' WHERE id='d2'")
            )
    with engine.connect() as conn:
        assert conn.execute(
            text("SELECT lifecycle_state FROM documents WHERE id='d2'")
        ).scalar() == "deleted"
    engine.dispose()


async def test_postgres_identity_layer_round_trip(postgres, pg_url):
    """The real identity service runs against PostgreSQL, not just SQLite."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.tenancy import LEGACY_TENANT_ID
    from app.services import identity as identity_service

    sync = sync_url_for(pg_url)
    _reset(sync)
    adopt_and_upgrade(pg_url)

    engine = create_async_engine(pg_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as db:
            await identity_service.ensure_local_identity(db)
            tenant = await identity_service.create_tenant(
                db, slug="pg-acme", name="PG Acme", tenant_id="tnt-pg"
            )
            account = await identity_service.get_or_create_principal(
                db, subject="pg-user", principal_id="pgp"
            )
            await identity_service.add_membership(
                db, tenant_id=tenant.id, principal_id=account.id, role="editor"
            )
            await db.commit()

        async with maker() as db:
            principal = await identity_service.principal_for_account(
                db, account, tenant_id="tnt-pg", auth_method="oidc"
            )
            assert principal.tenant_id == "tnt-pg"
            assert principal.role == "editor"
            assert principal.user_id == "tnt-pg:pgp"
            assert principal.has("knowledge:read")

            session_token = await identity_service.create_session(db, principal=principal)
            resolved = await identity_service.resolve_session(db, session_token)
            assert resolved.principal_id == "pgp"

            await identity_service.revoke_membership(
                db, tenant_id="tnt-pg", principal_id="pgp"
            )
            await db.commit()

        # Revocation is durable and immediate on PostgreSQL too. Revoking a
        # membership also revokes the sessions bound to it, so the session is
        # refused as an unauthenticated credential.
        async with maker() as db:
            from app.core.identity import AuthenticationError, IdentityError

            with pytest.raises(IdentityError) as excinfo:
                await identity_service.resolve_session(db, session_token)
            assert isinstance(excinfo.value, AuthenticationError)
            assert excinfo.value.code == "session_revoked"

        async with maker() as db:
            legacy = await identity_service.principal_for_account(
                db,
                await identity_service.get_or_create_principal(
                    db, subject="local:local-admin", principal_id="local-admin"
                ),
                tenant_id=LEGACY_TENANT_ID,
                auth_method="local-dev",
            )
            assert legacy.user_id == "local-admin"
    finally:
        await engine.dispose()
