"""REL1-B1 release-integrity contract tests."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "release-integrity.py"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def make_bundle(tmp_path: Path) -> Path:
    bundle = tmp_path / "bundle"
    (bundle / "app").mkdir(parents=True)
    (bundle / "python" / "wheelhouse").mkdir(parents=True)
    (bundle / "app" / "README.txt").write_text("OpenJM release payload\n")
    (bundle / "python" / "requirements.lock").write_text("example==1.0\n")
    (bundle / "python" / "wheelhouse" / "example.whl").write_bytes(b"wheel")
    return bundle


def test_generate_is_sorted_and_records_release_identity(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    result = run("generate", str(bundle))
    assert result.returncode == 0, result.stderr

    manifest = json.loads((bundle / "RELEASE-MANIFEST.json").read_text())
    assert manifest["schema_version"] == 1
    assert manifest["product_version"] == "0.2.0"
    assert manifest["release_train"] == "REL1"
    paths = [entry["path"] for entry in manifest["files"]]
    assert paths == sorted(paths)
    assert "RELEASE-MANIFEST.json" not in paths
    assert "RELEASE-MANIFEST.sha256" not in paths


def test_verify_accepts_unchanged_payload_and_trusted_digest(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    assert run("generate", str(bundle)).returncode == 0
    digest = hashlib.sha256((bundle / "RELEASE-MANIFEST.json").read_bytes()).hexdigest()

    result = run("verify", str(bundle), "--expected-manifest-sha256", digest)
    assert result.returncode == 0, result.stderr
    assert "verified release payload: 3 files" in result.stdout


def test_verify_rejects_modified_missing_and_unexpected_payload(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    assert run("generate", str(bundle)).returncode == 0

    target = bundle / "app" / "README.txt"
    target.write_text("tampered\n")
    assert run("verify", str(bundle)).returncode == 1

    target.write_text("OpenJM release payload\n")
    target.unlink()
    assert run("verify", str(bundle)).returncode == 1

    target.write_text("OpenJM release payload\n")
    (bundle / "unexpected.txt").write_text("not manifested")
    assert run("verify", str(bundle)).returncode == 1


def test_verify_rejects_manifest_tamper_and_wrong_trust_anchor(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    assert run("generate", str(bundle)).returncode == 0

    manifest_path = bundle / "RELEASE-MANIFEST.json"
    manifest_path.write_bytes(manifest_path.read_bytes() + b" ")
    result = run("verify", str(bundle))
    assert result.returncode == 1
    assert "release manifest checksum mismatch" in result.stderr

    assert run("generate", str(bundle)).returncode == 0
    result = run("verify", str(bundle), "--expected-manifest-sha256", "0" * 64)
    assert result.returncode == 1
    assert "trusted expected sha256" in result.stderr


def test_generate_and_verify_reject_symlink_payload(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    external = tmp_path / "external.txt"
    external.write_text("outside")
    (bundle / "link.txt").symlink_to(external)

    generated = run("generate", str(bundle))
    assert generated.returncode == 1
    assert "symlinks are not allowed" in generated.stderr
