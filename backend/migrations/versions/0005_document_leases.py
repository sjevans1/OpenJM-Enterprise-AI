"""Cross-process document leases (#6).

Revision ID: 0005_document_leases
Revises: 0004_action_runtime
Create Date: 2026-10-06

A single row per document implements a compare-and-swap lease. The acquire
statement is a conditional UPDATE (or a first INSERT), which both SQLite and
PostgreSQL evaluate atomically, so two processes racing for the same document
cannot both win. In-process locks are not sufficient here: the ingestion worker
and the API request that deletes a document can live in different processes.

``document_leases`` is deliberately separate from ``documents`` so a held lease
never widens the row a reader has to scan.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_document_leases"
down_revision = "0004_action_runtime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_leases",
        sa.Column("document_id", sa.String(36), primary_key=True),
        sa.Column("holder_token", sa.String(36), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=True),
    )
    op.create_index("ix_document_leases_expires_at", "document_leases", ["expires_at"])


def downgrade() -> None:
    op.drop_table("document_leases")
