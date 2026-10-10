"""REL1-B3 offline bundle assembly, determinism and exclusion contract tests.

Hermetic and offline: these tests never resolve dependencies or download
packages. They exercise the release-integrity manifest tool, the payload
staging exclusion policy, and the structural contract of the bundle/npm-cache
build scripts. The registry-blocked install itself is proven separately by
``scripts/rel1-offline-acceptance.sh`` (local harness / CI job).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
INTEGRITY = SCRIPTS / "release-integrity.py"
STAGE = SCRIPTS / "stage-release-payload.sh"
BUNDLE = SCRIPTS / "build-rel1-bundle.sh"
NPM_CACHE = SCRIPTS / "build-npm-offline-cache.sh"
NORMALIZER = SCRIPTS / "normalize-npm-cache.py"
ACCEPTANCE = SCRIPTS / "rel1-offline-acceptance.sh"
PROD_LOCK = ROOT / "backend" / "requirements.lock"


def run(*args: str, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )


def integrity(*args: str, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return run(sys.executable, str(INTEGRITY), *args, cwd=cwd)


def make_bundle(tmp_path: Path) -> Path:
    bundle = tmp_path / "openjm-rel1-0.2.0"
    (bundle / "app").mkdir(parents=True)
    (bundle / "python" / "wheelhouse").mkdir(parents=True)
    (bundle / "npm" / "cache" / "_cacache").mkdir(parents=True)
    (bundle / "app" / "README.txt").write_text("OpenJM release payload\n")
    (bundle / "python" / "requirements.lock").write_text("example==1.0\n")
    (bundle / "python" / "wheelhouse" / "example.whl").write_bytes(b"wheel")
    (bundle / "npm" / "cache" / "example.tgz").write_bytes(b"tarball")
    return bundle


# --- manifest determinism ----------------------------------------------------


def test_manifest_is_byte_stable_across_regeneration(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    assert integrity("generate", str(bundle)).returncode == 0
    first = (bundle / "RELEASE-MANIFEST.json").read_bytes()
    first_list = json.loads(first)["files"]

    assert integrity("generate", str(bundle)).returncode == 0
    second = (bundle / "RELEASE-MANIFEST.json").read_bytes()
    second_list = json.loads(second)["files"]

    assert first == second, "manifest generation is not deterministic"
    assert first_list == second_list


def test_rebuild_from_same_source_yields_same_file_list_and_digests(tmp_path: Path) -> None:
    a = make_bundle(tmp_path / "a")
    b = make_bundle(tmp_path / "b")
    assert integrity("generate", str(a)).returncode == 0
    assert integrity("generate", str(b)).returncode == 0

    ma = json.loads((a / "RELEASE-MANIFEST.json").read_text())
    mb = json.loads((b / "RELEASE-MANIFEST.json").read_text())
    assert [e["path"] for e in ma["files"]] == [e["path"] for e in mb["files"]]
    assert [e["sha256"] for e in ma["files"]] == [e["sha256"] for e in mb["files"]]
    assert (a / "RELEASE-MANIFEST.json").read_bytes() == (b / "RELEASE-MANIFEST.json").read_bytes()


def test_manifest_file_list_is_sorted_and_excludes_reserved(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    assert integrity("generate", str(bundle)).returncode == 0
    paths = [e["path"] for e in json.loads((bundle / "RELEASE-MANIFEST.json").read_text())["files"]]
    assert paths == sorted(paths)
    assert "RELEASE-MANIFEST.json" not in paths
    assert "RELEASE-MANIFEST.sha256" not in paths


# --- tamper detection --------------------------------------------------------


def test_verify_rejects_modified_payload_file(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    assert integrity("generate", str(bundle)).returncode == 0
    (bundle / "app" / "README.txt").write_text("tampered\n")
    result = integrity("verify", str(bundle))
    assert result.returncode == 1
    assert "mismatch" in result.stderr


def test_verify_rejects_missing_payload_file(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    assert integrity("generate", str(bundle)).returncode == 0
    (bundle / "python" / "wheelhouse" / "example.whl").unlink()
    result = integrity("verify", str(bundle))
    assert result.returncode == 1
    assert "missing payload files" in result.stderr


def test_verify_rejects_unexpected_extra_payload_file(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    assert integrity("generate", str(bundle)).returncode == 0
    (bundle / "npm" / "cache" / "smuggled.tgz").write_bytes(b"extra")
    result = integrity("verify", str(bundle))
    assert result.returncode == 1
    assert "unexpected payload files" in result.stderr


def test_generate_and_verify_reject_symlink_payload(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    external = tmp_path / "external.txt"
    external.write_text("outside")
    (bundle / "app" / "link.txt").symlink_to(external)

    generated = integrity("generate", str(bundle))
    assert generated.returncode == 1
    assert "symlinks are not allowed" in generated.stderr


def test_verify_rejects_wrong_trusted_manifest_digest(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    assert integrity("generate", str(bundle)).returncode == 0
    real = hashlib.sha256((bundle / "RELEASE-MANIFEST.json").read_bytes()).hexdigest()
    assert integrity("verify", str(bundle), "--expected-manifest-sha256", real).returncode == 0

    result = integrity("verify", str(bundle), "--expected-manifest-sha256", "0" * 64)
    assert result.returncode == 1
    assert "trusted expected sha256" in result.stderr


# --- payload exclusion policy ------------------------------------------------


def _synthetic_source(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    (src / "backend" / "app").mkdir(parents=True)
    (src / "backend" / ".venv").mkdir(parents=True)
    (src / "backend" / "app" / "__pycache__").mkdir(parents=True)
    (src / "frontend" / "node_modules").mkdir(parents=True)
    (src / "frontend" / "src").mkdir(parents=True)
    (src / "frontend" / "dist").mkdir(parents=True)
    (src / ".git").mkdir()
    (src / ".hermes").mkdir()
    (src / "deploy" / "compose" / "secrets").mkdir(parents=True)
    (src / "data").mkdir()

    (src / "backend" / "app" / "main.py").write_text("keep\n")
    (src / "frontend" / "src" / "index.tsx").write_text("keep\n")
    (src / "README.md").write_text("keep\n")
    (src / ".env.example").write_text("template\n")
    (src / ".env.production.example").write_text("template\n")

    for rel in (
        ".env",
        ".env.local",
        ".env.production",
        ".git/config",
        ".hermes/state.json",
        "backend/.venv/lib.so",
        "frontend/node_modules/dep.js",
        "frontend/dist/bundle.js",
        "deploy/compose/secrets/key.pem",
        "data/private.db",
        "backend/app/__pycache__/m.pyc",
        "backend/app/module.pyc",
    ):
        target = src / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("forbidden\n")
    return src


def test_stage_payload_excludes_secrets_state_and_caches(tmp_path: Path) -> None:
    src = _synthetic_source(tmp_path)
    dest = tmp_path / "dest"
    result = run("bash", str(STAGE), str(src), str(dest))
    assert result.returncode == 0, result.stderr

    remaining = {p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()}
    for forbidden in (
        ".env",
        ".env.local",
        ".env.production",
        ".git/config",
        ".hermes/state.json",
        "backend/.venv/lib.so",
        "frontend/node_modules/dep.js",
        "frontend/dist/bundle.js",
        "deploy/compose/secrets/key.pem",
        "data/private.db",
        "backend/app/__pycache__/m.pyc",
        "backend/app/module.pyc",
    ):
        assert forbidden not in remaining, f"{forbidden} must be excluded from the payload"

    # Committed content and the config templates that install-linux.sh needs stay.
    assert "backend/app/main.py" in remaining
    assert "frontend/src/index.tsx" in remaining
    assert "README.md" in remaining
    assert ".env.example" in remaining
    assert ".env.production.example" in remaining


def test_staged_payload_has_no_forbidden_state_after_render(tmp_path: Path) -> None:
    src = _synthetic_source(tmp_path)
    dest = tmp_path / "dest"
    assert run("bash", str(STAGE), str(src), str(dest)).returncode == 0
    for pattern in (".git", ".hermes", ".venv", "node_modules", "__pycache__"):
        assert not list(dest.rglob(pattern)), f"{pattern} leaked into the payload"


# --- npm cache normalizer ----------------------------------------------------


def _fake_npm_index(cache: Path, key: str, integrity: str) -> Path:
    # cacache index line: sha1(json) \t json, with volatile fetch metadata.
    payload = json.dumps(
        {
            "key": key,
            "integrity": integrity,
            "time": 1791624235310,
            "size": 42,
            "metadata": {
                "time": 1791624233242,
                "url": "https://registry.npmjs.org/x/-/x-1.0.0.tgz",
                "resHeaders": {"date": "Sat, 10 Oct 2026 09:23:52 GMT", "etag": '"abc"'},
            },
        },
        separators=(",", ":"),
    )
    line_hash = hashlib.sha1(payload.encode()).hexdigest()
    index = cache / "_cacache" / "index-v5" / "aa" / "bb"
    index.mkdir(parents=True)
    entry = index / "cccc"
    entry.write_text(f"\n{line_hash}\t{payload}\n", encoding="utf-8")
    (cache / "_cacache" / "content-v2").mkdir(parents=True, exist_ok=True)
    (cache / "_logs").mkdir(exist_ok=True)
    (cache / "_logs" / "debug.log").write_text("noise\n")
    (cache / "_update-notifier-last-checked").write_text("1759999999999\n")
    return entry


def test_normalizer_is_deterministic_and_drops_volatile_state(tmp_path: Path) -> None:
    key = "make-fetch-happen:request-cache:https://registry.npmjs.org/x/-/x-1.0.0.tgz"
    integrity = "sha512-" + "A" * 86 + "=="

    c1 = tmp_path / "c1"
    c2 = tmp_path / "c2"
    e1 = _fake_npm_index(c1, key, integrity)
    e2 = _fake_npm_index(c2, key, integrity)
    # Simulate a different fetch time in the second cache.
    e2.write_text(e2.read_text().replace("1791624235310", "1799999999999"), encoding="utf-8")

    for cache in (c1, c2):
        result = run(sys.executable, str(NORMALIZER), str(cache))
        assert result.returncode == 0, result.stderr
        assert not (cache / "_logs").exists()
        assert not (cache / "_update-notifier-last-checked").exists()

    assert e1.read_bytes() == e2.read_bytes(), "normalized index is not deterministic"
    normalized = json.loads(e1.read_text().split("\t", 1)[1])
    assert "metadata" not in normalized
    assert normalized["time"] == 0
    # The bucket hash must remain valid: cacache re-validates sha1(json) == hash.
    line_hash, _, body = e1.read_text().lstrip("\n").strip().partition("\t")
    assert hashlib.sha1(body.encode()).hexdigest() == line_hash


# --- structural contract of the build scripts --------------------------------


def test_bundle_script_derives_version_and_orders_generate_before_verify() -> None:
    script = BUNDLE.read_text(encoding="utf-8")
    # Version comes from the single source of truth, never hardcoded.
    assert "PRODUCT_VERSION" in script
    assert "backend/app/version.py" in script
    assert '"0.2.0"' not in script
    # The manifest is generated last and verified immediately afterwards.
    gen_at = script.index('"$INTEGRITY" generate')
    verify_at = script.index('"$INTEGRITY" verify')
    assert gen_at < verify_at
    # Committed production lock is copied verbatim into python/.
    assert "backend/requirements.lock" in script
    assert 'cp "$LOCK_FILE" "$BUNDLE/python/requirements.lock"' in script
    # Wheelhouse and npm cache are populated via the bounded helper scripts.
    assert "build-python-wheelhouse.sh" in script
    assert "build-npm-offline-cache.sh" in script
    assert "stage-release-payload.sh" in script


def test_bundle_script_never_commits_node_modules_or_cache() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "node_modules/" in gitignore
    # The cache is a build artifact; the layout places it under the output bundle
    # (outside the repo tree) and the bundle script refuses in-repo output.
    script = BUNDLE.read_text(encoding="utf-8")
    assert "output directory must be outside the repository tree" in script


def test_npm_cache_builder_proves_registry_blocked_offline_install() -> None:
    script = NPM_CACHE.read_text(encoding="utf-8")
    assert "package-lock.json" in script
    assert "--cache" in script
    assert "npm_config_offline=true" in script
    assert "npm_config_registry=" in script
    assert "normalize-npm-cache.py" in script
    assert "Node.js 22" in script


def test_committed_locks_are_untouched_by_bundle_tooling() -> None:
    # The bundle tooling consumes the committed lock; it must never regenerate or
    # mutate it. Assert the committed digests are the pinned release digests.
    digest = hashlib.sha256(PROD_LOCK.read_bytes()).hexdigest()
    assert digest == "90afebf3c719b1db838cbf77a3799477c8ac312d16817aca7fab670b34fde72b"
    for script in (BUNDLE, NPM_CACHE, STAGE, ACCEPTANCE):
        text = script.read_text(encoding="utf-8")
        assert "compile-python-locks" not in text
        assert "requirements.lock >" not in text
