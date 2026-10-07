"""#46 M1 immutable LLM usage metering.

Revision ID: 0011_usage_metering
Revises: 0010_bv1c_data_classification
Create Date: 2026-10-07

Adds ``model_usage_events``: one append-only row per model invocation. Additive
and idempotent; no existing table is altered and no row is rewritten.

On SQLite an immutability trigger blocks UPDATE of a finalized usage row, so a
correction must be a new adjustment row. The application layer is append-only on
every dialect; the trigger is the database-level guard where the dialect
supports one.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.migrations_util import has_table

revision = "0011_usage_metering"
down_revision = "0010_bv1c_data_classification"
branch_labels = None
depends_on = None

_TRIGGER_NAME = "trg_model_usage_events_immutable"
_TRIGGER_SQL = f"""CREATE TRIGGER {_TRIGGER_NAME}
BEFORE UPDATE ON model_usage_events FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'model usage rows are immutable');
END;"""


def upgrade() -> None:
    bind = op.get_bind()
    if not has_table(bind, "model_usage_events"):
        op.create_table(
            "model_usage_events",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("tenant_id", sa.String(36), nullable=False, server_default="tnt-local"),
            sa.Column("principal_id", sa.String(128), nullable=True),
            sa.Column("request_id", sa.String(64), nullable=False),
            sa.Column("conversation_id", sa.String(36), nullable=True),
            sa.Column("message_id", sa.String(36), nullable=True),
            sa.Column("execution_class", sa.String(32), nullable=True),
            sa.Column("provider_route", sa.String(32), nullable=False),
            sa.Column("model_name", sa.String(240), nullable=False),
            sa.Column("usage_source", sa.String(32), nullable=False),
            sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("cached_input_tokens", sa.Integer(), nullable=True),
            sa.Column("reasoning_tokens", sa.Integer(), nullable=True),
            sa.Column("call_role", sa.String(16), nullable=False, server_default="primary"),
            sa.Column("parent_call_id", sa.String(36), nullable=True),
            sa.Column("idempotency_key", sa.String(120), nullable=False),
            sa.Column("status", sa.String(16), nullable=False),
            sa.Column("failure_category", sa.String(64), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("idempotency_key", name="uq_model_usage_idempotency"),
            sa.CheckConstraint(
                "usage_source IN ('provider_reported','estimated')",
                name="ck_model_usage_source",
            ),
            sa.CheckConstraint(
                "call_role IN ('primary','retry','fallback')",
                name="ck_model_usage_call_role",
            ),
            sa.CheckConstraint("status IN ('succeeded','failed')", name="ck_model_usage_status"),
            sa.CheckConstraint(
                "input_tokens >= 0 AND output_tokens >= 0 AND total_tokens >= 0",
                name="ck_model_usage_tokens",
            ),
        )
        op.create_index("ix_model_usage_events_tenant_id", "model_usage_events", ["tenant_id"])
        op.create_index(
            "ix_model_usage_events_principal_id", "model_usage_events", ["principal_id"]
        )
        op.create_index(
            "ix_model_usage_events_request_id", "model_usage_events", ["request_id"]
        )

    if bind.dialect.name == "sqlite":
        # DROP + CREATE so a corrected body replaces any earlier release.
        op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER_NAME}")
        op.execute(_TRIGGER_SQL)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER_NAME}")
    if has_table(bind, "model_usage_events"):
        op.drop_table("model_usage_events")
