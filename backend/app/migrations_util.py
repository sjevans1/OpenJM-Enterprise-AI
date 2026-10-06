"""Idempotent helpers for schema revisions.

A deployment upgraded in place may already contain a table or a column that a
revision is about to add: the previous bootstrap ran ``create_all`` against the
*current* ORM metadata on every startup, so an operator who ran that build
already has the new tables and columns. Revisions must therefore be safe to
apply to such a database instead of aborting on a duplicate object.

These helpers make each step a no-op when the object is already present, which
is what allows adoption and repeated startup to be safe by construction.
"""

from __future__ import annotations

import sqlalchemy as sa

__all__ = [
    "add_column_if_missing",
    "create_index_if_missing",
    "create_table_if_missing",
    "has_column",
    "has_index",
    "has_table",
    "table_names",
]


def table_names(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def has_table(bind, name: str) -> bool:
    return sa.inspect(bind).has_table(name)


def has_column(bind, table: str, column: str) -> bool:
    if not has_table(bind, table):
        return False
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def has_index(bind, table: str, name: str) -> bool:
    if not has_table(bind, table):
        return False
    return name in {i["name"] for i in sa.inspect(bind).get_indexes(table)}


def create_table_if_missing(bind, table: sa.Table) -> bool:
    """Create ``table`` unless it already exists. Returns True when created."""
    if has_table(bind, table.name):
        return False
    table.create(bind=bind, checkfirst=True)
    return True


def add_column_if_missing(bind, table_name: str, column: sa.Column) -> bool:
    """Add ``column`` unless the table already has it. Returns True when added.

    Uses Alembic's ``op`` so the operation participates in the migration
    context (and in SQLite batch handling). Callers must supply a server default
    for any NOT NULL column, because SQLite requires one to add such a column to
    a table that already has rows.
    """
    if not has_table(bind, table_name):
        return False
    if has_column(bind, table_name, column.name):
        return False
    from alembic import op

    op.add_column(table_name, column)
    return True


def create_index_if_missing(bind, index: sa.Index, table_name: str) -> bool:
    if has_index(bind, table_name, index.name or ""):
        return False
    if not has_table(bind, table_name):
        return False
    index.create(bind=bind, checkfirst=True)
    return True
