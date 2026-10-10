"""REL1-B release-bundle and offline-install contract tests."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _bundle_module():
    path = ROOT / "scripts" / "build-rel1b-bundle.py"
    spec = importlib.util.spec_from_file_location("rel1b_bundle", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_bundle_verifier_accepts_matching_integrity_metadata(tmp_path: Path) -> None:
    module = _bundle_module()
    payload = tmp_path / "source" / "payload.txt"
    payload.parent.mkdir()
    payload.write_text("openjm", encoding="utf-8")
    manifest = {
        "manifest_version": 1,
        "version": "0.2.0",
        "git_commit": "abc",
        "schema_head": "0021_support_content_scope",
        "files": [{
            "path": "source/payload.txt",
            "sha256": _sha(payload),
            "bytes": payload.stat().st_size,
            "class": "source",
        }],
    }
    manifest_path = tmp_path / "BUNDLE-MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "SHA256SUMS").write_text(
        f"{_sha(manifest_path)}  BUNDLE-MANIFEST.json\n"
        f"{_sha(payload)}  source/payload.txt\n",
        encoding="utf-8",
    )

    result = module.verify_bundle(tmp_path)
    assert result["ok"] is True
    assert result["version"] == "0.2.0"


def test_bundle_verifier_rejects_corruption(tmp_path: Path) -> None:
    module = _bundle_module()
    payload = tmp_path / "payload.bin"
    payload.write_bytes(b"good")
    manifest = {
        "manifest_version": 1,
        "files": [{
            "path": "payload.bin",
            "sha256": _sha(payload),
            "bytes": 4,
            "class": "source",
        }],
    }
    manifest_path = tmp_path / "BUNDLE-MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "SHA256SUMS").write_text(
        f"{_sha(manifest_path)}  BUNDLE-MANIFEST.json\n"
        f"{_sha(payload)}  payload.bin\n",
        encoding="utf-8",
    )
    payload.write_bytes(b"tampered")

    with pytest.raises(module.BundleError, match="checksum mismatch"):
        module.verify_bundle(tmp_path)


def test_offline_installer_cannot_fall_back_to_pypi_or_npm() -> None:
    script = (ROOT / "scripts" / "install-offline.sh").read_text(encoding="utf-8")
    assert "--no-index" in script
    assert "--find-links" in script
    assert "--require-hashes" in script
    assert "npm ci" not in script
    assert "npm install" not in script
    assert "verify-bundle.py" in script
    assert "sha256sum -c SHA256SUMS" in script


def test_bundle_freezes_frontend_and_self_contains_install_tools() -> None:
    script = (ROOT / "scripts" / "build-rel1b-bundle.py").read_text(encoding="utf-8")
    assert "frontend-dist.tar.gz" in script
    assert "requirements.lock" in script
    assert "install/verify-bundle.py" not in script  # copied via Path operations, not hardcoded escape
    assert 'install / "verify-bundle.py"' in script
    assert 'install / "install-offline.sh"' in script


def test_proxy_build_uses_rel1_node_major() -> None:
    dockerfile = (ROOT / "deploy" / "compose" / "Dockerfile.proxy").read_text()
    assert "FROM node:22-bookworm-slim AS frontend" in dockerfile
    assert "node:20" not in dockerfile
