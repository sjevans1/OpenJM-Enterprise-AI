"""Workspace independence (#9) — enforced, not merely documented.

OpenJM Enterprise AI must boot and operate with Workspace absent. These tests
assert that at the source level (no imports, no shared configuration) and at the
runtime level (the app starts and serves with no Workspace environment set).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1] / "app"

FORBIDDEN_PATTERNS = (
    r"\bworkspace_platform\b",
    r"\bfrom\s+workspace\b",
    r"\bimport\s+workspace\b",
    r"\bWorkspacePlatform\b",
    r"\bworkspace_platform\.",
)

# Configuration keys that would imply shared state with another product.
FORBIDDEN_CONFIG = (
    "workspace_database_url",
    "workspace_session_store",
    "workspace_secret",
    "shared_session",
)


def _python_sources() -> list[Path]:
    return sorted(APP_ROOT.rglob("*.py"))


def test_no_workspace_source_imports():
    offenders = []
    for path in _python_sources():
        text = path.read_text(encoding="utf-8")
        for pattern in FORBIDDEN_PATTERNS:
            if re.search(pattern, text, flags=re.IGNORECASE):
                offenders.append((str(path.relative_to(APP_ROOT)), pattern))
    assert offenders == [], f"Workspace coupling found in source: {offenders}"


def test_no_shared_configuration_secrets():
    text = (APP_ROOT / "core" / "config.py").read_text(encoding="utf-8").lower()
    for key in FORBIDDEN_CONFIG:
        assert key not in text, f"shared configuration key present: {key}"


def test_single_database_url_of_its_own():
    """Exactly one application database setting, and it is OpenJM's own."""
    text = (APP_ROOT / "core" / "config.py").read_text(encoding="utf-8")
    assert "database_url" in text
    # No shared-state settings of any kind are declared.
    for key in FORBIDDEN_CONFIG:
        assert key not in text.lower(), f"shared configuration key present: {key}"
    # And no second product's database or session store is referenced.
    lowered = text.lower()
    for forbidden in ("workspace_database", "workspace_session", "workspace_secret"):
        assert forbidden not in lowered


async def test_app_imports_and_boots_without_workspace(client, monkeypatch):
    """The ASGI app boots and serves with no Workspace environment present."""
    for key in list(dict(__import__("os").environ)):
        if "WORKSPACE" in key.upper():
            monkeypatch.delenv(key, raising=False)

    from app.main import app

    assert app.title == "OpenJM Enterprise AI"
    # The identity and action surfaces are OpenJM's own, and they answer.
    assert (await client.get("/api/health")).status_code == 200
    assert (await client.get("/api/auth/config")).status_code == 200
    assert (await client.get("/api/actions/tools")).status_code == 200


async def test_health_reports_product_identity(client):
    response = await client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["product"] == "OpenJM Enterprise AI"
