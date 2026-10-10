"""BV6-A curation migration: head-agnostic, additive and reversible.

The head is NEVER pinned to a literal. The revision id is asserted to be *in*
the down-revision chain from the script head, so a later package that adds a
revision (or repoints 0020 onto the tail of the reserved 0018/0019 chain at
integration) does not break these tests.
"""

from __future__ import annotations

import sqlite3

from alembic import command
from sqlalchemy import create_engine, inspect

from app.migrations_runner import (
    _alembic_config,
    adopt_and_upgrade,
    current_revision,
    sync_url_for,
)
from test_migrations import LEGACY_ROWS

REVISION = "0020_bv6_report_curation"
LIVE_PARENT = "0017_inf1b_admission"

CURATION_COLUMNS = (
    "curation_state",
    "curation_reason",
    "curation_audit_id",
    "curation_department_id",
    "curation_first_approver_id",
    "curation_second_approver_id",
    "curation_updated_at",
    "featured",
)


def _revision_chain(url: str) -> set[str]:
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(_alembic_config(sync_url_for(url)))
    chain: set[str] = set()
    cursor = script.get_current_head()
    assert cursor is not None, "the migration script must have a head"
    while cursor:
        chain.add(cursor)
        revision = script.get_revision(cursor)
        cursor = revision.down_revision if revision else None
    return chain


def _saved_report_columns(path: str) -> set[str]:
    engine = create_engine(f"sqlite:///{path}")
    try:
        return {column["name"] for column in inspect(engine).get_columns("saved_reports")}
    finally:
        engine.dispose()


def test_revision_is_in_head_chain_and_columns_applied(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'curation.db'}"
    adopt_and_upgrade(url)

    head = current_revision(url)
    assert head is not None, "a migrated database must record a non-null head"
    chain = _revision_chain(url)
    assert head in chain
    # Head-agnostic membership: the revision and its live parent are BOTH in the
    # chain, but neither is asserted to be the literal head.
    assert REVISION in chain
    assert LIVE_PARENT in chain

    path = tmp_path / "curation.db"
    assert set(CURATION_COLUMNS) <= _saved_report_columns(str(path))


def test_upgrade_preserves_rows_and_defaults_uncurated(tmp_path):
    path = tmp_path / "legacy_curation.db"
    url = f"sqlite+aiosqlite:///{path}"
    command.upgrade(_alembic_config(sync_url_for(url)), "0001_vs4_baseline")
    connection = sqlite3.connect(path)
    connection.executescript(LEGACY_ROWS)
    connection.execute("DELETE FROM alembic_version")
    connection.commit()
    connection.close()

    adopt_and_upgrade(url)
    assert REVISION in _revision_chain(url)

    connection = sqlite3.connect(path)
    assert connection.execute(
        "SELECT answer_text FROM saved_reports WHERE id='rep1'"
    ).fetchone()[0] == "legacy answer"
    state, featured = connection.execute(
        "SELECT curation_state, featured FROM saved_reports WHERE id='rep1'"
    ).fetchone()
    connection.close()
    assert state == "none", "an adopted report must default to uncurated"
    assert not featured


def test_downgrade_drops_columns_and_reupgrade_restores(tmp_path):
    path = tmp_path / "roundtrip_curation.db"
    url = f"sqlite+aiosqlite:///{path}"
    config = _alembic_config(sync_url_for(url))
    adopt_and_upgrade(url)
    assert set(CURATION_COLUMNS) <= _saved_report_columns(str(path))

    command.downgrade(config, LIVE_PARENT)
    assert current_revision(url) == LIVE_PARENT
    assert not (set(CURATION_COLUMNS) & _saved_report_columns(str(path)))

    command.upgrade(config, "head")
    assert set(CURATION_COLUMNS) <= _saved_report_columns(str(path))
