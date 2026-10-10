"""BV6-A governed report curation for Saved Reports.

Revision ID: 0020_bv6_report_curation
Revises: 0017_inf1b_admission
Create Date: 2026-10-09

Additive and idempotent. Adds the curation columns to ``saved_reports`` and
nothing else: no existing table is created or dropped, no historical row is
rewritten, and no money/credit column is touched (this lane has none).

Curation state machine (one explicit machine, resolving the planning note's
``under_review`` inconsistency):

    none -> under_review -> approved -> authoritative
    any  -> (withdraw / auto-demote) -> none

``featured`` is a SEPARATE presentation/catalog flag; it never overwrites the
evidentiary ``curation_state``. Saving a report always yields ``none``/false.

Integration note
----------------
Down-revision is pinned to the LIVE head ``0017_inf1b_admission``. Revisions
``0018`` and ``0019`` are reserved by other open PRs and MUST NOT be claimed
here. At integration this revision is repointed onto the tail of the
``0018``/``0019`` chain (i.e. ``down_revision`` becomes the last of those
revisions once they land); the column set and semantics do not change.

Rollback notes: the downgrade drops only the columns this revision added. The
curation state is customer content; where the audit history matters, revert
executable code and leave the columns in place, matching the accepted plan.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.migrations_util import has_column, has_index, has_table

revision = "0020_bv6_report_curation"
down_revision = "0019_inf1c_capacity"
branch_labels = None
depends_on = None

_TABLE = "saved_reports"
_INDEX_NAME = "ix_saved_reports_curation_state"

# New columns: (name, sa.Column). NOT NULL columns carry a server default so the
# ALTER is valid against a table that already has rows.
_COLUMNS = (
    (
        "curation_state",
        sa.Column("curation_state", sa.String(16), nullable=False, server_default="none"),
    ),
    ("curation_reason", sa.Column("curation_reason", sa.String(240), nullable=True)),
    ("curation_audit_id", sa.Column("curation_audit_id", sa.String(36), nullable=True)),
    (
        "curation_department_id",
        sa.Column("curation_department_id", sa.String(36), nullable=True),
    ),
    (
        "curation_first_approver_id",
        sa.Column("curation_first_approver_id", sa.String(128), nullable=True),
    ),
    (
        "curation_second_approver_id",
        sa.Column("curation_second_approver_id", sa.String(128), nullable=True),
    ),
    (
        "curation_updated_at",
        sa.Column("curation_updated_at", sa.DateTime(timezone=True), nullable=True),
    ),
    (
        "featured",
        sa.Column("featured", sa.Boolean(), nullable=False, server_default="0"),
    ),
)


def upgrade() -> None:
    bind = op.get_bind()
    if not has_table(bind, _TABLE):
        return
    for name, column in _COLUMNS:
        if not has_column(bind, _TABLE, name):
            op.add_column(_TABLE, column)
    if not has_index(bind, _TABLE, _INDEX_NAME):
        op.create_index(_INDEX_NAME, _TABLE, ["curation_state"])


def downgrade() -> None:
    bind = op.get_bind()
    if not has_table(bind, _TABLE):
        return
    # Drop the index first: batch mode recreates the table and would otherwise
    # recreate an index on a column that is being dropped.
    if has_index(bind, _TABLE, _INDEX_NAME):
        op.drop_index(_INDEX_NAME, table_name=_TABLE)
    present = [name for name, _ in _COLUMNS if has_column(bind, _TABLE, name)]
    if present:
        with op.batch_alter_table(_TABLE) as batch:
            for name in present:
                batch.drop_column(name)
