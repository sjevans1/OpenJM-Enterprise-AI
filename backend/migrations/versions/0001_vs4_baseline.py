"""VS4 baseline schema.

Revision ID: 0001_vs4_baseline
Revises:
Create Date: 2026-10-06

Creates the accepted VS1-VS4 application-metadata schema exactly as it existed
when the migration framework was introduced.

The table definitions live in :mod:`app.migrations_schema` so that the adoption
path in ``app.migrations_runner`` can create an individually missing baseline
table (a deployment predating VS4-B2C1 has no ``report_runs``) without replaying
this whole revision and colliding with the tables that already exist.

Absolutely no data is moved or rewritten by this revision.
"""

from __future__ import annotations

from alembic import op

from app.migrations_schema import baseline_metadata

revision = "0001_vs4_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    baseline_metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    baseline_metadata.drop_all(bind=op.get_bind())
