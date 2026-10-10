"""REL1-B2 release-lock contract tests.

Hermetic and offline: they assert the committed hash-pinned locks, the
lock-authoritative installer, the drift gate workflow, and the drift-comparison
script's exit behaviour without building a real wheelhouse or resolving
dependencies.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
PROD_LOCK = BACKEND / "requirements.lock"
DEV_LOCK = BACKEND / "requirements-dev.lock"
INSTALLER = ROOT / "scripts" / "install-linux.sh"
WHEELHOUSE = ROOT / "scripts" / "build-python-wheelhouse.sh"
DRIFT = ROOT / "scripts" / "check-python-lock-drift.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

# Test-only packages that must never leak into the production lock. (pypdf is
# omitted here: it is a genuine transitive production dependency in this tree.)
DEV_ONLY = ("pytest", "pytest-asyncio", "pgserver")


def requirement_blocks(text: str) -> list[str]:
    """Split a pip-compile lock into per-requirement blocks (comments dropped)."""
    blocks: list[str] = []
    current: list[str] = []
    for raw in text.splitlines():
        if not raw.strip():
            if current:
                blocks.append("\n".join(current))
                current = []
            continue
        if raw.lstrip().startswith("#"):
            continue
        if raw[0].isspace() and current:
            current.append(raw)
        else:
            if current:
                blocks.append("\n".join(current))
            current = [raw]
    if current:
        blocks.append("\n".join(current))
    return blocks


def test_both_committed_locks_exist() -> None:
    assert PROD_LOCK.is_file(), f"missing committed lock: {PROD_LOCK}"
    assert DEV_LOCK.is_file(), f"missing committed lock: {DEV_LOCK}"


def test_every_requirement_is_hash_pinned() -> None:
    for lock in (PROD_LOCK, DEV_LOCK):
        blocks = requirement_blocks(lock.read_text(encoding="utf-8"))
        assert blocks, f"{lock.name} has no requirements"
        for block in blocks:
            first = block.splitlines()[0]
            assert re.match(r"^[A-Za-z0-9]", first), f"unexpected block start: {first!r}"
            assert "==" in first, f"{lock.name}: requirement is not pinned: {first!r}"
            assert "--hash=sha256:" in block, (
                f"{lock.name}: requirement lacks a sha256 hash: {first!r}"
            )


def test_production_lock_excludes_dev_only_packages() -> None:
    prod = PROD_LOCK.read_text(encoding="utf-8")
    dev = DEV_LOCK.read_text(encoding="utf-8")
    for name in DEV_ONLY:
        assert not re.search(rf"^{re.escape(name)}==", prod, re.MULTILINE), (
            f"production lock must not pin dev-only package {name!r}"
        )
        assert re.search(rf"^{re.escape(name)}==", dev, re.MULTILINE), (
            f"development lock must pin {name!r}"
        )


def test_installer_uses_committed_production_lock_without_dev_extras() -> None:
    script = INSTALLER.read_text(encoding="utf-8")
    assert 'LOCK_FILE="requirements.lock"' in script
    assert 'LOCK_FILE="requirements-dev.lock"' in script
    assert 'python -m pip install --quiet --require-hashes -r "$LOCK_FILE"' in script
    assert '[dev]"' not in script
    # Hash enforcement must not be weakened for editable-install convenience.
    assert 'python -m pip install --quiet --no-deps -e "."' in script


def test_installer_has_no_uncontrolled_pip_self_upgrade() -> None:
    script = INSTALLER.read_text(encoding="utf-8")
    assert not re.search(r"pip install[^\n]*--upgrade[^\n]*\bpip\b", script)
    assert "--upgrade pip" not in script


def test_workflow_contains_lock_drift_comparison_gate() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "rel1_lock_drift_gate" in workflow
    assert "check-python-lock-drift.sh" in workflow
    assert "compile-python-locks.sh" in workflow
    assert "build-python-wheelhouse.sh" in workflow
    assert "requirements-dev.lock" in workflow or "requirements.lock" in workflow
    # The candidate-artifact upload is gone: committed locks are authoritative.
    assert "rel1-python-lock-candidate" not in workflow
    assert "upload-artifact" not in workflow


def _write_locks(directory: Path, prod: str, dev: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "requirements.lock").write_text(prod, encoding="utf-8")
    (directory / "requirements-dev.lock").write_text(dev, encoding="utf-8")


def _run_drift(generated: Path, committed: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(DRIFT), str(generated), str(committed)],
        text=True,
        capture_output=True,
        check=False,
    )


def test_drift_gate_passes_on_identical_locks(tmp_path: Path) -> None:
    prod = "aiohttp==3.8.4 \\\n    --hash=sha256:" + "0" * 64 + "\n"
    dev = prod + "pytest==8.0 \\\n    --hash=sha256:" + "1" * 64 + "\n"
    generated = tmp_path / "generated"
    committed = tmp_path / "committed"
    _write_locks(generated, prod, dev)
    _write_locks(committed, prod, dev)

    result = _run_drift(generated, committed)
    assert result.returncode == 0, result.stderr
    assert "LOCK DRIFT GATE PASSED" in result.stdout


def test_drift_gate_fails_and_names_file_on_drift(tmp_path: Path) -> None:
    prod = "aiohttp==3.8.4 \\\n    --hash=sha256:" + "0" * 64 + "\n"
    dev = prod + "pytest==8.0 \\\n    --hash=sha256:" + "1" * 64 + "\n"
    generated = tmp_path / "generated"
    committed = tmp_path / "committed"
    _write_locks(generated, prod, dev)
    _write_locks(committed, prod, dev)

    # Change only the production lock in the regenerated output.
    (generated / "requirements.lock").write_text(
        "aiohttp==3.8.5 \\\n    --hash=sha256:" + "2" * 64 + "\n", encoding="utf-8"
    )

    result = _run_drift(generated, committed)
    assert result.returncode != 0
    assert "requirements.lock" in result.stderr
    assert "LOCK DRIFT" in result.stderr


def test_drift_gate_fails_when_generated_lock_missing(tmp_path: Path) -> None:
    committed = tmp_path / "committed"
    _write_locks(committed, "example==1.0 \\\n    --hash=sha256:" + "0" * 64 + "\n", "x==1\n")
    generated = tmp_path / "generated"
    generated.mkdir()

    result = _run_drift(generated, committed)
    assert result.returncode != 0
    assert "missing" in result.stderr


def test_wheelhouse_builder_requires_hashes_and_binary_wheels() -> None:
    script = WHEELHOUSE.read_text(encoding="utf-8")
    assert "--require-hashes" in script
    assert "--only-binary=:all:" in script
    assert "backend/requirements.lock" in script
    assert "--allow-source" in script
    assert "SHA256SUMS" in script
