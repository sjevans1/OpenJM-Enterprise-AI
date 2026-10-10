"""REL1-A reproducible-deployment contract tests."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from app.version import PRODUCT_VERSION, RELEASE_TRAIN

ROOT = Path(__file__).resolve().parents[2]


def test_release_identity_is_consistent() -> None:
    with (ROOT / "backend" / "pyproject.toml").open("rb") as handle:
        backend_version = tomllib.load(handle)["project"]["version"]
    package = json.loads((ROOT / "frontend" / "package.json").read_text())
    lock = json.loads((ROOT / "frontend" / "package-lock.json").read_text())

    assert PRODUCT_VERSION == "0.2.0"
    assert RELEASE_TRAIN == "REL1"
    assert backend_version == PRODUCT_VERSION
    assert package["version"] == PRODUCT_VERSION
    assert lock["version"] == PRODUCT_VERSION
    assert lock["packages"][""]["version"] == PRODUCT_VERSION


def test_linux_installer_enforces_supported_toolchain_and_profile() -> None:
    script = (ROOT / "scripts" / "install-linux.sh").read_text()
    assert '[[ "$PROFILE" == "development" || "$PROFILE" == "production" ]]' in script
    assert '[[ "$NODE_MAJOR" -eq 22 ]]' in script
    assert 'python -m pip install --quiet --prefer-binary -e "."' in script
    assert 'python -m pip install --quiet --prefer-binary -e ".[dev]"' in script
    assert "verify-rel1a-install.py" in script
    assert "pip install --quiet --upgrade pip" not in script


def test_windows_bootstrap_propagates_profile_to_linux_installer() -> None:
    script = (ROOT / "scripts" / "install-wsl2.ps1").read_text()
    assert '[ValidateSet("development", "production")]' in script
    assert '[string]$Profile = "development"' in script
    assert '$linuxArgs = "--profile $Profile"' in script
    assert "install-linux.sh $linuxArgs" in script


def test_post_install_verifier_is_stdlib_only_and_static_gate_is_available() -> None:
    script = (ROOT / "scripts" / "verify-rel1a-install.py").read_text()
    assert "--static-only" in script
    assert "verify_release_identity()" in script
    assert "verify_toolchain(" in script
    # Prevent accidental dependency on the application environment before it is verified.
    assert not re.search(r"^from (fastapi|sqlalchemy|pydantic)\b", script, re.MULTILINE)


def test_alembic_uses_current_path_separator_setting() -> None:
    config = (ROOT / "backend" / "alembic.ini").read_text(encoding="utf-8")
    assert "path_separator = os" in config
    assert "version_path_separator" not in config
