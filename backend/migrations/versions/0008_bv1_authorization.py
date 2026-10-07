"""BV1-A authorization and information-governance foundation.

Revision ID: 0008_bv1_authorization
Revises: 0007_vs7_connectors
Create Date: 2026-10-07

Adds, additively and idempotently:

* ``departments`` and ``access_groups`` (tenant-scoped client grouping);
* ``group_memberships`` (principal-in-group, tenant-scoped);
* ``platform_operators`` (explicit platform capabilities, a separate axis from
  tenant roles);
* ``data_stewards`` (delegated stewardship over a tenant/department/group scope).

No existing table is altered, no row is rewritten and no data is moved. Every
step is guarded so a database already carrying these tables (for example one
upgraded in place by a build that created them from the ORM metadata) upgrades
cleanly instead of aborting on a duplicate object.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.migrations_util import has_table

revision = "0008_bv1_authorization"
down_revision = "0007_vs7_connectors"
branch_labels = None
depends_on = None

# Kept as a literal, immutable list so this revision never changes behaviour if
# the application's capability vocabulary grows: history must replay identically.
_CAPABILITY_CHECK = (
    "capability IN ('platform:metadata:read','platform:tenants:admin',"
    "'platform:operators:admin','platform:content:support')"
)


def upgrade() -> None:
    bind = op.get_bind()
    _create_departments(bind)
    _create_access_groups(bind)
    _create_group_memberships(bind)
    _create_platform_operators(bind)
    _create_data_stewards(bind)


def _create_departments(bind) -> None:
    if has_table(bind, "departments"):
        return
    op.create_table(
        "departments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("slug", sa.String(120), nullable=False),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "slug", name="uq_department_tenant_slug"),
        sa.CheckConstraint("status IN ('active','archived')", name="ck_department_status"),
    )
    op.create_index("ix_departments_tenant_id", "departments", ["tenant_id"])


def _create_access_groups(bind) -> None:
    if has_table(bind, "access_groups"):
        return
    op.create_table(
        "access_groups",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "department_id",
            sa.String(36),
            sa.ForeignKey("departments.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("slug", sa.String(120), nullable=False),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "slug", name="uq_access_group_tenant_slug"),
        sa.CheckConstraint("status IN ('active','archived')", name="ck_access_group_status"),
    )
    op.create_index("ix_access_groups_tenant_id", "access_groups", ["tenant_id"])
    op.create_index("ix_access_groups_department_id", "access_groups", ["department_id"])


def _create_group_memberships(bind) -> None:
    if has_table(bind, "group_memberships"):
        return
    op.create_table(
        "group_memberships",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "group_id",
            sa.String(36),
            sa.ForeignKey("access_groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "principal_id",
            sa.String(36),
            sa.ForeignKey("principal_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "tenant_id", "group_id", "principal_id", name="uq_group_membership_principal"
        ),
        sa.CheckConstraint("status IN ('active','revoked')", name="ck_group_membership_status"),
    )
    op.create_index("ix_group_memberships_tenant_id", "group_memberships", ["tenant_id"])
    op.create_index("ix_group_memberships_group_id", "group_memberships", ["group_id"])
    op.create_index("ix_group_memberships_principal_id", "group_memberships", ["principal_id"])


def _create_platform_operators(bind) -> None:
    if has_table(bind, "platform_operators"):
        return
    op.create_table(
        "platform_operators",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "principal_id",
            sa.String(36),
            sa.ForeignKey("principal_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("capability", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("granted_by", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "principal_id", "capability", name="uq_platform_operator_capability"
        ),
        sa.CheckConstraint(_CAPABILITY_CHECK, name="ck_platform_operator_capability"),
        sa.CheckConstraint("status IN ('active','revoked')", name="ck_platform_operator_status"),
    )
    op.create_index("ix_platform_operators_principal_id", "platform_operators", ["principal_id"])


def _create_data_stewards(bind) -> None:
    if has_table(bind, "data_stewards"):
        return
    op.create_table(
        "data_stewards",
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
        sa.Column("scope_type", sa.String(32), nullable=False),
        sa.Column("scope_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("granted_by", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "tenant_id",
            "principal_id",
            "scope_type",
            "scope_id",
            name="uq_data_steward_scope",
        ),
        sa.CheckConstraint(
            "scope_type IN ('tenant','department','group')", name="ck_data_steward_scope_type"
        ),
        sa.CheckConstraint("status IN ('active','revoked')", name="ck_data_steward_status"),
    )
    op.create_index("ix_data_stewards_tenant_id", "data_stewards", ["tenant_id"])
    op.create_index("ix_data_stewards_principal_id", "data_stewards", ["principal_id"])
    op.create_index("ix_data_stewards_scope_id", "data_stewards", ["scope_id"])


def downgrade() -> None:
    op.drop_table("data_stewards")
    op.drop_table("platform_operators")
    op.drop_table("group_memberships")
    op.drop_table("access_groups")
    op.drop_table("departments")
