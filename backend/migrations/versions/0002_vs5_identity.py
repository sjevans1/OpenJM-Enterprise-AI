"""VS5 identity, tenancy and audit schema.

Revision ID: 0002_vs5_identity
Revises: 0001_vs4_baseline
Create Date: 2026-10-06

Adds:

* the tenant / principal / membership / session / audit tables;
* a ``tenant_id`` column on every tenant-owned VS1-VS4 table.

Every existing row is adopted into ``LEGACY_TENANT_ID`` via the column's server
default. That is a pure additive backfill: no row is deleted, no value is
rewritten, and the previous owner (``user_id``) is left exactly as it was.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.core.tenancy import LEGACY_TENANT_ID, LEGACY_TENANT_NAME, LEGACY_TENANT_SLUG
from app.migrations_util import has_column, has_index, has_table

revision = "0002_vs5_identity"
down_revision = "0001_vs4_baseline"
branch_labels = None
depends_on = None

# Tables that gain a tenant scope, with the existing index that mirrors it.
_TENANT_OWNED = (
    "conversations",
    "documents",
    "data_sources",
    "execution_traces",
    "saved_reports",
    "report_definition_versions",
    "report_runs",
)


def upgrade() -> None:
    bind = op.get_bind()
    _add_tenant_scope(bind)
    _create_identity_tables(bind)
    _seed_local_tenant(bind)


def _add_tenant_scope(bind) -> None:
    """Add the tenant scope to every owned table, skipping what already exists.

    A database created by the previous bootstrap's ``create_all`` already has
    these columns, so this must be a no-op there rather than a duplicate-column
    error.
    """
    for table in _TENANT_OWNED:
        if not has_column(bind, table, "tenant_id"):
            op.add_column(
                table,
                sa.Column(
                    "tenant_id",
                    sa.String(36),
                    nullable=False,
                    server_default=LEGACY_TENANT_ID,
                ),
            )
        if has_table(bind, table) and not has_index(bind, table, f"ix_{table}_tenant_id"):
            op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])


def _create_identity_tables(bind) -> None:
    if has_table(bind, "tenants"):
        return
    op.create_table(
        "tenants",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("slug", sa.String(120), nullable=False),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_tenants_slug", "tenants", ["slug"], unique=True)

    op.create_table(
        "principal_accounts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("issuer", sa.String(255), nullable=True),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("display_name", sa.String(240), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_principal_accounts_subject", "principal_accounts", ["subject"], unique=True
    )

    op.create_table(
        "tenant_memberships",
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
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "tenant_id", "principal_id", name="uq_membership_tenant_principal"
        ),
        sa.CheckConstraint("status IN ('active','revoked')", name="ck_membership_status"),
        sa.CheckConstraint(
            "role IN ('viewer','editor','admin','owner')", name="ck_membership_role"
        ),
    )
    op.create_index("ix_tenant_memberships_tenant_id", "tenant_memberships", ["tenant_id"])
    op.create_index(
        "ix_tenant_memberships_principal_id", "tenant_memberships", ["principal_id"]
    )

    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column(
            "principal_id",
            sa.String(36),
            sa.ForeignKey("principal_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("auth_method", sa.String(24), nullable=False, server_default="oidc"),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_auth_sessions_token_hash", "auth_sessions", ["token_hash"], unique=True)
    op.create_index("ix_auth_sessions_principal_id", "auth_sessions", ["principal_id"])
    op.create_index("ix_auth_sessions_tenant_id", "auth_sessions", ["tenant_id"])

    op.create_table(
        "audit_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("principal_id", sa.String(128), nullable=True),
        sa.Column("role", sa.String(32), nullable=True),
        sa.Column("action", sa.String(120), nullable=False),
        sa.Column("resource_type", sa.String(64), nullable=True),
        sa.Column("resource_id", sa.String(128), nullable=True),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(240), nullable=True),
        sa.Column("auth_method", sa.String(24), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_records_tenant_id", "audit_records", ["tenant_id"])
    op.create_index("ix_audit_records_principal_id", "audit_records", ["principal_id"])
    op.create_index("ix_audit_records_action", "audit_records", ["action"])



def _seed_local_tenant(bind) -> None:
    """Adopt the local development tenant, exactly once."""
    if not has_table(bind, "tenants"):
        return
    existing = bind.execute(
        sa.text("SELECT id FROM tenants WHERE id = :id"), {"id": LEGACY_TENANT_ID}
    ).first()
    if existing is not None:
        return
    bind.execute(
        sa.text(
            "INSERT INTO tenants (id, slug, name, status, created_at) "
            "VALUES (:id, :slug, :name, 'active', :now)"
        ),
        {
            "id": LEGACY_TENANT_ID,
            "slug": LEGACY_TENANT_SLUG,
            "name": LEGACY_TENANT_NAME,
            "now": "1970-01-01 00:00:00.000000",
        },
    )


def downgrade() -> None:
    for table in reversed(_TENANT_OWNED):
        op.drop_index(f"ix_{table}_tenant_id", table_name=table)
        op.drop_column(table, "tenant_id")
    op.drop_table("audit_records")
    op.drop_table("auth_sessions")
    op.drop_table("tenant_memberships")
    op.drop_table("principal_accounts")
    op.drop_table("tenants")
