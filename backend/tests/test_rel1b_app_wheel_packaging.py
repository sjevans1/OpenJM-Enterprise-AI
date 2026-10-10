"""REL1-B4: the application wheel must be self-migrating.

`app/migrations_runner.py` resolves the Alembic script directory and
`alembic.ini` relative to the installed package root (``Path(__file__).parents[1]``),
which is ``site-packages`` for a wheel install. The project's hatch wheel target
therefore force-includes the Alembic script directory and config so a
non-editable (production) deployment can migrate itself without a source tree.
"""

from __future__ import annotations

import re
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def test_migrations_runner_resolves_relative_to_installed_package() -> None:
    runner = (BACKEND_ROOT / "app" / "migrations_runner.py").read_text(encoding="utf-8")
    # The resolver keys off the installed package location, so the wheel must
    # carry migrations/ and alembic.ini at the package root.
    assert 'BACKEND_ROOT = Path(__file__).resolve().parents[1]' in runner
    assert 'BACKEND_ROOT / "migrations"' in runner


def test_wheel_target_force_includes_migrations_and_alembic_ini() -> None:
    text = (BACKEND_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "tool.hatch.build.targets.wheel.force-include" in text
    assert re.search(r'"migrations"\s*=\s*"migrations"', text), "migrations not force-included"
    assert re.search(r'"alembic\.ini"\s*=\s*"alembic\.ini"', text), "alembic.ini not force-included"


def test_alembic_ini_script_location_matches_force_included_dir() -> None:
    text = (BACKEND_ROOT / "alembic.ini").read_text(encoding="utf-8")
    assert re.search(r"^script_location\s*=\s*migrations\s*$", text, re.MULTILINE)
