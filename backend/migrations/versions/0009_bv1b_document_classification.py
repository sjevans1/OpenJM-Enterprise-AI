"""BV1-B governed Knowledge source classification.

Revision ID: 0009_bv1b_document_classification
Revises: 0008_bv1_authorization
Create Date: 2026-10-07

Adds, additively and idempotently, the policy columns a Knowledge document
carries:

* ``classification`` (default ``internal``, which matches the accepted
  pre-BV1 behavior so existing rows do not change meaning);
* ``department_id`` (owning department/domain, nullable);
* ``tenant_visible`` (the tenant-wide fallback flag, default true);
* ``allowed_group_ids_json`` (explicit allow-list, nullable).

No existing row is rewritten or deleted and no column is dropped. Every step is
guarded so a database that already carries these columns upgrades cleanly.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.core.governance import DEFAULT_SOURCE_CLASSIFICATION
from app.migrations_util import has_column, has_index, has_table

revision = "0009_bv1b_document_classification"
down_revision = "0008_bv1_authorization"
branch_labels = None
depends_on = None

_INDEX_NAME = "ix_documents_department_id"


def upgrade() -> None:
    bind = op.get_bind()
    if not has_table(bind, "documents"):
        return

    if not has_column(bind, "documents", "classification"):
        op.add_column(
            "documents",
            sa.Column(
                "classification",
                sa.String(32),
                nullable=False,
                server_default=DEFAULT_SOURCE_CLASSIFICATION,
            ),
        )
    if not has_column(bind, "documents", "department_id"):
        # A plain column, not a FK: SQLite cannot ALTER-ADD a constraint, and the
        # owning department is validated in the application (set_document_policy)
        # rather than by the database.
        op.add_column(
            "documents", sa.Column("department_id", sa.String(36), nullable=True)
        )
    if not has_column(bind, "documents", "tenant_visible"):
        op.add_column(
            "documents",
            sa.Column("tenant_visible", sa.Boolean(), nullable=False, server_default="1"),
        )
    if not has_column(bind, "documents", "allowed_group_ids_json"):
        op.add_column(
            "documents", sa.Column("allowed_group_ids_json", sa.Text(), nullable=True)
        )

    if not has_index(bind, "documents", _INDEX_NAME):
        op.create_index(_INDEX_NAME, "documents", ["department_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if not has_table(bind, "documents"):
        return
    # Drop the department index first: batch mode recreates the table and would
    # otherwise re-create an index on a column that is being dropped.
    if has_index(bind, "documents", _INDEX_NAME):
        op.drop_index(_INDEX_NAME, table_name="documents")
    columns = [
        column
        for column in (
            "allowed_group_ids_json",
            "tenant_visible",
            "department_id",
            "classification",
        )
        if has_column(bind, "documents", column)
    ]
    if columns:
        with op.batch_alter_table("documents") as batch:
            for column in columns:
                batch.drop_column(column)
