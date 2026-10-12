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
    # The committed hash-pinned locks are the dependency authority.
    assert 'LOCK_FILE="requirements.lock"' in script
    assert 'LOCK_FILE="requirements-dev.lock"' in script
    assert 'python -m pip install --quiet --require-hashes -r "$LOCK_FILE"' in script
    # The application source installs without re-resolving dependencies.
    assert 'python -m pip install --quiet --no-deps -e "."' in script
    assert '".[dev]"' not in script
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


def test_production_proxy_frontend_builder_uses_node_22() -> None:
    """The production Compose proxy image must build the frontend on Node 22.

    Issue #79 requires production to remain Python 3.11 / Node 22, and the
    accepted installer/toolchain contract enforces Node 22. The Compose proxy
    image builds the frontend that production actually ships, so a Node 20
    builder stage there would ship a frontend built on an unsupported toolchain
    while every other gate reported Node 22.

    Semantically scoped: any Node 22 major tag is accepted (for example
    ``node:22`` or ``node:22-bookworm-slim``); no exact minor or patch is pinned,
    because the production contract requires the major version only.
    """
    dockerfile = (ROOT / "deploy" / "compose" / "Dockerfile.proxy").read_text()

    frontend_stages = re.findall(
        r"^FROM\s+node:(\S+)\s+AS\s+frontend\s*$", dockerfile, re.MULTILINE
    )
    assert frontend_stages, "expected a frontend build stage: FROM node:<tag> AS frontend"
    for tag in frontend_stages:
        assert tag.startswith("22"), f"frontend builder must be Node 22, found node:{tag}"

    # Node 20 must not build the shipped frontend in any stage.
    assert not re.search(
        r"^FROM\s+node:20\S*\s+AS\s+frontend\s*$", dockerfile, re.MULTILINE
    ), "the frontend builder must not use Node 20"


def test_production_compose_backend_obeys_locked_release_contract() -> None:
    """The Compose backend image must install the same artifact as the release contract.

    The accepted REL1 production contract is: Python 3.11, the committed
    ``backend/requirements.lock`` as runtime dependency authority, hash-enforced
    installation, no ``[dev]`` extras, and the application as a NON-EDITABLE wheel
    installed with ``--no-deps`` so the runtime dependency graph is exactly the
    locked one and never re-resolved from open-ended ``pyproject.toml`` ranges.

    An editable source install in the Compose backend would let production run a
    different dependency graph from the artifact being qualified, which would make
    a production Compose/browser run indefensible as release evidence.
    """
    dockerfile = (ROOT / "deploy" / "compose" / "Dockerfile").read_text()

    # Runtime is Python 3.11.
    assert re.search(r"^FROM\s+python:3\.11", dockerfile, re.MULTILINE), (
        "the Compose backend image must use a Python 3.11 base"
    )

    # The committed production lock is the dependency authority, hash-enforced.
    assert "requirements.lock" in dockerfile
    assert "--require-hashes" in dockerfile
    assert "-r /opt/openjm/backend/requirements.lock" in dockerfile

    # Production never installs the development lock or dev extras.
    assert "requirements-dev.lock" not in dockerfile
    assert "[dev]" not in dockerfile

    # The application arrives as a wheel, installed non-editable and without
    # re-resolving dependencies.
    assert ".whl" in dockerfile, "the application must be installed from a wheel"
    assert "--no-deps" in dockerfile
    assert not re.search(r"pip install[^\n]*(-e|--editable)\s", dockerfile), (
        "the production image must not perform an editable application install"
    )
    # The runtime installs the application from the build stage's wheel path,
    # never from a source directory (a source-directory install is what
    # re-resolves the dependency graph).
    assert re.search(
        r"pip install[^\n]*--no-deps[^\n]*/opt/openjm/wheels/\*\.whl", dockerfile
    ), "the application wheel must be installed from the build stage with --no-deps"
    assert not re.search(
        r"pip install[^\n]*[\"'\s](/opt/openjm/backend|/build/backend)[\"'\s]", dockerfile
    ), "the runtime must not install the application from a source directory"
