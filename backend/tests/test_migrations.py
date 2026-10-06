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
