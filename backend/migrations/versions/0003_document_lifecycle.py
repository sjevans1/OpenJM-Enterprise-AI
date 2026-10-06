"""Document lifecycle state for cross-process concurrency (#6).

Revision ID: 0003_document_lifecycle
Revises: 0002_vs5_identity
Create Date: 2026-10-06

Adds the explicit lifecycle columns used by the hardened Knowledge lifecycle.
The legacy ``status``/``indexed`` columns are kept and remain in sync so every
existing VS1 acceptance surface keeps working unchanged.

Existing rows are classified deterministically:

* ``indexed = true``            -> ``ready``
* ``status = 'failed'``         -> ``failed``
* anything else                 -> ``indexing`` (an unfinished attempt)

Nothing is deleted and no document becomes retrievable that was not already.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.migrations_util import add_column_if_missing, has_index

revision = "0003_document_lifecycle"
down_revision = "0002_vs5_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    for column in (
        sa.Column("lifecycle_state", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("lifecycle_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ingest_token", sa.String(36), nullable=True),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    ):
        add_column_if_missing(bind, "documents", column)
    if not has_index(bind, "documents", "ix_documents_lifecycle_state"):
        op.create_index("ix_documents_lifecycle_state", "documents", ["lifecycle_state"])

    conn = op.get_bind()
    conn.execute(
        sa.text(
            "UPDATE documents SET lifecycle_state = 'ready' "
            "WHERE indexed = :truthy"
        ),
        {"truthy": True},
    )
    conn.execute(
        sa.text(
            "UPDATE documents SET lifecycle_state = 'failed' "
            "WHERE indexed = :falsy AND status = 'failed'"
        ),
        {"falsy": False},
    )
    conn.execute(
        sa.text(
            "UPDATE documents SET lifecycle_state = 'indexing' "
            "WHERE indexed = :falsy AND status <> 'failed'"
        ),
        {"falsy": False},
    )
    conn.execute(
        sa.text(
            "UPDATE documents SET indexed_at = created_at WHERE indexed = :truthy"
        ),
        {"truthy": True},
    )


def downgrade() -> None:
    op.drop_index("ix_documents_lifecycle_state", table_name="documents")
    for column in (
        "deleted_at",
        "indexed_at",
        "ingest_token",
        "lifecycle_version",
        "lifecycle_state",
    ):
        op.drop_column("documents", column)
