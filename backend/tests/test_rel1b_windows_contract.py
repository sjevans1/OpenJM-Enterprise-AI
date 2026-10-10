"""REL1-B4 Windows/WSL2 contract tests (B4-E).

The real Windows/WSL2 runtime install is executed by the lead on a host. These
tests are machine-checkable HERE and prove from the scripts alone that the WSL2
bootstrap is a thin, non-divergent delegator to the canonical Linux installer.

Each assertion cites the exact string it relies on, so a regression in the
bootstrap is caught without a Windows host.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WSL2_PS1 = ROOT / "scripts" / "install-wsl2.ps1"
LINUX_SH = ROOT / "scripts" / "install-linux.sh"


def _ps1() -> str:
    return WSL2_PS1.read_text(encoding="utf-8")


def _sh() -> str:
    return LINUX_SH.read_text(encoding="utf-8")


# --- profile propagation -----------------------------------------------------


def test_wsl2_propagates_selected_profile_to_linux_installer() -> None:
    script = _ps1()
    # The profile is a validated choice, not a free-form string.
    assert '[ValidateSet("development", "production")]' in script
    # It has an explicit development default (documented), and it is ALWAYS
    # forwarded — the bootstrap never silently drops it and lets the Linux
    # installer fall back to its own default.
    assert '[string]$Profile = "development"' in script
    assert '$linuxArgs = "--profile $Profile"' in script
    # The forwarded argument is passed to the canonical Linux installer.
    assert "\"cd '$wslPath' && bash scripts/install-linux.sh $linuxArgs\"" in script


def test_wsl2_propagates_skip_frontend() -> None:
    script = _ps1()
    assert 'if ($SkipFrontend) { $linuxArgs += " --skip-frontend" }' in script
    assert "[switch]$SkipFrontend" in script


# --- single, canonical dependency path ---------------------------------------


def test_wsl2_invokes_canonical_linux_installer_not_a_divergent_path() -> None:
    script = _ps1()
    # It delegates to scripts/install-linux.sh (the same script used on a native
    # Linux host) rather than re-implementing the install.
    assert "bash scripts/install-linux.sh $linuxArgs" in script
    # No divergent Python/npm dependency resolution in the PowerShell layer: the
    # committed locks and `pip install`/`npm ci` live only in install-linux.sh.
    assert "pip install" not in script
    assert "requirements.lock" not in script
    assert "requirements-dev.lock" not in script
    assert "npm ci" not in script
    assert "--require-hashes" not in script


def test_linux_installer_is_the_single_dependency_authority() -> None:
    script = _sh()
    assert 'LOCK_FILE="requirements.lock"' in script
    assert 'LOCK_FILE="requirements-dev.lock"' in script
    assert 'python -m pip install --quiet --require-hashes -r "$LOCK_FILE"' in script
    assert 'python -m pip install --quiet --no-deps -e "."' in script


# --- toolchain requirements preserved ----------------------------------------


def test_python_311_and_node_22_requirements_are_preserved() -> None:
    ps1 = _ps1()
    sh = _sh()
    # The bootstrap installs Python 3.11 inside WSL as a prerequisite.
    assert "python3.11" in ps1
    # The canonical installer enforces the exact runtime toolchain, fail-early.
    assert "Python 3.11 is required" in sh
    assert '[[ "$NODE_MAJOR" -eq 22 ]]' in sh
    assert "Node.js 22 is required" in sh
    # Profile is validated before anything is installed.
    assert '[[ "$PROFILE" == "development" || "$PROFILE" == "production" ]]' in sh


# --- production excludes [dev] extras ----------------------------------------


def test_production_install_excludes_dev_extras() -> None:
    sh = _sh()
    assert '".[dev]"' not in sh
    assert "[dev]" not in sh
    # The production lock file is exactly the production lock; dev extras come
    # only from the separately committed development lock.
    assert 'LOCK_FILE="requirements.lock"' in sh
