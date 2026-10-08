"""BV3-B: safe tenant preferences column.

Revision ID: 0013_tenant_preferences
Revises: 0012_support_delegations
Create Date: 2026-10-07

Additive: adds ``tenants.settings_json`` (default ``'{}'``). No existing column,
constraint or row is altered.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.migrations_util import add_column_if_missing, has_column

revision = "0013_tenant_preferences"
down_revision = "0012_support_delegations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    add_column_if_missing(
        bind,
        "tenants",
        sa.Column("settings_json", sa.Text(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if has_column(bind, "tenants", "settings_json"):
        op.drop_column("tenants", "settings_json")
