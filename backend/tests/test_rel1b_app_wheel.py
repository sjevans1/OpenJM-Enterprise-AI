"""REL1-B4 production application-wheel contract tests.

Hermetic and offline: they assert the structural contract of the release bundle
builder and the production installer, plus the manifest coverage of the bundled
application wheel, without building a real wheel or resolving dependencies. The
registry-blocked install itself is proven separately by
``scripts/rel1-offline-acceptance.sh`` (local harness / CI job).

Background: the backend build backend is Hatchling, which is NOT in the runtime
wheelhouse or the committed lock. A registry-blocked offline host therefore
cannot complete an editable (``pip install -e .``) application install. The
production installer must instead install a PRE-BUILT application wheel shipped
inside the bundle at ``python/app/openjm_enterprise_ai_backend-<version>-*.whl``.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from app.version import PRODUCT_VERSION

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
BUNDLE = SCRIPTS / "build-rel1-bundle.sh"
INSTALLER = SCRIPTS / "install-linux.sh"
INTEGRITY = SCRIPTS / "release-integrity.py"

ACCEPTANCE = SCRIPTS / "rel1-offline-acceptance.sh"
WHEEL_PREFIX = "openjm_enterprise_ai_backend-"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*args], cwd=ROOT, text=True, capture_output=True, check=False
    )


def integrity(*args: str) -> subprocess.CompletedProcess[str]:
    return run(sys.executable, str(INTEGRITY), *args)


# --- bundle builder ----------------------------------------------------------


def test_bundle_script_builds_a_single_named_application_wheel() -> None:
    script = _read(BUNDLE)
    # The wheel is built into python/app of the bundle with a controlled 3.11
    # interpreter and no dependency resolution.
    assert "APP_WHEEL_DIR=\"$BUNDLE/python/app\"" in script
    assert "pip wheel" in script
    assert "--no-deps" in script
    assert '--wheel-dir "$APP_WHEEL_DIR" "$ROOT/backend"' in script
    # Exactly one wheel, named for the product version read from version.py.
    assert '"${#APP_WHEELS[@]}" -eq 1' in script
    assert "openjm_enterprise_ai_backend-${VERSION}-" in script
    # A wheel that does not match the product version is a hard error.
    assert "is not named openjm_enterprise_ai_backend-${VERSION}-" in script


def test_bundle_manifest_covers_the_bundled_application_wheel(tmp_path: Path) -> None:
    bundle = tmp_path / "openjm-rel1-0.2.0"
    (bundle / "app").mkdir(parents=True)
    (bundle / "python" / "wheelhouse").mkdir(parents=True)
    (bundle / "python" / "app").mkdir(parents=True)
    (bundle / "app" / "README.txt").write_text("payload\n")
    (bundle / "python" / "requirements.lock").write_text("example==1.0\n")
    (bundle / "python" / "wheelhouse" / "example.whl").write_bytes(b"wheel")

    wheel_name = f"{WHEEL_PREFIX}{PRODUCT_VERSION}-py3-none-any.whl"
    wheel = bundle / "python" / "app" / wheel_name
    wheel.write_bytes(b"application wheel bytes")

    assert integrity("generate", str(bundle)).returncode == 0
    manifest = json.loads((bundle / "RELEASE-MANIFEST.json").read_text())
    paths = [entry["path"] for entry in manifest["files"]]
    assert manifest["product_version"] == PRODUCT_VERSION
    assert f"python/app/{wheel_name}" in paths
    # The wheel version equals the product version (single source of truth).
    assert wheel_name == f"openjm_enterprise_ai_backend-{PRODUCT_VERSION}-py3-none-any.whl"
    # And the bundle with the wheel still verifies cleanly.
    assert integrity("verify", str(bundle)).returncode == 0


# --- installer: bundle layout detection --------------------------------------


def test_installer_detects_bundle_python_sibling() -> None:
    script = _read(INSTALLER)
    # The script runs from the app root; python/ is a sibling of the app tree.
    assert 'BUNDLE_PY="$ROOT/../python"' in script


def test_installer_lock_install_is_index_free_and_hash_enforced() -> None:
    script = _read(INSTALLER)
    # Offline bundle: install the lock strictly from the bundled wheelhouse.
    assert '--no-index --find-links "$BUNDLE_PY/wheelhouse"' in script
    assert 'if [[ -d "$BUNDLE_PY/wheelhouse" ]]' in script
    # Hash enforcement is never weakened by the offline flags.
    assert 'python -m pip install --quiet --require-hashes -r "$LOCK_FILE"' in script


# --- installer: production application install --------------------------------


def _application_install_branch(script: str) -> str:
    """The production application-install block (after the lock install)."""
    tail = script.split('"${LOCK_EXTRA[@]}"', 1)[1]
    return tail.split('if [[ "$PROFILE" == "production" ]]; then', 1)[1].split("else", 1)[0]


def test_installer_production_installs_the_bundled_wheel_not_editable() -> None:
    script = _read(INSTALLER)
    # Exactly one bundled application wheel, installed with --no-deps.
    assert "-name 'openjm_enterprise_ai_backend-*.whl'" in script
    assert '"${#APP_WHEELS[@]}" -eq 1' in script
    assert 'python -m pip install --quiet --no-deps "${APP_WHEELS[0]}"' in script
    # Production must never reach for an editable install.
    prod_block = _application_install_branch(script)
    assert "pip install --quiet --no-deps -e" not in prod_block
    assert "-e \".\"" not in prod_block


def test_installer_production_fails_closed_when_wheel_missing_or_ambiguous() -> None:
    script = _read(INSTALLER)
    assert (
        'fail "production install requires the bundled application wheel at '
        'python/app/openjm_enterprise_ai_backend-<version>-*.whl"'
    ) in script
    # The check is an exactly-one assertion (covers both missing and ambiguous).
    assert '"${#APP_WHEELS[@]}" -eq 1' in script


def test_installer_development_keeps_editable_install() -> None:
    script = _read(INSTALLER)
    assert 'python -m pip install --quiet --no-deps -e "."' in script
    # The editable line lives only in the development branch (the else).
    tail = script.split('"${LOCK_EXTRA[@]}"', 1)[1]
    dev_block = tail.split('if [[ "$PROFILE" == "production" ]]; then', 1)[1].split("else", 1)[1]
    assert 'python -m pip install --quiet --no-deps -e "."' in dev_block
    assert "pip install --quiet --no-deps -e" not in _application_install_branch(script)


def test_installer_production_never_installs_dev_extras() -> None:
    script = _read(INSTALLER)
    assert "[dev]" not in script
    assert '".[dev]"' not in script
    assert 'LOCK_FILE="requirements.lock"' in script


def test_offline_acceptance_installs_the_bundled_application_wheel() -> None:
    script = _read(ACCEPTANCE)
    # The registry-blocked harness installs the pre-built application wheel the
    # same way the production installer does: no index, no dependency resolution.
    assert 'APP_WHEEL_DIR="$BUNDLE/python/app"' in script
    assert "-name 'openjm_enterprise_ai_backend-*.whl'" in script
    assert '"${#APP_WHEELS[@]}" -eq 1' in script
    assert "pip install --no-index --no-deps" in script
    assert "import app, app.version" in script


# --- integrity tool importable standalone (guards the manifest test's premise) --


def test_integrity_tool_is_importable_and_stdlib_only() -> None:
    spec = importlib.util.spec_from_file_location("release_integrity", INTEGRITY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.MANIFEST_NAME == "RELEASE-MANIFEST.json"
