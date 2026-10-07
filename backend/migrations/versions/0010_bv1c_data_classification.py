"""BV1-C governed structured Data source classification.

Revision ID: 0010_bv1c_data_classification
Revises: 0009_bv1b_classification
Create Date: 2026-10-07

Adds the same policy columns to ``data_sources`` that BV1-B added to
``documents``: classification (default ``internal``), department_id,
tenant_visible and allowed_group_ids_json. Additive, idempotent per step and
rollback-aware; no existing row is rewritten or deleted.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.core.governance import DEFAULT_SOURCE_CLASSIFICATION
from app.migrations_util import has_column, has_index, has_table

revision = "0010_bv1c_data_classification"
down_revision = "0009_bv1b_classification"
branch_labels = None
depends_on = None

_INDEX_NAME = "ix_data_sources_department_id"


def upgrade() -> None:
    bind = op.get_bind()
    if not has_table(bind, "data_sources"):
        return

    if not has_column(bind, "data_sources", "classification"):
        op.add_column(
            "data_sources",
            sa.Column(
                "classification",
                sa.String(32),
                nullable=False,
                server_default=DEFAULT_SOURCE_CLASSIFICATION,
            ),
        )
    if not has_column(bind, "data_sources", "department_id"):
        op.add_column("data_sources", sa.Column("department_id", sa.String(36), nullable=True))
    if not has_column(bind, "data_sources", "tenant_visible"):
        op.add_column(
            "data_sources",
            sa.Column("tenant_visible", sa.Boolean(), nullable=False, server_default="1"),
        )
    if not has_column(bind, "data_sources", "allowed_group_ids_json"):
        op.add_column(
            "data_sources", sa.Column("allowed_group_ids_json", sa.Text(), nullable=True)
        )

    if not has_index(bind, "data_sources", _INDEX_NAME):
        op.create_index(_INDEX_NAME, "data_sources", ["department_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if not has_table(bind, "data_sources"):
        return
    if has_index(bind, "data_sources", _INDEX_NAME):
        op.drop_index(_INDEX_NAME, table_name="data_sources")
    columns = [
        column
        for column in (
            "allowed_group_ids_json",
            "tenant_visible",
            "department_id",
            "classification",
        )
        if has_column(bind, "data_sources", column)
    ]
    if columns:
        with op.batch_alter_table("data_sources") as batch:
            for column in columns:
                batch.drop_column(column)
