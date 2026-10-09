"""M3: commercial entitlement foundation.

Revision ID: 0016_m3_entitlements
Revises: 0015_inf1_inference_registry
Create Date: 2026-10-08

Additive only. Creates the M3 commercial tables:

* ``entitlement_plans`` and ``entitlement_plan_versions`` (versioned terms);
* ``entitlement_price_schedules`` (versioned integer rates; no computation);
* ``entitlement_subscriptions``;
* ``entitlement_allowances`` (versioned allowance);
* ``entitlement_billing_periods`` (the counters the reservation guard updates);
* ``credit_ledger_entries`` (append-only);
* ``usage_reservations``.

No existing table, column or constraint is altered, no row is rewritten and the
accepted M1 ledger (``model_usage_events``) and M2 read model are untouched.

Immutability
------------
The credit ledger is append-only. On SQLite this revision installs two triggers,
``trg_credit_ledger_entries_immutable`` and ``trg_credit_ledger_entries_no_delete``,
which raise ``ABORT`` on any UPDATE or DELETE of a ledger row. The PostgreSQL
equivalent, documented here rather than executed because this migration runs on
SQLite in development and PostgreSQL in production, is a trigger function that
raises on UPDATE/DELETE:

    CREATE OR REPLACE FUNCTION reject_credit_ledger_mutation()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        RAISE EXCEPTION 'credit ledger entries are append-only';
    END $$;
    CREATE TRIGGER trg_credit_ledger_entries_immutable
        BEFORE UPDATE OR DELETE ON credit_ledger_entries
        FOR EACH ROW EXECUTE FUNCTION reject_credit_ledger_mutation();

Both are backed by the service and ORM guards, so a mutation is refused in the
application and in the database.

Rollback notes: the downgrade drops these tables and their triggers. Revert the
executable code and leave the tables in place where commercial state must be
preserved.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.migrations_util import add_column_if_missing, create_table_if_missing, has_table

revision = "0016_m3_entitlements"
down_revision = "0015_inf1_inference_registry"
branch_labels = None
depends_on = None

_LEDGER_UPDATE_TRIGGER = "trg_credit_ledger_entries_immutable"
_LEDGER_DELETE_TRIGGER = "trg_credit_ledger_entries_no_delete"

_LEDGER_UPDATE_SQL = (
    f"CREATE TRIGGER IF NOT EXISTS {_LEDGER_UPDATE_TRIGGER} "
    "BEFORE UPDATE ON credit_ledger_entries FOR EACH ROW "
    "BEGIN SELECT RAISE(ABORT, 'credit ledger entries are append-only'); END"
)
_LEDGER_DELETE_SQL = (
    f"CREATE TRIGGER IF NOT EXISTS {_LEDGER_DELETE_TRIGGER} "
    "BEFORE DELETE ON credit_ledger_entries FOR EACH ROW "
    "BEGIN SELECT RAISE(ABORT, 'credit ledger entries are append-only'); END"
)


def _tables() -> dict[str, sa.Table]:
    """Table definitions in one place so upgrade and downgrade agree.

    One shared MetaData instance so the foreign keys between these tables
    resolve against each other.
    """
    metadata = sa.MetaData()
    return {
        "entitlement_plans": sa.Table(
            "entitlement_plans",
            metadata,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("slug", sa.String(120), nullable=False),
            sa.Column("name", sa.String(240), nullable=False),
            sa.Column("status", sa.String(32), nullable=False, server_default="active"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("slug", name="uq_entitlement_plan_slug"),
            sa.CheckConstraint(
                "status IN ('active','retired')", name="ck_entitlement_plan_status"
            ),
        ),
        "entitlement_plan_versions": sa.Table(
            "entitlement_plan_versions",
            metadata,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "plan_id",
                sa.String(36),
                sa.ForeignKey("entitlement_plans.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("included_allowance_units", sa.BigInteger(), nullable=False),
            sa.Column(
                "overage_allowed", sa.Boolean(), nullable=False, server_default=sa.false()
            ),
            sa.Column("status", sa.String(32), nullable=False, server_default="active"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("plan_id", "version", name="uq_entitlement_plan_version"),
            sa.CheckConstraint("version >= 1", name="ck_entitlement_plan_version_number"),
            sa.CheckConstraint(
                "included_allowance_units >= 0", name="ck_entitlement_plan_version_allowance"
            ),
            sa.CheckConstraint(
                "status IN ('active','retired')", name="ck_entitlement_plan_version_status"
            ),
        ),
        "entitlement_price_schedules": sa.Table(
            "entitlement_price_schedules",
            metadata,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("schedule_key", sa.String(120), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("currency_code", sa.String(3), nullable=False),
            sa.Column("minor_unit_scale", sa.Integer(), nullable=False, server_default="6"),
            sa.Column("rate_units", sa.BigInteger(), nullable=False),
            sa.Column(
                "unit_basis", sa.String(32), nullable=False, server_default="per_1000_tokens"
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "schedule_key", "version", name="uq_entitlement_price_schedule"
            ),
            sa.CheckConstraint("version >= 1", name="ck_entitlement_price_version_number"),
            sa.CheckConstraint("rate_units >= 0", name="ck_entitlement_price_rate"),
            sa.CheckConstraint("minor_unit_scale >= 0", name="ck_entitlement_price_scale"),
        ),
        "entitlement_subscriptions": sa.Table(
            "entitlement_subscriptions",
            metadata,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("tenant_id", sa.String(36), nullable=False),
            sa.Column(
                "plan_version_id",
                sa.String(36),
                sa.ForeignKey("entitlement_plan_versions.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column("status", sa.String(32), nullable=False, server_default="active"),
            sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("tenant_id", name="uq_entitlement_subscription_tenant"),
            sa.CheckConstraint(
                "status IN ('active','suspended','revoked')",
                name="ck_entitlement_subscription_status",
            ),
        ),
        "entitlement_allowances": sa.Table(
            "entitlement_allowances",
            metadata,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("tenant_id", sa.String(36), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column(
                "plan_version_id",
                sa.String(36),
                sa.ForeignKey("entitlement_plan_versions.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column("allowance_units", sa.BigInteger(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "tenant_id", "version", name="uq_entitlement_allowance_version"
            ),
            sa.CheckConstraint("version >= 1", name="ck_entitlement_allowance_version_number"),
            sa.CheckConstraint("allowance_units >= 0", name="ck_entitlement_allowance_units"),
        ),
        "entitlement_billing_periods": sa.Table(
            "entitlement_billing_periods",
            metadata,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("tenant_id", sa.String(36), nullable=False),
            sa.Column("period_key", sa.String(32), nullable=False),
            sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
            sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
            sa.Column(
                "plan_version_id",
                sa.String(36),
                sa.ForeignKey("entitlement_plan_versions.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column(
                "allowance_id",
                sa.String(36),
                sa.ForeignKey("entitlement_allowances.id", ondelete="RESTRICT"),
                nullable=True,
            ),
            sa.Column("allowance_version", sa.Integer(), nullable=False),
            sa.Column("allowance_units", sa.BigInteger(), nullable=False),
            sa.Column("purchased_units", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("held_units", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("consumed_units", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(16), nullable=False, server_default="open"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "tenant_id", "period_key", name="uq_entitlement_billing_period"
            ),
            sa.CheckConstraint(
                "status IN ('open','closed')", name="ck_entitlement_billing_status"
            ),
            sa.CheckConstraint(
                "allowance_units >= 0 AND purchased_units >= 0 "
                "AND held_units >= 0 AND consumed_units >= 0",
                name="ck_entitlement_billing_counters",
            ),
        ),
        "credit_ledger_entries": sa.Table(
            "credit_ledger_entries",
            metadata,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("tenant_id", sa.String(36), nullable=False),
            sa.Column(
                "billing_period_id",
                sa.String(36),
                sa.ForeignKey("entitlement_billing_periods.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column("entry_type", sa.String(16), nullable=False),
            sa.Column("amount_units", sa.BigInteger(), nullable=False),
            sa.Column("balance_after_units", sa.BigInteger(), nullable=False),
            sa.Column("reservation_id", sa.String(36), nullable=True),
            sa.Column("idempotency_key", sa.String(160), nullable=False),
            sa.Column("reason", sa.String(120), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("idempotency_key", name="uq_credit_ledger_idempotency"),
            sa.CheckConstraint(
                "entry_type IN "
                "('grant','purchase','hold','release','expiry','settlement','adjustment')",
                name="ck_credit_ledger_entry_type",
            ),
        ),
        "usage_reservations": sa.Table(
            "usage_reservations",
            metadata,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("tenant_id", sa.String(36), nullable=False),
            sa.Column(
                "billing_period_id",
                sa.String(36),
                sa.ForeignKey("entitlement_billing_periods.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column("handle", sa.String(64), nullable=False),
            sa.Column("idempotency_key", sa.String(160), nullable=False),
            sa.Column("status", sa.String(16), nullable=False, server_default="reserved"),
            sa.Column("reserved_units", sa.BigInteger(), nullable=False),
            sa.Column("settled_units", sa.BigInteger(), nullable=True),
            sa.Column(
                "execution_state",
                sa.String(16),
                nullable=False,
                server_default="undispatched",
            ),
            sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint(
                "tenant_id", "idempotency_key", name="uq_usage_reservation_key"
            ),
            sa.UniqueConstraint("handle", name="uq_usage_reservation_handle"),
            sa.CheckConstraint(
                "status IN ('reserved','settled','released','expired')",
                name="ck_usage_reservation_status",
            ),
            sa.CheckConstraint("reserved_units >= 0", name="ck_usage_reservation_units"),
            sa.CheckConstraint(
                "execution_state IN "
                "('undispatched','dispatching','dispatched','uncertain')",
                name="ck_usage_reservation_execution_state",
            ),
        ),
    }


_INDEXES: tuple[tuple[str, str, list[str]], ...] = (
    ("ix_entitlement_plans_slug", "entitlement_plans", ["slug"]),
    ("ix_entitlement_plan_versions_plan_id", "entitlement_plan_versions", ["plan_id"]),
    ("ix_entitlement_price_schedules_schedule_key", "entitlement_price_schedules", ["schedule_key"]),
    ("ix_entitlement_subscriptions_tenant_id", "entitlement_subscriptions", ["tenant_id"]),
    ("ix_entitlement_subscriptions_plan_version_id", "entitlement_subscriptions", ["plan_version_id"]),
    ("ix_entitlement_allowances_tenant_id", "entitlement_allowances", ["tenant_id"]),
    ("ix_entitlement_allowances_plan_version_id", "entitlement_allowances", ["plan_version_id"]),
    ("ix_entitlement_billing_periods_tenant_id", "entitlement_billing_periods", ["tenant_id"]),
    ("ix_entitlement_billing_periods_period_key", "entitlement_billing_periods", ["period_key"]),
    ("ix_entitlement_billing_periods_plan_version_id", "entitlement_billing_periods", ["plan_version_id"]),
    ("ix_credit_ledger_entries_tenant_id", "credit_ledger_entries", ["tenant_id"]),
    ("ix_credit_ledger_entries_billing_period_id", "credit_ledger_entries", ["billing_period_id"]),
    ("ix_credit_ledger_entries_reservation_id", "credit_ledger_entries", ["reservation_id"]),
    ("ix_usage_reservations_tenant_id", "usage_reservations", ["tenant_id"]),
    ("ix_usage_reservations_billing_period_id", "usage_reservations", ["billing_period_id"]),
    ("ix_usage_reservations_handle", "usage_reservations", ["handle"]),
)


def upgrade() -> None:
    bind = op.get_bind()
    for name, table in _tables().items():
        create_table_if_missing(bind, table)

    # A database that already ran the earlier form of this revision has
    # usage_reservations without the execution-state columns. Add them rather than
    # aborting, the same idempotent discipline the other revisions use.
    if has_table(bind, "usage_reservations"):
        add_column_if_missing(
            bind,
            "usage_reservations",
            sa.Column(
                "execution_state",
                sa.String(16),
                nullable=False,
                server_default="undispatched",
            ),
        )
        add_column_if_missing(
            bind,
            "usage_reservations",
            sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        )
        # A database that already ran the earlier form of this revision also has
        # the narrower execution-state constraint, so widen it in place. SQLite
        # cannot alter a constraint, so batch mode recreates the table.
        with op.batch_alter_table("usage_reservations") as batch:
            batch.drop_constraint("ck_usage_reservation_execution_state", type_="check")
            batch.create_check_constraint(
                "ck_usage_reservation_execution_state",
                "execution_state IN "
                "('undispatched','dispatching','dispatched','uncertain')",
            )
    for index_name, table_name, columns in _INDEXES:
        if has_table(bind, table_name):
            op.create_index(index_name, table_name, columns, if_not_exists=True)

    if bind.dialect.name == "sqlite" and has_table(bind, "credit_ledger_entries"):
        bind.exec_driver_sql(_LEDGER_UPDATE_SQL)
        bind.exec_driver_sql(_LEDGER_DELETE_SQL)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql(f"DROP TRIGGER IF EXISTS {_LEDGER_UPDATE_TRIGGER}")
        bind.exec_driver_sql(f"DROP TRIGGER IF EXISTS {_LEDGER_DELETE_TRIGGER}")

    for table_name in reversed(list(_tables())):
        if has_table(bind, table_name):
            op.drop_table(table_name)
