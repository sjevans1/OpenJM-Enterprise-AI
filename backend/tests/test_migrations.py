"""#7 versioned application-metadata migration acceptance.

Every test builds a real database file, migrates it with the real Alembic
runner, and asserts on real rows. Nothing is mocked: the point of this suite is
that an actual previous deployment upgrades without losing or rewriting data.
"""

import sqlite3

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

LEGACY_ROWS = """
INSERT INTO conversations (id,user_id,title,created_at,updated_at)
VALUES ('c1','local-admin','Legacy chat','2026-01-01 00:00:00.000000','2026-01-01 00:00:00.000000');
INSERT INTO messages (id,conversation_id,role,content,execution_class,requested_mode,created_at)
VALUES ('m1','c1','user','legacy question','knowledge','knowledge','2026-01-01 00:00:00.000000'),
       ('m2','c1','assistant','legacy answer','knowledge','knowledge','2026-01-01 00:00:01.000000');
INSERT INTO documents (id,user_id,original_name,stored_path,mime_type,size_bytes,status,indexed,created_at)
VALUES ('d1','local-admin','policy.md','/tmp/policy.md','text/markdown',42,'ready',1,'2026-01-01 00:00:00.000000');
INSERT INTO data_sources (id,user_id,name,engine,connection_secret,revenue_currency,status,enabled,created_at,updated_at)
VALUES ('s1','local-admin','Finance','sqlite','enc',NULL,'connected',1,'2026-01-01 00:00:00.000000','2026-01-01 00:00:00.000000');
INSERT INTO execution_traces
 (id,request_id,tool_invocation_id,user_id,conversation_id,requested_mode,tool_name,operation_class,
  risk_level,requires_approval,input_hash,status,created_at)
VALUES ('t1','r1','ti1','local-admin','c1','knowledge','knowledge.search','read','low',0,'hash1','succeeded','2026-01-01 00:00:00.000000');
INSERT INTO saved_reports
 (id,user_id,conversation_id,message_id,title,answer_text,evidence_json,execution_class,requested_mode,
  source_count,snapshot_as_of,created_at)
VALUES ('rep1','local-admin','c1','m2','Legacy report','legacy answer','[]','knowledge','knowledge',1,
        '2026-01-01 00:00:01.000000','2026-01-01 00:00:01.000000');
INSERT INTO report_definition_versions
 (id,report_id,user_id,version,question_text,requested_mode,pinned_document_ids_json,
  pinned_source_tables_json,created_at)
VALUES ('def1','rep1','local-admin',1,'legacy question','knowledge','["d1"]','{}','2026-01-01 00:00:01.000000');
INSERT INTO report_runs
 (id,user_id,report_id,definition_id,definition_version,requested_mode,idempotency_key,
  request_fingerprint,status,started_at,deadline_at,finished_at,result_json,result_size_bytes,result_sha256,created_at)
VALUES ('run1','local-admin','rep1','def1',1,'knowledge','key-1','fp-1','succeeded',
        '2026-01-01 00:00:02.000000','2026-01-01 00:01:02.000000','2026-01-01 00:00:05.000000',
        '{"answer":"legacy"}',18,'sha','2026-01-01 00:00:02.000000');
"""


def _make_legacy_db(tmp_path) -> tuple[str, str]:
    """Create a database in the exact shape a pre-migration deployment had."""
    path = tmp_path / "legacy.db"
    url = f"sqlite+aiosqlite:///{path}"
    command.upgrade(_alembic_config(sync_url_for(url)), BASELINE_REVISION)
    connection = sqlite3.connect(path)
    connection.executescript(LEGACY_ROWS)
    # A pre-migration deployment has no alembic_version row at all.
    connection.execute("DELETE FROM alembic_version")
    connection.commit()
    connection.close()
    return str(path), url


def _counts(path: str) -> dict:
    connection = sqlite3.connect(path)
    tables = [
        "conversations",
        "messages",
        "documents",
        "data_sources",
        "execution_traces",
        "saved_reports",
        "report_definition_versions",
        "report_runs",
    ]
    result = {
        t: connection.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables
    }
    connection.close()
    return result


def test_fresh_database_upgrades_to_head(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'fresh.db'}"
    assert current_revision(url) is None
    result = adopt_and_upgrade(url)
    assert result["adopted_baseline"] is False
    revision = current_revision(url)
    assert revision and revision != BASELINE_REVISION

    engine = create_engine(sync_url_for(url))
    names = set(inspect(engine).get_table_names())
    engine.dispose()
    for expected in (
        "tenants",
        "principal_accounts",
        "tenant_memberships",
        "auth_sessions",
        "audit_records",
        "action_plans",
        "action_approvals",
        "action_executions",
        "document_leases",
        "documents",
        "conversations",
        "report_runs",
        # VS7
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
        assert expected in names


def test_repeated_startup_is_idempotent(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'repeat.db'}"
    first = adopt_and_upgrade(url)
    revision = current_revision(url)
    for _ in range(3):
        again = adopt_and_upgrade(url)
        assert again["adopted_baseline"] is False
        assert current_revision(url) == revision
    assert first["adopted_baseline"] is False


def test_legacy_deployment_is_adopted_and_preserved(tmp_path):
    path, url = _make_legacy_db(tmp_path)
    before = _counts(path)

    result = adopt_and_upgrade(url)
    assert result["adopted_baseline"] is True, "a pre-migration DB must be stamped"
    assert current_revision(url)
    assert _counts(path) == before, "no row may be lost or duplicated"

    connection = sqlite3.connect(path)
    assert connection.execute("SELECT content FROM messages WHERE id='m1'").fetchone()[0] == "legacy question"
    assert connection.execute("SELECT answer_text FROM saved_reports WHERE id='rep1'").fetchone()[0] == "legacy answer"
    assert connection.execute("SELECT result_json FROM report_runs WHERE id='run1'").fetchone()[0] == '{"answer":"legacy"}'
    assert connection.execute("SELECT tool_name FROM execution_traces WHERE id='t1'").fetchone()[0] == "knowledge.search"
    assert connection.execute("SELECT name FROM data_sources WHERE id='s1'").fetchone()[0] == "Finance"
    assert connection.execute("SELECT question_text FROM report_definition_versions WHERE id='def1'").fetchone()[0] == "legacy question"

    # Every legacy row is adopted into the local tenant, and the previous owner
    # key is untouched.
    assert connection.execute("SELECT DISTINCT tenant_id FROM conversations").fetchall() == [("tnt-local",)]
    assert connection.execute("SELECT DISTINCT tenant_id FROM report_runs").fetchall() == [("tnt-local",)]
    assert connection.execute("SELECT DISTINCT user_id FROM saved_reports").fetchall() == [("local-admin",)]

    # A previously ready document stays retrievable; a previously deleted one
    # does not exist here, so nothing becomes newly visible.
    assert connection.execute("SELECT lifecycle_state FROM documents WHERE id='d1'").fetchone()[0] == "ready"
    connection.close()


def test_upgrade_preserves_run_history_and_immutability_guards(tmp_path):
    path, url = _make_legacy_db(tmp_path)
    adopt_and_upgrade(url)
    connection = sqlite3.connect(path)
    triggers = {
        row[0]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
    }
    connection.close()
    assert {"trg_report_runs_protect_identity", "trg_report_runs_terminal_immutable", "trg_documents_no_resurrection"} <= triggers

    engine = create_engine(sync_url_for(url))
    with engine.begin() as conn:
        with pytest.raises(Exception):
            conn.execute(
                text("UPDATE report_runs SET status='failed' WHERE id='run1'")
            )
        row = conn.execute(
            text("SELECT status, result_json FROM report_runs WHERE id='run1'")
        ).fetchone()
    engine.dispose()
    assert row[0] == "succeeded"
    assert row[1] == '{"answer":"legacy"}'


def test_unknown_recorded_revision_fails_without_touching_data(tmp_path):
    """A database recorded at a revision this build does not know must fail closed."""
    path, url = _make_legacy_db(tmp_path)
    adopt_and_upgrade(url)
    before = _counts(path)

    connection = sqlite3.connect(path)
    connection.execute("UPDATE alembic_version SET version_num='deadbeef_unknown'")
    connection.commit()
    connection.close()

    with pytest.raises(Exception):
        adopt_and_upgrade(url)

    assert _counts(path) == before, "a failed migration must not destroy data"


def test_downgrade_and_reupgrade_preserves_application_data(tmp_path):
    """The documented rollback boundary: schema can move, application rows cannot."""
    path, url = _make_legacy_db(tmp_path)
    adopt_and_upgrade(url)
    before = _counts(path)

    config = _alembic_config(sync_url_for(url))
    command.downgrade(config, "0002_vs5_identity")
    command.upgrade(config, "head")

    assert _counts(path) == before
    assert current_revision(url)


VS7_TABLES = (
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
)


def test_vs7_upgrade_from_accepted_head_preserves_vs1_to_vs6_data(tmp_path):
    """Upgrade a deployment that already sits at the accepted VS5+VS6 head.

    This is the real upgrade path for VS7: a live deployment is at
    ``0006_guard_triggers`` with data in it, not at the baseline. The test seeds
    representative VS1-VS6 rows, applies the VS7 revision, and asserts that no
    earlier row was lost, duplicated or rewritten, that the VS7 tables now
    exist, and that a repeated start-up stays a no-op.
    """
    path = tmp_path / "vs7_upgrade.db"
    url = f"sqlite+aiosqlite:///{path}"
    config = _alembic_config(sync_url_for(url))

    # Sit at the accepted head, which is where a real deployment is today.
    command.upgrade(config, "0006_guard_triggers")
    assert current_revision(url) == "0006_guard_triggers"

    connection = sqlite3.connect(path)
    connection.executescript(LEGACY_ROWS)
    connection.commit()
    connection.close()
    before = _counts(str(path))
    assert before["documents"] == 1 and before["conversations"] == 1

    result = adopt_and_upgrade(url)
    assert result["adopted_baseline"] is False, "an already-versioned DB must not be re-stamped"
    assert current_revision(url) != "0006_guard_triggers"

    after = _counts(str(path))
    assert after == before, "no VS1-VS6 row may be lost or duplicated by the VS7 upgrade"

    connection = sqlite3.connect(path)
    assert (
        connection.execute("SELECT content FROM messages WHERE id='m1'").fetchone()[0]
        == "legacy question"
    )
    assert (
        connection.execute("SELECT original_name FROM documents WHERE id='d1'").fetchone()[0]
        == "policy.md"
    )
    names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    connection.close()
    for table in VS7_TABLES:
        assert table in names, f"VS7 table {table} was not created"

    # Repeated startup after the VS7 upgrade stays a no-op.
    revision = current_revision(url)
    for _ in range(3):
        assert adopt_and_upgrade(url)["adopted_baseline"] is False
        assert current_revision(url) == revision


def test_vs7_downgrade_and_reupgrade_preserves_earlier_data(tmp_path):
    """The VS7 revision must be reversible without touching earlier data."""
    path = tmp_path / "vs7_downgrade.db"
    url = f"sqlite+aiosqlite:///{path}"
    config = _alembic_config(sync_url_for(url))
    command.upgrade(config, "0006_guard_triggers")
    connection = sqlite3.connect(path)
    connection.executescript(LEGACY_ROWS)
    connection.commit()
    connection.close()
    before = _counts(str(path))

    command.upgrade(config, "head")
    command.downgrade(config, "0006_guard_triggers")
    connection = sqlite3.connect(path)
    names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    connection.close()
    for table in VS7_TABLES:
        assert table not in names, f"downgrade left {table} behind"

    command.upgrade(config, "head")
    assert _counts(str(path)) == before, "re-upgrade must not disturb earlier data"


# ---------------------------------------------------------------------------
# E2 migration hardening: populated-schema upgrade and downgrade round trips
# ---------------------------------------------------------------------------
#
# The tests above start from the baseline or the VS7 head. A live deployment is
# usually further along than that, so these tests upgrade a populated database
# from a representative *older* head all the way to the current head and prove
# that no pre-existing row is lost, duplicated or rewritten. The head is never
# pinned: the assertions walk the revision chain instead, so a later package
# that adds a revision does not break them.

# The tables every upgrade source below seeds and then re-checks. They all exist
# at the baseline and survive to the head.
REPRESENTATIVE_TABLES = (
    "conversations",
    "messages",
    "documents",
    "data_sources",
    "execution_traces",
    "saved_reports",
    "report_definition_versions",
    "report_runs",
    "connector_instances",
    "schedules",
)

# Tables that must exist once a populated database reaches the head. Their names
# come from the revision files, so the presence check doubles as a proof that the
# upgrade actually ran to head rather than stopping early.
HEAD_TABLES = (
    "tenants",
    "departments",
    "platform_operators",
    "data_stewards",
    "model_usage_events",
    "support_delegations",
    "inference_deployments",
    "inference_usage_attributions",
    "entitlement_plans",
    "credit_ledger_entries",
    "usage_reservations",
    "capacity_pools",
    "capacity_scopes",
    "capacity_leases",
    "admission_tickets",
)

# Representative VS7 rows beyond the baseline set: a connector instance and a
# schedule. Both are tenant-owned and carry data that later revisions must not
# touch.
POPULATED_EXTRA_ROWS = """
INSERT INTO connector_instances
 (id,tenant_id,name,connector_type,connector_version,display_name,status,enabled,
  health_status,created_at,updated_at)
VALUES ('ci1','tnt-local','Finance connector','sqlite','1','Finance','active',1,
        'unknown','2026-01-01 00:00:00.000000','2026-01-01 00:00:00.000000');
INSERT INTO schedules
 (id,tenant_id,owner_principal_id,name,schedule_type,operation,status,enabled,timezone,
  interval_seconds,misfire_policy,max_retries,created_at,updated_at)
VALUES ('sch1','tnt-local','local-admin','Nightly sync','connector_sync','connector.sync',
        'active',1,'UTC',3600,'skip',2,'2026-01-01 00:00:00.000000','2026-01-01 00:00:00.000000');
"""


def _revision_chain(url: str) -> set[str]:
    """Every revision from the script head down the down_revision chain."""
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(_alembic_config(sync_url_for(url)))
    chain: set[str] = set()
    cursor: str | None = script.get_current_head()
    while cursor:
        chain.add(cursor)
        revision = script.get_revision(cursor)
        cursor = revision.down_revision if revision else None
    return chain


def _table_counts(path: str, tables) -> dict:
    connection = sqlite3.connect(path)
    try:
        return {
            t: connection.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in tables
        }
    finally:
        connection.close()


def _fingerprint(path: str) -> dict:
    """A few stored values that no migration may silently rewrite.

    Captured before and after an upgrade or rollback so a rewrite, truncation or
    re-classification of an existing row is caught even when the row count is
    unchanged.
    """
    connection = sqlite3.connect(path)
    try:
        return {
            "message_content": connection.execute(
                "SELECT content FROM messages WHERE id='m1'"
            ).fetchone()[0],
            "document_lifecycle": connection.execute(
                "SELECT lifecycle_state FROM documents WHERE id='d1'"
            ).fetchone()[0],
            "document_tenant": connection.execute(
                "SELECT tenant_id FROM documents WHERE id='d1'"
            ).fetchone()[0],
            "connector_name": connection.execute(
                "SELECT name FROM connector_instances WHERE id='ci1'"
            ).fetchone()[0],
            "schedule_name": connection.execute(
                "SELECT name FROM schedules WHERE id='sch1'"
            ).fetchone()[0],
        }
    finally:
        connection.close()


def _populated_db_at(tmp_path, revision: str) -> tuple[str, str]:
    """Build a populated database stopped at ``revision``.

    ``revision`` here is a deliberate *starting* revision for an upgrade test,
    not a pinned head: the test then upgrades to head and asserts the chain
    reached a later revision.
    """
    path = tmp_path / f"populated_{revision}.db"
    url = f"sqlite+aiosqlite:///{path}"
    command.upgrade(_alembic_config(sync_url_for(url)), revision)
    assert current_revision(url) == revision
    connection = sqlite3.connect(path)
    connection.executescript(LEGACY_ROWS)
    connection.executescript(POPULATED_EXTRA_ROWS)
    connection.commit()
    connection.close()
    return str(path), url


def test_upgrade_from_populated_vs7_head_preserves_all_rows(tmp_path):
    """A populated VS7 database upgrades to head without a destructive rewrite."""
    path, url = _populated_db_at(tmp_path, "0007_vs7_connectors")
    before = _table_counts(path, REPRESENTATIVE_TABLES)
    before_values = _fingerprint(path)

    result = adopt_and_upgrade(url)
    assert result["adopted_baseline"] is False, "a versioned DB must not be re-stamped"

    revision = current_revision(url)
    assert revision is not None
    assert revision in _revision_chain(url)
    assert "0017_inf1b_admission" in _revision_chain(url)

    assert _table_counts(path, REPRESENTATIVE_TABLES) == before, (
        "no pre-existing row may be lost or duplicated by the upgrade"
    )
    assert _fingerprint(path) == before_values, (
        "no stored value may be rewritten by the upgrade"
    )

    connection = sqlite3.connect(path)
    assert connection.execute(
        "SELECT content FROM messages WHERE id='m1'"
    ).fetchone()[0] == "legacy question"
    assert connection.execute(
        "SELECT name FROM connector_instances WHERE id='ci1'"
    ).fetchone()[0] == "Finance connector"
    assert connection.execute(
        "SELECT name FROM schedules WHERE id='sch1'"
    ).fetchone()[0] == "Nightly sync"
    # The tenant the 0002 backfill assigned is unchanged.
    assert connection.execute(
        "SELECT DISTINCT tenant_id FROM conversations"
    ).fetchall() == [("tnt-local",)]
    tables = {
        row[0]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    connection.close()
    for table in HEAD_TABLES:
        assert table in tables, f"head table {table} was not created"


def test_upgrade_from_populated_bv1_head_preserves_all_rows(tmp_path):
    """A populated database at the pre-INF1 head upgrades to head additively.

    This is the path a deployment that adopted BV1/M1 takes to reach the M3 and
    INF1 packages: the accepted usage ledger and an active widened operator
    capability must both survive.
    """
    path, url = _populated_db_at(tmp_path, "0014_operations_admin_capability")
    connection = sqlite3.connect(path)
    connection.executescript(
        """
INSERT INTO principal_accounts (id,subject,status,created_at,updated_at)
VALUES ('pa1','local:local-admin','active','2026-01-01 00:00:00.000000','2026-01-01 00:00:00.000000');
INSERT INTO platform_operators (id,principal_id,capability,status,created_at)
VALUES ('po1','pa1','platform:operations:admin','active','2026-01-01 00:00:00.000000');
INSERT INTO model_usage_events
 (id,tenant_id,request_id,provider_route,model_name,usage_source,input_tokens,
  output_tokens,total_tokens,call_role,idempotency_key,status,started_at,finished_at,created_at)
VALUES ('mu1','tnt-local','req-1','local','gemma','provider_reported',10,20,30,'primary',
        'idem-1','succeeded','2026-01-01 00:00:00.000000','2026-01-01 00:00:01.000000',
        '2026-01-01 00:00:00.000000');
"""
    )
    connection.commit()
    connection.close()

    counted = REPRESENTATIVE_TABLES + (
        "principal_accounts",
        "platform_operators",
        "model_usage_events",
    )
    before = _table_counts(path, counted)

    result = adopt_and_upgrade(url)
    assert result["adopted_baseline"] is False
    assert current_revision(url) is not None
    assert "0017_inf1b_admission" in _revision_chain(url)
    assert _table_counts(path, counted) == before

    connection = sqlite3.connect(path)
    assert connection.execute(
        "SELECT capability FROM platform_operators WHERE id='po1'"
    ).fetchone()[0] == "platform:operations:admin"
    assert connection.execute(
        "SELECT status FROM platform_operators WHERE id='po1'"
    ).fetchone()[0] == "active", "the 0015 widening must not revoke an existing operator"
    assert connection.execute(
        "SELECT total_tokens FROM model_usage_events WHERE id='mu1'"
    ).fetchone()[0] == 30
    assert connection.execute(
        "SELECT content FROM messages WHERE id='m1'"
    ).fetchone()[0] == "legacy question"
    # The head capability vocabulary contains both the BV3-C and the INF1-A
    # additions, so the widening is additive rather than a replacement.
    ddl = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='platform_operators'"
    ).fetchone()[0]
    connection.close()
    assert "platform:operations:admin" in ddl
    assert "platform:inference:admin" in ddl


def test_downgrade_to_older_revision_and_reupgrade_round_trips_populated_data(tmp_path):
    """Downgrade a populated head to an old revision and re-upgrade it.

    This exercises the whole reversible span 0008..0017 (including the 0009/0010
    batch table rewrites of ``documents``/``data_sources``) and proves the rows
    seeded before the later packages survive the round trip.
    """
    path, url = _populated_db_at(tmp_path, "0007_vs7_connectors")
    config = _alembic_config(sync_url_for(url))

    command.upgrade(config, "head")
    assert current_revision(url) is not None
    at_head = _table_counts(path, REPRESENTATIVE_TABLES)
    at_head_values = _fingerprint(path)

    command.downgrade(config, "0007_vs7_connectors")
    assert current_revision(url) == "0007_vs7_connectors"
    connection = sqlite3.connect(path)
    old_tables = {
        row[0]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    connection.close()
    for table in (
        "capacity_pools",
        "admission_tickets",
        "credit_ledger_entries",
        "model_usage_events",
        "support_delegations",
        "departments",
    ):
        assert table not in old_tables, f"downgrade left {table} behind"

    command.upgrade(config, "head")
    assert current_revision(url) is not None
    assert _table_counts(path, REPRESENTATIVE_TABLES) == at_head, (
        "downgrade then re-upgrade must preserve every earlier row"
    )
    assert _fingerprint(path) == at_head_values, (
        "downgrade then re-upgrade must preserve every stored value"
    )
    connection = sqlite3.connect(path)
    assert connection.execute(
        "SELECT content FROM messages WHERE id='m1'"
    ).fetchone()[0] == "legacy question"
    assert connection.execute(
        "SELECT name FROM connector_instances WHERE id='ci1'"
    ).fetchone()[0] == "Finance connector"
    connection.close()


def test_downgrade_to_inf1_registry_and_reupgrade_restores_newest_tables(tmp_path):
    """The 0016 and 0017 revisions are reversible without disturbing INF1-A."""
    url = f"sqlite+aiosqlite:///{tmp_path / 'newest_roundtrip.db'}"
    config = _alembic_config(sync_url_for(url))
    adopt_and_upgrade(url)

    newest = (
        "entitlement_plans",
        "credit_ledger_entries",
        "usage_reservations",
        "capacity_pools",
        "capacity_scopes",
        "capacity_leases",
        "admission_tickets",
    )

    command.downgrade(config, "0015_inf1_inference_registry")
    assert current_revision(url) == "0015_inf1_inference_registry"
    engine = create_engine(sync_url_for(url))
    try:
        names = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    for table in newest:
        assert table not in names, f"downgrade left {table} behind"
    assert "inference_deployments" in names, "the 0015 tables must survive the rollback"

    command.upgrade(config, "head")
    assert current_revision(url) is not None
    engine = create_engine(sync_url_for(url))
    try:
        names = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    for table in newest:
        assert table in names, f"re-upgrade did not restore {table}"
