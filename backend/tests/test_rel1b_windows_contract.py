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
    # Python 3.11 is required by the canonical installer, fail-early.
    assert "Python 3.11 is required" in sh
    assert "python3.11" in ps1
    # The bootstrap REPORTS Python 3.11 as an operator prerequisite. It must not
    # attempt to install it: stock Ubuntu 24.04 (Noble) ships Python 3.12, so
    # `apt-get install python3.11` is not a valid path there, and installing a
    # pinned 3.11 from an unverified third-party source would widen the release
    # supply chain.
    assert "python3.11-venv" not in ps1
    assert "apt-get install -y python3.11" not in ps1
    assert "python3.11 python3.11-venv" not in ps1
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


# ---------------------------------------------------------------------------
# REL1-B4H hardening contract (H1, H2, H4, H5, H6)
# ---------------------------------------------------------------------------


def test_repo_path_default_resolution_is_robust() -> None:
    """H1: explicit wins, script location is used, cwd is never a fallback."""
    ps1 = _ps1()
    # No default expression in the param block: an empty $PSScriptRoot there
    # throws during parameter binding, before the body can report anything.
    assert "[string]$RepoPath," in ps1
    assert "Resolve-RepoRoot" in ps1
    assert "$PSCommandPath" in ps1
    assert "$MyInvocation.MyCommand.Path" in ps1
    assert "$MyInvocation.MyCommand.Definition" in ps1
    assert "Cannot resolve the script location to locate the repository" in ps1
    assert "Repository path does not exist" in ps1
    # ProviderPath, not Path: .Path returns a provider prefix for UNC locations.
    assert ".ProviderPath" in ps1
    # The current working directory is never used to guess the repository.
    assert "Get-Location" not in ps1
    assert "(Get-Item .)" not in ps1


def test_privilege_is_detected_before_installing_anything() -> None:
    """H2: root, sudo-capable and no-sudo runtimes are distinguished."""
    ps1 = _ps1()
    assert "id -u" in ps1
    assert "PRIV=SUDO" in ps1
    assert "$isRoot" in ps1
    assert "$hasSudo" in ps1
    assert "non-root without sudo" in ps1
    assert "cannot elevate privileges" in ps1
    assert "Provision Python 3.11 / required packages or use a sudo-capable account." in ps1
    # Root installs base packages directly; a sudo user uses sudo -n (never an
    # interactive prompt).
    assert "apt-get install -y $Package" in ps1
    assert "sudo -n apt-get" in ps1


def test_node_22_is_required_but_never_auto_installed() -> None:
    """H4: frontend implies Node 22; it is reported, never installed."""
    ps1 = _ps1()
    assert "Node.js 22 is required" in ps1
    assert "npm install -g" not in ps1
    assert "nodesource" not in ps1.lower()
    assert "setup_22.x" not in ps1
    # -SkipFrontend removes the Node requirement.
    assert "if (-not $SkipFrontend) {" in ps1


def test_prerequisite_categories_are_reported_precisely() -> None:
    """H5: the exact missing category is named, not a cascading failure."""
    ps1 = _ps1()
    for message in (
        "Repository path unresolved",
        "is absent from 'wsl --list --quiet'",
        "Python 3.11 absent",
        "Node.js 22 is required",
        "cannot elevate privileges",
        "Linux installer failed inside WSL",
    ):
        assert message in ps1, message
    # Failures exit non-zero.
    assert "function Fail($msg) { Write-Error $msg; exit 1 }" in ps1


def test_bootstrap_remains_a_thin_delegator() -> None:
    """H6: the PowerShell layer never resolves dependencies itself."""
    ps1 = _ps1()
    assert "pip install" not in ps1
    assert "npm ci" not in ps1
    assert "--require-hashes" not in ps1
    assert "requirements.lock" not in ps1
    assert "requirements-dev.lock" not in ps1
    # Only base packages may be installed, and only git/curl are requested.
    assert '"git"' in ps1
    assert '"curl"' in ps1
    assert "python3.11" not in ps1.split("foreach ($pkg in")[1].split("}")[0] if "foreach ($pkg in" in ps1 else True


def test_focused_behavioural_harness_exists_and_is_bounded() -> None:
    """H7: a bounded harness drives the bootstrap without real distributions."""
    harness = ROOT / "scripts" / "tests" / "wsl2_bootstrap_harness.ps1"
    assert harness.exists()
    text = harness.read_text(encoding="utf-8")
    assert "wsl_stub.ps1" in text
    # It never creates or removes a real WSL distribution.
    assert "wsl --install" not in text
    assert "wsl --unregister" not in text
    # It exercises the required case families.
    for case in (
        "script-location resolution",
        "explicit RepoPath",
        "repo not visible",
        "distro absent",
        "root missing py311",
        "non-root with sudo",
        "non-root no sudo",
        "stock noble no py311",
        "node absent",
        "node wrong major",
        "skip frontend",
        "development profile",
        "production profile",
        "installer exit propagated",
    ):
        assert case in text, case
