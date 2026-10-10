"""REL1-B4 tamper-negative tests against a real assembled small bundle.

These tests build a small (hand-authored, not multi-GB) bundle, generate its
manifest with the stdlib release-integrity tool, then prove that verification
FAILS CLOSED for each tamper class before any install or execute step could run.
The proof is made directly against the verifier function
(``release_integrity.verify``) so a rejection is an exception raised by the same
code path the installer trusts, not merely a script exit code.

Every test rebuilds its own bundle and never reuses a mutated artifact from a
previous case.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "release-integrity.py"
PROD_VERSION = "0.2.0"


def _load_integrity() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("release_integrity", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


integrity = _load_integrity()


def make_bundle(tmp_path: Path) -> Path:
    """A small, real bundle fixture: payload + reserved manifest files."""
    bundle = tmp_path / f"openjm-rel1-{PROD_VERSION}"
    (bundle / "app").mkdir(parents=True)
    (bundle / "python" / "wheelhouse").mkdir(parents=True)
    (bundle / "npm" / "cache").mkdir(parents=True)
    (bundle / "app" / "README.txt").write_text("OpenJM release payload\n")
    (bundle / "app" / "version.py").write_text(f'PRODUCT_VERSION = "{PROD_VERSION}"\n')
    (bundle / "python" / "requirements.lock").write_text("example==1.0\n")
    (bundle / "python" / "wheelhouse" / "example.whl").write_bytes(b"wheel-bytes")
    (bundle / "npm" / "cache" / "example.tgz").write_bytes(b"tarball-bytes")
    integrity.generate(bundle, ROOT)
    return bundle


def _write_manifest(bundle: Path, entries: list[dict]) -> None:
    """Write a hand-crafted manifest + matching checksum file (test fixtures)."""
    manifest = {
        "schema_version": 1,
        "product_version": PROD_VERSION,
        "release_train": "REL1",
        "files": entries,
    }
    raw = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    (bundle / "RELEASE-MANIFEST.json").write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    (bundle / "RELEASE-MANIFEST.sha256").write_text(
        f"{digest}  RELEASE-MANIFEST.json\n", encoding="ascii"
    )


# --- 1. modified payload file ------------------------------------------------


def test_modified_payload_file_is_rejected(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    (bundle / "app" / "README.txt").write_text("tampered\n")
    with pytest.raises(ValueError, match="mismatch"):
        integrity.verify(bundle, None)


# --- 2. deleted payload file -------------------------------------------------


def test_deleted_payload_file_is_rejected(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    (bundle / "python" / "wheelhouse" / "example.whl").unlink()
    with pytest.raises(ValueError, match="missing payload files"):
        integrity.verify(bundle, None)


# --- 3. unexpected extra payload file ----------------------------------------


def test_unexpected_extra_payload_file_is_rejected(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    (bundle / "npm" / "cache" / "smuggled.tgz").write_bytes(b"extra")
    with pytest.raises(ValueError, match="unexpected payload files"):
        integrity.verify(bundle, None)


# --- 4. modified manifest ----------------------------------------------------


def test_modified_manifest_is_rejected(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    manifest = bundle / "RELEASE-MANIFEST.json"
    manifest.write_bytes(manifest.read_bytes() + b" ")
    with pytest.raises(ValueError, match="checksum mismatch"):
        integrity.verify(bundle, None)


# --- 5. wrong externally trusted manifest sha256 -----------------------------


def test_wrong_trusted_manifest_sha256_is_rejected(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    real = hashlib.sha256((bundle / "RELEASE-MANIFEST.json").read_bytes()).hexdigest()
    # The genuine digest is accepted...
    integrity.verify(bundle, real)
    # ...but a wrong out-of-band trust anchor must fail closed.
    with pytest.raises(ValueError, match="trusted expected sha256"):
        integrity.verify(bundle, "0" * 64)


# --- 6. symlink payload ------------------------------------------------------


def test_symlink_payload_is_rejected(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    external = tmp_path / "external.txt"
    external.write_text("outside\n")
    (bundle / "app" / "link.txt").symlink_to(external)
    with pytest.raises(ValueError, match="symlinks are not allowed"):
        integrity.verify(bundle, None)


# --- 7. unsafe / path-traversal manifest entries -----------------------------


def test_manifest_entry_with_parent_traversal_is_rejected(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    _write_manifest(
        bundle,
        [
            {
                "path": "app/README.txt",
                "size": 22,
                "sha256": hashlib.sha256(b"x").hexdigest(),
            },
            {
                "path": "../escape.txt",
                "size": 1,
                "sha256": hashlib.sha256(b"x").hexdigest(),
            },
        ],
    )
    with pytest.raises(ValueError, match="unsafe manifest path"):
        integrity.verify(bundle, None)


def test_manifest_entry_with_absolute_path_is_rejected(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    _write_manifest(
        bundle,
        [
            {
                "path": "/etc/passwd",
                "size": 1,
                "sha256": hashlib.sha256(b"x").hexdigest(),
            }
        ],
    )
    with pytest.raises(ValueError, match="unsafe manifest path"):
        integrity.verify(bundle, None)


def test_manifest_entry_naming_reserved_file_is_rejected(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    _write_manifest(
        bundle,
        [
            {
                "path": "RELEASE-MANIFEST.json",
                "size": 1,
                "sha256": hashlib.sha256(b"x").hexdigest(),
            }
        ],
    )
    with pytest.raises(ValueError, match="unsafe manifest path"):
        integrity.verify(bundle, None)


# --- guard: a clean, freshly built bundle verifies ---------------------------


def test_clean_bundle_verifies_before_any_tamper(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    # No exception: the negative cases above are real rejections, not a broken
    # verifier that fails on everything.
    integrity.verify(bundle, None)
