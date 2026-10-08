"""BV3-A: explicit, tenant-scoped OpenJM support delegations.

Revision ID: 0012_support_delegations
Revises: 0011_usage_metering
Create Date: 2026-10-07

Adds ``support_delegations``: one row per (tenant, operator, scope). Additive and
idempotent; no existing table is altered and no row is rewritten.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.migrations_util import has_table

revision = "0012_support_delegations"
down_revision = "0011_usage_metering"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if has_table(bind, "support_delegations"):
        return
    op.create_table(
        "support_delegations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "principal_id",
            sa.String(36),
            sa.ForeignKey("principal_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("granted_by", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "tenant_id", "principal_id", "scope", name="uq_support_delegation"
        ),
        sa.CheckConstraint(
            "scope IN ('metadata','content')", name="ck_support_delegation_scope"
        ),
        sa.CheckConstraint(
            "status IN ('active','revoked')", name="ck_support_delegation_status"
        ),
    )
    op.create_index(
        "ix_support_delegations_tenant_id", "support_delegations", ["tenant_id"]
    )
    op.create_index(
        "ix_support_delegations_principal_id", "support_delegations", ["principal_id"]
    )


def downgrade() -> None:
    bind = op.get_bind()
    if has_table(bind, "support_delegations"):
        op.drop_table("support_delegations")
