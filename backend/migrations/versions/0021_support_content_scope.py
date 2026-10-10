"""Lane K hardening: bounded content scope on support delegations.

Revision ID: 0021_support_content_scope
Revises: 0019_inf1c_capacity
Create Date: 2026-10-09

Adds, additively and idempotently, the three columns a delegable *bounded*
support-content scope needs on ``support_delegations``:

* ``classification_ceiling`` (default ``internal``) — the most classified source
  a ``content`` delegation may reach, drawn from the BV1 classification
  vocabulary;
* ``allowed_group_ids_json`` (default ``[]``) — the explicit group allow-list the
  delegation is confined to;
* ``department_id`` (nullable) — the owning department the delegation is
  confined to.

No existing row is rewritten or deleted and no column is dropped. Every step is
guarded so a database that already carries these columns upgrades cleanly. The
DTO check that constrains ``classification_ceiling`` to the known vocabulary
lives in the ORM model; a SQLite ``ALTER TABLE ADD COLUMN`` cannot attach a CHECK
constraint, so the column carries the safe server default here and the value is
validated on the write path in the application.

Branch note: this revision is based on the accepted post-INF1-C main, so it
follows ``0019_inf1c_capacity``. ``0020_bv6_report_curation`` belongs to PR #71;
whichever of 0020/0021 merges second repoints its ``down_revision`` so the chain
keeps exactly one head.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.core.governance import DEFAULT_SOURCE_CLASSIFICATION
from app.migrations_util import add_column_if_missing, has_table

revision = "0021_support_content_scope"
down_revision = "0020_bv6_report_curation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if not has_table(bind, "support_delegations"):
        return

    add_column_if_missing(
        bind,
        "support_delegations",
        sa.Column(
            "classification_ceiling",
            sa.String(32),
            nullable=False,
            server_default=DEFAULT_SOURCE_CLASSIFICATION,
        ),
    )
    add_column_if_missing(
        bind,
        "support_delegations",
        sa.Column(
            "allowed_group_ids_json",
            sa.Text(),
            nullable=False,
            server_default="[]",
        ),
    )
    add_column_if_missing(
        bind,
        "support_delegations",
        sa.Column("department_id", sa.String(36), nullable=True),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if not has_table(bind, "support_delegations"):
        return
    from app.migrations_util import has_column

    columns = [
        column
        for column in (
            "department_id",
            "allowed_group_ids_json",
            "classification_ceiling",
        )
        if has_column(bind, "support_delegations", column)
    ]
    if columns:
        with op.batch_alter_table("support_delegations") as batch:
            for column in columns:
                batch.drop_column(column)
