"""PostgreSQL parity for the migration hardening suite.

The populated-schema upgrade and the downgrade/re-upgrade round trip are the
riskiest parts of a release: the revisions run against a database that already
holds a deployment's rows, on the dialect a production install uses. The SQLite
tests in ``test_migrations.py`` prove the row-preservation invariant; this module
proves the same scenarios on a real PostgreSQL server started by ``pgserver``.

If ``pgserver`` is missing or its server cannot start, every test in this module
skips with a reason. The PostgreSQL path must never be reported as verified when
it did not actually run.
"""

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text

from app.migrations_runner import (
    _alembic_config,
    adopt_and_upgrade,
    current_revision,
    sync_url_for,
)

pgserver = pytest.importorskip("pgserver", reason="pgserver not installed")


@pytest.fixture(scope="module")
def postgres(tmp_path_factory):
    root = tmp_path_factory.mktemp("e2_pgdata")
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


# Tables seeded at the old revision and re-checked after the upgrade. They all
# exist at 0007 and must survive to the head.
REPRESENTATIVE_TABLES = (
    "conversations",
    "messages",
    "documents",
    "data_sources",
    "saved_reports",
    "report_definition_versions",
    "report_runs",
    "connector_instances",
    "schedules",
)

# Representative rows in the shape a 0007_vs7_connectors deployment holds. Raw
# SQL, because the ORM reflects the head schema and would name columns that do
# not exist yet at 0007.
_SEED_STATEMENTS = (
    "INSERT INTO conversations (id,user_id,title,created_at,updated_at) "
    "VALUES ('c1','local-admin','Legacy chat','2026-01-01','2026-01-01')",
    "INSERT INTO messages (id,conversation_id,role,content,created_at) "
    "VALUES ('m1','c1','user','legacy question','2026-01-01')",
    "INSERT INTO documents (id,user_id,original_name,stored_path,size_bytes,status,indexed,created_at) "
    "VALUES ('d1','local-admin','policy.md','/tmp/policy.md',42,'ready',true,'2026-01-01')",
    "INSERT INTO data_sources (id,user_id,name,engine,connection_secret,status,enabled,created_at,updated_at) "
    "VALUES ('s1','local-admin','Finance','sqlite','enc','connected',true,'2026-01-01','2026-01-01')",
    "INSERT INTO saved_reports (id,user_id,conversation_id,message_id,title,answer_text,evidence_json,"
    "execution_class,source_count,snapshot_as_of,created_at) "
    "VALUES ('rep1','local-admin','c1','m1','Legacy report','legacy answer','[]','knowledge',1,"
    "'2026-01-01','2026-01-01')",
    "INSERT INTO report_definition_versions (id,report_id,user_id,version,question_text,requested_mode,"
    "pinned_document_ids_json,pinned_source_tables_json,created_at) "
    "VALUES ('def1','rep1','local-admin',1,'legacy question','knowledge','[\"d1\"]','{}','2026-01-01')",
    "INSERT INTO report_runs (id,user_id,report_id,definition_id,definition_version,requested_mode,"
    "idempotency_key,request_fingerprint,status,started_at,deadline_at,finished_at,result_json,"
    "result_size_bytes,result_sha256,created_at) "
    "VALUES ('run1','local-admin','rep1','def1',1,'knowledge','key-1','fp-1','succeeded',"
    "'2026-01-01','2026-01-01','2026-01-01','{\"answer\":\"legacy\"}',18,'sha','2026-01-01')",
    "INSERT INTO connector_instances (id,tenant_id,name,connector_type,connector_version,display_name,"
    "status,enabled,health_status,created_at,updated_at) "
    "VALUES ('ci1','tnt-local','Finance connector','sqlite','1','Finance','active',true,'unknown',"
    "'2026-01-01','2026-01-01')",
    "INSERT INTO schedules (id,tenant_id,owner_principal_id,name,schedule_type,operation,status,enabled,"
    "timezone,interval_seconds,misfire_policy,max_retries,created_at,updated_at) "
    "VALUES ('sch1','tnt-local','local-admin','Nightly sync','connector_sync','connector.sync','active',"
    "true,'UTC',3600,'skip',2,'2026-01-01','2026-01-01')",
)


def _seed(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        for statement in _SEED_STATEMENTS:
            conn.execute(text(statement))
    engine.dispose()


def _counts(sync_url: str, tables) -> dict:
    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            return {
                table: conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
                for table in tables
            }
    finally:
        engine.dispose()


def _fingerprint(sync_url: str) -> dict:
    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            return {
                "message_content": conn.execute(
                    text("SELECT content FROM messages WHERE id='m1'")
                ).scalar(),
                "document_tenant": conn.execute(
                    text("SELECT tenant_id FROM documents WHERE id='d1'")
                ).scalar(),
                "connector_name": conn.execute(
                    text("SELECT name FROM connector_instances WHERE id='ci1'")
                ).scalar(),
                "schedule_name": conn.execute(
                    text("SELECT name FROM schedules WHERE id='sch1'")
                ).scalar(),
            }
    finally:
        engine.dispose()


def _chain(url: str) -> set[str]:
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(_alembic_config(sync_url_for(url)))
    chain: set[str] = set()
    cursor: str | None = script.get_current_head()
    while cursor:
        chain.add(cursor)
        revision = script.get_revision(cursor)
        cursor = revision.down_revision if revision else None
    return chain


def test_postgres_upgrade_from_populated_vs7_head_preserves_all_rows(postgres, pg_url):
    """A populated VS7 database upgrades to head on PostgreSQL with no rewrite."""
    sync = sync_url_for(pg_url)
    _reset(sync)
    command.upgrade(_alembic_config(sync), "0007_vs7_connectors")
    assert current_revision(pg_url) == "0007_vs7_connectors"
    _seed(sync)

    before = _counts(sync, REPRESENTATIVE_TABLES)
    before_values = _fingerprint(sync)

    result = adopt_and_upgrade(pg_url)
    assert result["adopted_baseline"] is False
    revision = current_revision(pg_url)
    assert revision is not None
    assert revision in _chain(pg_url)
    assert "0017_inf1b_admission" in _chain(pg_url)

    assert _counts(sync, REPRESENTATIVE_TABLES) == before, (
        "no pre-existing row may be lost or duplicated by the PostgreSQL upgrade"
    )
    assert _fingerprint(sync) == before_values, (
        "no stored value may be rewritten by the PostgreSQL upgrade"
    )

    engine = create_engine(sync)
    try:
        names = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    for table in (
        "capacity_pools",
        "admission_tickets",
        "entitlement_plans",
        "credit_ledger_entries",
        "usage_reservations",
        "inference_deployments",
        "model_usage_events",
        "support_delegations",
        "departments",
    ):
        assert table in names, f"{table} missing on PostgreSQL after upgrade"


def test_postgres_downgrade_and_reupgrade_round_trips_populated_data(postgres, pg_url):
    """Downgrade a populated PostgreSQL head to 0007 and re-upgrade it."""
    sync = sync_url_for(pg_url)
    _reset(sync)
    config = _alembic_config(sync)
    command.upgrade(config, "0007_vs7_connectors")
    _seed(sync)

    command.upgrade(config, "head")
    assert current_revision(pg_url) is not None
    at_head = _counts(sync, REPRESENTATIVE_TABLES)
    at_head_values = _fingerprint(sync)

    command.downgrade(config, "0007_vs7_connectors")
    assert current_revision(pg_url) == "0007_vs7_connectors"
    engine = create_engine(sync)
    try:
        old_names = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    for table in (
        "capacity_pools",
        "admission_tickets",
        "credit_ledger_entries",
        "model_usage_events",
        "support_delegations",
        "departments",
    ):
        assert table not in old_names, f"downgrade left {table} behind on PostgreSQL"

    command.upgrade(config, "head")
    assert current_revision(pg_url) is not None
    assert _counts(sync, REPRESENTATIVE_TABLES) == at_head, (
        "the PostgreSQL round trip must preserve every earlier row"
    )
    assert _fingerprint(sync) == at_head_values, (
        "the PostgreSQL round trip must preserve every stored value"
    )
