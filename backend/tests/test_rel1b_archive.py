"""REL1-B4 deterministic-archive tests (B4-F).

Build the SAME small bundle twice — in two independent parent directories — and
prove the produced ``openjm-rel1-<version>.tar.gz`` archives are byte-identical
(same sha256). Also prove a tampered payload is refused BEFORE any archive is
written, and that archive members are relative to the canonical bundle prefix
with machine-specific metadata normalized away.

Kept fast: a hand-built multi-file fixture, no wheelhouse/npm download.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
INTEGRITY = SCRIPTS / "release-integrity.py"
ARCHIVE = SCRIPTS / "build-rel1-archive.sh"
PROD_VERSION = "0.2.0"
ARCHIVE_NAME = f"openjm-rel1-{PROD_VERSION}.tar.gz"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*args], cwd=ROOT, text=True, capture_output=True, check=False
    )


def build_bundle(parent: Path) -> Path:
    """Assemble a small verified bundle at ``parent/openjm-rel1-<version>``."""
    bundle = parent / f"openjm-rel1-{PROD_VERSION}"
    (bundle / "app").mkdir(parents=True)
    (bundle / "python" / "wheelhouse").mkdir(parents=True)
    (bundle / "app" / "README.txt").write_text("OpenJM release payload\n")
    (bundle / "python" / "requirements.lock").write_text("example==1.0\n")
    (bundle / "python" / "wheelhouse" / "example.whl").write_bytes(b"wheel-bytes")
    assert run(sys.executable, str(INTEGRITY), "generate", str(bundle)).returncode == 0
    return bundle


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_same_bundle_built_twice_yields_byte_identical_archive(tmp_path: Path) -> None:
    # Two independent builds of the same content, in different parent dirs.
    bundle_a = build_bundle(tmp_path / "a")
    bundle_b = build_bundle(tmp_path / "b")

    out_a = tmp_path / "arch-a"
    out_b = tmp_path / "arch-b"
    out_a.mkdir()
    out_b.mkdir()

    res_a = run("bash", str(ARCHIVE), "--output-dir", str(out_a), str(bundle_a))
    res_b = run("bash", str(ARCHIVE), "--output-dir", str(out_b), str(bundle_b))
    assert res_a.returncode == 0, res_a.stderr
    assert res_b.returncode == 0, res_b.stderr

    archive_a = out_a / ARCHIVE_NAME
    archive_b = out_b / ARCHIVE_NAME
    assert archive_a.is_file() and archive_b.is_file()
    assert sha256(archive_a) == sha256(archive_b), "archive must be byte-identical"


def test_archive_is_deterministic_for_a_single_payload_across_runs(
    tmp_path: Path,
) -> None:
    bundle = build_bundle(tmp_path)
    out = tmp_path / "arch"
    out.mkdir()
    assert (
        run("bash", str(ARCHIVE), "--output-dir", str(out), str(bundle)).returncode == 0
    )
    first = sha256(out / ARCHIVE_NAME)
    oss = out / ARCHIVE_NAME
    oss.unlink()
    assert (
        run("bash", str(ARCHIVE), "--output-dir", str(out), str(bundle)).returncode == 0
    )
    assert sha256(out / ARCHIVE_NAME) == first


def test_archive_members_are_relative_to_canonical_prefix(tmp_path: Path) -> None:
    bundle = build_bundle(tmp_path / "src")
    out = tmp_path / "arch"
    out.mkdir()
    assert (
        run("bash", str(ARCHIVE), "--output-dir", str(out), str(bundle)).returncode == 0
    )

    listing = run("tar", "tzf", str(out / ARCHIVE_NAME))
    assert listing.returncode == 0, listing.stderr
    members = [m for m in listing.stdout.splitlines() if m]
    assert members, "archive is empty"
    prefix = f"openjm-rel1-{PROD_VERSION}/"
    for member in members:
        assert not member.startswith("/"), f"absolute path leaked: {member}"
        assert member.startswith(prefix), f"member outside canonical prefix: {member}"
    assert f"{prefix}RELEASE-MANIFEST.json" in members
    assert f"{prefix}app/README.txt" in members


def test_modified_payload_is_refused_before_archiving(tmp_path: Path) -> None:
    bundle = build_bundle(tmp_path)
    (bundle / "app" / "README.txt").write_text("tampered\n")

    out = tmp_path / "arch"
    out.mkdir()
    result = run("bash", str(ARCHIVE), "--output-dir", str(out), str(bundle))
    assert result.returncode == 1
    assert "refusing to archive" in result.stderr
    assert not (out / ARCHIVE_NAME).exists(), (
        "no archive may be written for a bad bundle"
    )


def test_wrong_trusted_digest_is_refused_before_archiving(tmp_path: Path) -> None:
    bundle = build_bundle(tmp_path)
    out = tmp_path / "arch"
    out.mkdir()
    result = run(
        "bash",
        str(ARCHIVE),
        "--expected-manifest-sha256",
        "0" * 64,
        "--output-dir",
        str(out),
        str(bundle),
    )
    assert result.returncode == 1
    assert "trusted expected sha256" in result.stderr
    assert not (out / ARCHIVE_NAME).exists()


def test_archive_is_refused_inside_repository_tree(tmp_path: Path) -> None:
    bundle = build_bundle(tmp_path)
    in_repo = ROOT / "tmp-rel1-archive-should-not-exist"
    result = run("bash", str(ARCHIVE), "--output-dir", str(in_repo), str(bundle))
    assert result.returncode == 2
    assert "outside the repository tree" in result.stderr
    assert not in_repo.exists()
