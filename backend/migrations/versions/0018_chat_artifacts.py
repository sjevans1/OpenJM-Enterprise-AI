"""BV5-A: chat artifacts.

Revision ID: 0018_chat_artifacts
Revises: 0017_inf1b_admission
Create Date: 2026-10-09

Additive only: creates the ``chat_artifacts`` table and its indexes and nothing
else. No existing table, column or row is altered. A row stores METADATA for one
downloadable Chat work product; its content lives in the controlled artifact
store under an opaque, server-generated key (never a caller-supplied path), and
is deliberately SEPARATE from a Governed Saved Report.

Rollback notes: the downgrade drops only ``chat_artifacts``. Where artifact
evidence must be preserved, revert executable code and leave the table in place.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.migrations_util import create_table_if_missing, has_table

revision = "0018_chat_artifacts"
down_revision = "0017_inf1b_admission"
branch_labels = None
depends_on = None

STATES = ("active", "deleted", "revoked")
FORMATS = ("html", "markdown", "text", "csv")


def _sql(values: tuple[str, ...]) -> str:
    return "(" + ",".join(f"'{value}'" for value in values) + ")"


def _tables() -> dict[str, sa.Table]:
    """One shared MetaData so upgrade and downgrade agree on the shape.

    ``conversations`` and ``messages`` are present only so ``chat_artifacts``'
    foreign keys resolve; they are never created here because earlier revisions
    already own those tables.
    """
    metadata = sa.MetaData()
    sa.Table(
        "conversations", metadata, sa.Column("id", sa.String(36), primary_key=True)
    )
    sa.Table("messages", metadata, sa.Column("id", sa.String(36), primary_key=True))
    return {
        "chat_artifacts": sa.Table(
            "chat_artifacts",
            metadata,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("tenant_id", sa.String(36), nullable=False),
            sa.Column("user_id", sa.String(128), nullable=False),
            sa.Column("conversation_id", sa.String(36), nullable=True),
            sa.Column("message_id", sa.String(36), nullable=True),
            sa.Column("title", sa.String(240), nullable=False),
            sa.Column("filename", sa.String(240), nullable=False),
            sa.Column("mime_type", sa.String(128), nullable=False),
            sa.Column("artifact_format", sa.String(16), nullable=False),
            sa.Column("size_bytes", sa.BigInteger(), nullable=False),
            sa.Column("storage_key", sa.String(128), nullable=False),
            sa.Column("sha256", sa.String(64), nullable=True),
            sa.Column("state", sa.String(16), nullable=False, server_default="active"),
            sa.Column(
                "is_evidence_backed",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column("provenance_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(
                ["conversation_id"], ["conversations.id"], ondelete="SET NULL"
            ),
            sa.ForeignKeyConstraint(
                ["message_id"], ["messages.id"], ondelete="SET NULL"
            ),
            sa.UniqueConstraint("storage_key", name="uq_chat_artifact_storage_key"),
            sa.CheckConstraint(
                f"state IN {_sql(STATES)}", name="ck_chat_artifact_state"
            ),
            sa.CheckConstraint(
                f"artifact_format IN {_sql(FORMATS)}", name="ck_chat_artifact_format"
            ),
            sa.CheckConstraint("size_bytes >= 0", name="ck_chat_artifact_size"),
        )
    }


_INDEXES: tuple[tuple[str, str, list[str]], ...] = (
    ("ix_chat_artifacts_tenant_id", "chat_artifacts", ["tenant_id"]),
    ("ix_chat_artifacts_user_id", "chat_artifacts", ["user_id"]),
    ("ix_chat_artifacts_conversation_id", "chat_artifacts", ["conversation_id"]),
    ("ix_chat_artifacts_message_id", "chat_artifacts", ["message_id"]),
)


def upgrade() -> None:
    bind = op.get_bind()
    for name, table in _tables().items():
        create_table_if_missing(bind, table)
    for index_name, table_name, columns in _INDEXES:
        if has_table(bind, table_name):
            op.create_index(index_name, table_name, columns, if_not_exists=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table_name in reversed(list(_tables())):
        if has_table(bind, table_name):
            op.drop_table(table_name)
