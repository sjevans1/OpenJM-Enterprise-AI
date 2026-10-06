"""VS6 bounded action runtime schema.

Revision ID: 0004_action_runtime
Revises: 0003_document_lifecycle
Create Date: 2026-10-06

Adds the plan / approval / execution tables that make every governed action
attributable to a tenant, principal, role, plan, approval, tool, argument set,
result and status. No table in this revision can be written by the model
directly: all writes go through the server-side runtime.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.migrations_util import has_table

revision = "0004_action_runtime"
down_revision = "0003_document_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if has_table(bind, 'action_plans'):
        return
    op.create_table(
        "action_plans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("principal_id", sa.String(128), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("conversation_id", sa.String(36), nullable=True),
        sa.Column("goal_text", sa.Text(), nullable=False),
        sa.Column("steps_json", sa.Text(), nullable=False),
        sa.Column("max_steps", sa.Integer(), nullable=False),
        sa.Column("step_count", sa.Integer(), nullable=False),
        sa.Column("budget_seconds", sa.Integer(), nullable=False),
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(16), nullable=False, server_default="proposed"),
        sa.Column("plan_fingerprint", sa.String(64), nullable=False),
        sa.Column("permissions_fingerprint", sa.String(64), nullable=False),
        sa.Column("planner_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('proposed','approved','executing','succeeded','failed','rejected','expired')",
            name="ck_action_plan_status",
        ),
    )
    op.create_index("ix_action_plans_tenant_id", "action_plans", ["tenant_id"])
    op.create_index("ix_action_plans_principal_id", "action_plans", ["principal_id"])

    op.create_table(
        "action_approvals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column(
            "plan_id",
            sa.String(36),
            sa.ForeignKey("action_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("tool_name", sa.String(120), nullable=False),
        sa.Column("action_fingerprint", sa.String(64), nullable=False),
        sa.Column("requested_by", sa.String(128), nullable=False),
        sa.Column("decided_by", sa.String(128), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("reason", sa.String(240), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending','approved','rejected','consumed','expired')",
            name="ck_action_approval_status",
        ),
    )
    op.create_index("ix_action_approvals_tenant_id", "action_approvals", ["tenant_id"])
    op.create_index("ix_action_approvals_plan_id", "action_approvals", ["plan_id"])
    op.create_index(
        "ix_action_approvals_action_fingerprint", "action_approvals", ["action_fingerprint"]
    )

    op.create_table(
        "action_executions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column(
            "plan_id",
            sa.String(36),
            sa.ForeignKey("action_plans.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("step_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("principal_id", sa.String(128), nullable=False),
        sa.Column("role", sa.String(32), nullable=True),
        sa.Column("tool_name", sa.String(120), nullable=False),
        sa.Column("operation_class", sa.String(16), nullable=False),
        sa.Column("risk_level", sa.String(16), nullable=False),
        sa.Column("arguments_json", sa.Text(), nullable=False),
        sa.Column("arguments_hash", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("approval_id", sa.String(36), nullable=True),
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(16), nullable=False, server_default="started"),
        sa.Column("failure_category", sa.String(48), nullable=True),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_action_execution_idempotency"
        ),
        sa.CheckConstraint(
            "status IN ('started','succeeded','failed','refused','dry_run')",
            name="ck_action_execution_status",
        ),
    )
    op.create_index("ix_action_executions_tenant_id", "action_executions", ["tenant_id"])
    op.create_index("ix_action_executions_plan_id", "action_executions", ["plan_id"])
    op.create_index("ix_action_executions_principal_id", "action_executions", ["principal_id"])
    op.create_index("ix_action_executions_tool_name", "action_executions", ["tool_name"])


def downgrade() -> None:
    op.drop_table("action_executions")
    op.drop_table("action_approvals")
    op.drop_table("action_plans")
