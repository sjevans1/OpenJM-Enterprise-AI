"""BV3-C: widen the platform-operator capability vocabulary.

Revision ID: 0014_operations_admin_capability
Revises: 0013_tenant_preferences
Create Date: 2026-10-07

Adds ``platform:operations:admin`` to the ``platform_operators`` capability check
constraint. The 0008 revision keeps an immutable literal so history replays
identically, so the widening is an explicit, additive revision here.

The constraint is written as ``status = 'revoked' OR capability IN (...)``: an
active grant must always be inside the current vocabulary, while a revoked row is
history and may keep a value that a later narrowing retired. Without that clause
the downgrade is impossible, because revoking a row does not remove the capability
value it holds. A plain narrowed ``capability IN`` constraint fails with a check
violation while any row still holds the retired value. This was found by the
PostgreSQL deployment-acceptance run against the original revision on head
``594b036``.

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

_ORIGINAL_ACTIVE = (
    "'platform:metadata:read','platform:tenants:admin',"
    "'platform:operators:admin','platform:content:support'"
)

_WIDENED_ACTIVE = (
    "'platform:metadata:read','platform:tenants:admin',"
    "'platform:operators:admin','platform:content:support',"
    "'platform:operations:admin'"
)


def _check(active_vocabulary: str) -> str:
    """Active grants are bounded by the vocabulary; revoked rows are history."""
    return f"status = 'revoked' OR capability IN ({active_vocabulary})"


def upgrade() -> None:
    bind = op.get_bind()
    if not has_table(bind, "platform_operators"):
        return
    with op.batch_alter_table("platform_operators") as batch:
        batch.drop_constraint(_CONSTRAINT, type_="check")
        batch.create_check_constraint(_CONSTRAINT, _check(_WIDENED_ACTIVE))


def downgrade() -> None:
    bind = op.get_bind()
    if not has_table(bind, "platform_operators"):
        return
    # Rows holding a capability this revision introduced are revoked, never
    # deleted, so the history of who held what survives the downgrade.
    bind.exec_driver_sql(
        "UPDATE platform_operators SET status = 'revoked', revoked_at = CURRENT_TIMESTAMP "
        "WHERE capability = 'platform:operations:admin'"
    )
    with op.batch_alter_table("platform_operators") as batch:
        batch.drop_constraint(_CONSTRAINT, type_="check")
        batch.create_check_constraint(_CONSTRAINT, _check(_ORIGINAL_ACTIVE))
