"""INF1-B: admission, capacity leases and bounded queue.

Revision ID: 0017_inf1b_admission
Revises: 0015_inf1_inference_registry
Create Date: 2026-10-08

Additive only. Creates four tables and nothing else:

* ``capacity_pools`` registered shared or dedicated capacity with an enforced
  owner split;
* ``capacity_scopes`` the one atomic in-flight counter per tenant, deployment
  and pool, plus the monotonic fence counter for each scope;
* ``capacity_leases`` fenced grants of a slot to one attempt;
* ``admission_tickets`` durable admission state for the bounded queue,
  reauthorization after waiting and crash recovery.

No existing table is altered, no column is added elsewhere and no historical row
is rewritten. The M1 usage ledger and the INF1-A registry and attribution tables
are untouched.

Rollback notes: the downgrade drops only these four tables. That is safe for a
deployment that has not admitted inference; where capacity evidence must be
preserved, revert executable code and leave the tables in place, as the accepted
plan requires.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.migrations_util import create_table_if_missing, has_table

revision = "0017_inf1b_admission"
down_revision = "0016_m3_entitlements"
branch_labels = None
depends_on = None

ADMISSION_STATES = (
    "queued",
    "admitted",
    "dispatched",
    "released",
    "rejected",
    "expired",
    "uncertain",
)
LEASE_STATES = ("active", "released", "expired", "uncertain")
POOL_KINDS = ("shared", "dedicated")
RESERVATION_STATES = ("present", "expired", "released", "unknown")


def _sql(values: tuple[str, ...]) -> str:
    return "(" + ",".join(f"'{value}'" for value in values) + ")"


def _tables() -> dict[str, sa.Table]:
    """One shared MetaData so upgrade and downgrade agree on the shape."""
    metadata = sa.MetaData()
    return {
        "capacity_pools": sa.Table(
            "capacity_pools",
            metadata,
            sa.Column("pool_id", sa.String(64), primary_key=True),
            sa.Column("pool_kind", sa.String(16), nullable=False),
            sa.Column("owner_tenant_id", sa.String(36), nullable=True),
            sa.Column("concurrency_limit", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint(
                f"pool_kind IN {_sql(POOL_KINDS)}", name="ck_capacity_pool_kind"
            ),
            sa.CheckConstraint(
                "(pool_kind = 'shared' AND owner_tenant_id IS NULL) "
                "OR (pool_kind = 'dedicated' AND owner_tenant_id IS NOT NULL)",
                name="ck_capacity_pool_owner_split",
            ),
            sa.CheckConstraint("concurrency_limit >= 1", name="ck_capacity_pool_limit"),
        ),
        "capacity_scopes": sa.Table(
            "capacity_scopes",
            metadata,
            sa.Column("scope_key", sa.String(200), primary_key=True),
            sa.Column("in_flight", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("concurrency_limit", sa.Integer(), nullable=False),
            sa.Column("next_fence", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint("in_flight >= 0", name="ck_capacity_scope_in_flight"),
            sa.CheckConstraint("concurrency_limit >= 1", name="ck_capacity_scope_limit"),
            sa.CheckConstraint("next_fence >= 1", name="ck_capacity_scope_fence"),
        ),
        "capacity_leases": sa.Table(
            "capacity_leases",
            metadata,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("tenant_id", sa.String(36), nullable=False),
            sa.Column("deployment_id", sa.String(64), nullable=True),
            sa.Column("pool_id", sa.String(64), nullable=True),
            sa.Column("attempt_id", sa.String(64), nullable=False),
            sa.Column("lease_token", sa.String(64), nullable=False),
            sa.Column("fence", sa.Integer(), nullable=False),
            sa.Column("state", sa.String(16), nullable=False, server_default="active"),
            sa.Column(
                "execution_certainty",
                sa.String(16),
                nullable=False,
                server_default="not_dispatched",
            ),
            sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("lease_token", name="uq_capacity_lease_token"),
            sa.CheckConstraint(f"state IN {_sql(LEASE_STATES)}", name="ck_capacity_lease_state"),
            sa.CheckConstraint("fence >= 1", name="ck_capacity_lease_fence"),
        ),
        "admission_tickets": sa.Table(
            "admission_tickets",
            metadata,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("tenant_id", sa.String(36), nullable=False),
            sa.Column("attempt_id", sa.String(64), nullable=False),
            sa.Column("deployment_id", sa.String(64), nullable=True),
            sa.Column("pool_id", sa.String(64), nullable=True),
            sa.Column("inference_mode", sa.String(32), nullable=False),
            sa.Column("cache_namespace", sa.String(200), nullable=False),
            sa.Column("reservation_handle", sa.String(200), nullable=True),
            sa.Column("reservation_state", sa.String(16), nullable=False, server_default="present"),
            sa.Column("state", sa.String(16), nullable=False, server_default="queued"),
            sa.Column("enqueue_seq", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("lease_id", sa.String(36), nullable=True),
            sa.Column("wait_ms", sa.Integer(), nullable=True),
            sa.Column("retry_after_ms", sa.Integer(), nullable=True),
            sa.Column(
                "execution_certainty",
                sa.String(16),
                nullable=False,
                server_default="not_dispatched",
            ),
            sa.Column("failure_category", sa.String(32), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("tenant_id", "attempt_id", name="uq_admission_ticket_attempt"),
            sa.CheckConstraint(
                f"state IN {_sql(ADMISSION_STATES)}", name="ck_admission_ticket_state"
            ),
            sa.CheckConstraint(
                f"reservation_state IN {_sql(RESERVATION_STATES)}",
                name="ck_admission_ticket_reservation_state",
            ),
        ),
    }


_INDEXES: tuple[tuple[str, str, list[str]], ...] = (
    ("ix_capacity_pools_owner_tenant_id", "capacity_pools", ["owner_tenant_id"]),
    ("ix_capacity_leases_tenant_id", "capacity_leases", ["tenant_id"]),
    ("ix_capacity_leases_deployment_id", "capacity_leases", ["deployment_id"]),
    ("ix_capacity_leases_pool_id", "capacity_leases", ["pool_id"]),
    ("ix_capacity_leases_attempt_id", "capacity_leases", ["attempt_id"]),
    ("ix_capacity_leases_expires_at", "capacity_leases", ["expires_at"]),
    ("ix_admission_tickets_tenant_id", "admission_tickets", ["tenant_id"]),
    ("ix_admission_tickets_attempt_id", "admission_tickets", ["attempt_id"]),
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
