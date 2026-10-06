"""Database-level guards for immutable/append-only lifecycles.

Revision ID: 0006_guard_triggers
Revises: 0005_document_leases
Create Date: 2026-10-06

Two classes of guard, implemented per dialect:

* ``report_runs`` — identity fields are immutable and terminal runs cannot be
  reopened or regress. Moved here from the previous ad-hoc bootstrap so the
  guard is created by a reviewed migration on both SQLite and PostgreSQL.
* ``documents`` — a deleted document can never leave the deleted state, so a
  concurrent or crashed writer cannot silently resurrect it.

Guards are a defense-in-depth layer *behind* the application-level
compare-and-swap, not a replacement for it.
"""

from __future__ import annotations

from alembic import op

revision = "0006_guard_triggers"
down_revision = "0005_document_leases"
branch_labels = None
depends_on = None

_RUN_TRIGGER_NAMES = ("trg_report_runs_protect_identity", "trg_report_runs_terminal_immutable")
_DOC_TRIGGER_NAME = "trg_documents_no_resurrection"
_PG_FUNCTIONS = (
    "openjm_report_runs_protect_identity",
    "openjm_report_runs_terminal_immutable",
    "openjm_documents_no_resurrection",
)

_SQLITE = [
    """CREATE TRIGGER trg_report_runs_protect_identity
BEFORE UPDATE OF tenant_id, user_id, report_id, definition_id, definition_version,
requested_mode, idempotency_key, request_fingerprint, started_at, deadline_at
ON report_runs FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'report run identity fields are immutable')
    WHERE NEW.user_id IS NOT OLD.user_id
       OR NEW.tenant_id IS NOT OLD.tenant_id
       OR NEW.report_id IS NOT OLD.report_id
       OR NEW.definition_id IS NOT OLD.definition_id
       OR NEW.definition_version IS NOT OLD.definition_version
       OR NEW.requested_mode IS NOT OLD.requested_mode
       OR NEW.idempotency_key IS NOT OLD.idempotency_key
       OR NEW.request_fingerprint IS NOT OLD.request_fingerprint
       OR NEW.started_at IS NOT OLD.started_at
       OR NEW.deadline_at IS NOT OLD.deadline_at;
END;""",
    """CREATE TRIGGER trg_report_runs_terminal_immutable
BEFORE UPDATE ON report_runs FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'terminal report runs are immutable')
    WHERE OLD.status IN ('succeeded','failed','interrupted')
       OR (NEW.status != 'running' AND OLD.status != 'running');
    SELECT RAISE(ABORT, 'status regression is not permitted')
    WHERE OLD.status = 'succeeded' AND NEW.status IN ('running','failed','interrupted')
       OR OLD.status = 'failed' AND NEW.status IN ('running','succeeded','interrupted')
       OR OLD.status = 'interrupted' AND NEW.status IN ('running','succeeded','failed');
END;""",
    """CREATE TRIGGER trg_documents_no_resurrection
BEFORE UPDATE OF lifecycle_state ON documents FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'a deleted document cannot be resurrected')
    WHERE OLD.lifecycle_state = 'deleted' AND NEW.lifecycle_state != 'deleted';
END;""",
]

_PG_FUNCTIONS_SQL = [
    """CREATE OR REPLACE FUNCTION openjm_report_runs_protect_identity() RETURNS trigger AS $$
BEGIN
    IF NEW.user_id IS DISTINCT FROM OLD.user_id
       OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
       OR NEW.report_id IS DISTINCT FROM OLD.report_id
       OR NEW.definition_id IS DISTINCT FROM OLD.definition_id
       OR NEW.definition_version IS DISTINCT FROM OLD.definition_version
       OR NEW.requested_mode IS DISTINCT FROM OLD.requested_mode
       OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
       OR NEW.request_fingerprint IS DISTINCT FROM OLD.request_fingerprint
       OR NEW.started_at IS DISTINCT FROM OLD.started_at
       OR NEW.deadline_at IS DISTINCT FROM OLD.deadline_at THEN
        RAISE EXCEPTION 'report run identity fields are immutable';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;""",
    """CREATE OR REPLACE FUNCTION openjm_report_runs_terminal_immutable() RETURNS trigger AS $$
BEGIN
    IF OLD.status IN ('succeeded','failed','interrupted')
       OR (NEW.status <> 'running' AND OLD.status <> 'running') THEN
        RAISE EXCEPTION 'terminal report runs are immutable';
    END IF;
    IF (OLD.status = 'succeeded' AND NEW.status IN ('running','failed','interrupted'))
       OR (OLD.status = 'failed' AND NEW.status IN ('running','succeeded','interrupted'))
       OR (OLD.status = 'interrupted' AND NEW.status IN ('running','succeeded','failed')) THEN
        RAISE EXCEPTION 'status regression is not permitted';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;""",
    """CREATE OR REPLACE FUNCTION openjm_documents_no_resurrection() RETURNS trigger AS $$
BEGIN
    IF OLD.lifecycle_state = 'deleted' AND NEW.lifecycle_state <> 'deleted' THEN
        RAISE EXCEPTION 'a deleted document cannot be resurrected';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;""",
]

_PG_TRIGGERS_SQL = [
    """CREATE TRIGGER trg_report_runs_protect_identity
BEFORE UPDATE ON report_runs FOR EACH ROW
EXECUTE FUNCTION openjm_report_runs_protect_identity();""",
    """CREATE TRIGGER trg_report_runs_terminal_immutable
BEFORE UPDATE ON report_runs FOR EACH ROW
EXECUTE FUNCTION openjm_report_runs_terminal_immutable();""",
    """CREATE TRIGGER trg_documents_no_resurrection
BEFORE UPDATE ON documents FOR EACH ROW
EXECUTE FUNCTION openjm_documents_no_resurrection();""",
]

_ALL_TRIGGERS = (*_RUN_TRIGGER_NAMES, _DOC_TRIGGER_NAME)
_TRIGGER_TABLE = {
    "trg_report_runs_protect_identity": "report_runs",
    "trg_report_runs_terminal_immutable": "report_runs",
    "trg_documents_no_resurrection": "documents",
}


def _drop_triggers(bind) -> None:
    """Drop the guards using dialect-correct syntax.

    SQLite drops a trigger by name; PostgreSQL requires the owning table.
    """
    for name in _ALL_TRIGGERS:
        if bind.dialect.name == "postgresql":
            bind.exec_driver_sql(
                f"DROP TRIGGER IF EXISTS {name} ON {_TRIGGER_TABLE[name]}"
            )
        else:
            bind.exec_driver_sql(f"DROP TRIGGER IF EXISTS {name}")


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    _drop_triggers(bind)

    if dialect == "sqlite":
        for statement in _SQLITE:
            bind.exec_driver_sql(statement)
    elif dialect == "postgresql":
        for statement in _PG_FUNCTIONS_SQL:
            bind.exec_driver_sql(statement)
        for statement in _PG_TRIGGERS_SQL:
            bind.exec_driver_sql(statement)
    else:  # pragma: no cover - unknown dialect keeps application-level guards only
        return


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    _drop_triggers(bind)
    if dialect == "postgresql":
        for function in _PG_FUNCTIONS:
            bind.exec_driver_sql(f"DROP FUNCTION IF EXISTS {function}()")
