"""BV3-C: widen the platform-operator capability vocabulary.

Revision ID: 0014_operations_admin_capability
Revises: 0013_tenant_preferences
Create Date: 2026-10-07

Adds ``platform:operations:admin`` to the ``platform_operators`` capability check
constraint. The 0008 revision keeps an immutable literal so history replays
identically, so the widening is an explicit, additive revision here.

No row is rewritten and no data is moved: the constraint only widens what the
column may hold. On SQLite (which cannot alter a constraint in place) Alembic's
batch mode recreates the table and copies every row; on PostgreSQL the constraint
is dropped and recreated.
"""

from __future__ import annotations

from alembic import op

from app.migrations_util import has_table

revision = "0014_operations_admin_capability"
down_revision = "0013_tenant_preferences"
branch_labels = None
depends_on = None

_CONSTRAINT = "ck_platform_operator_capability"

_WIDENED = (
    "capability IN ('platform:metadata:read','platform:tenants:admin',"
    "'platform:operators:admin','platform:content:support',"
    "'platform:operations:admin')"
)

_NARROWED = (
    "capability IN ('platform:metadata:read','platform:tenants:admin',"
    "'platform:operators:admin','platform:content:support')"
)


def upgrade() -> None:
    bind = op.get_bind()
    if not has_table(bind, "platform_operators"):
        return
    with op.batch_alter_table("platform_operators") as batch:
        batch.drop_constraint(_CONSTRAINT, type_="check")
        batch.create_check_constraint(_CONSTRAINT, _WIDENED)


def downgrade() -> None:
    bind = op.get_bind()
    if not has_table(bind, "platform_operators"):
        return
    # Rows holding the removed capability would violate the narrowed constraint,
    # so they are revoked (kept, never deleted) before it is applied.
    bind.exec_driver_sql(
        "UPDATE platform_operators SET status = 'revoked' "
        "WHERE capability = 'platform:operations:admin'"
    )
    with op.batch_alter_table("platform_operators") as batch:
        batch.drop_constraint(_CONSTRAINT, type_="check")
        batch.create_check_constraint(_CONSTRAINT, _NARROWED)
