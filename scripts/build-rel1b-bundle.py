#!/usr/bin/env python3
"""Build and verify the REL1-B connected-build/offline-install bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"


class BundleError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(args: list[str], *, cwd: Path = ROOT) -> None:
    subprocess.run(args, cwd=cwd, check=True)


def release_identity() -> tuple[str, str]:
    sys.path.insert(0, str(BACKEND))
    from app.version import PRODUCT_VERSION, RELEASE_TRAIN
    return PRODUCT_VERSION, RELEASE_TRAIN


def schema_head() -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    heads = ScriptDirectory.from_config(cfg).get_heads()
    if len(heads) != 1:
        raise BundleError(f"expected one Alembic head, found {heads}")
    return heads[0]


def write_wheel_lock(wheels: Path) -> None:
    try:
        from packaging.utils import canonicalize_name, parse_wheel_filename
    except ImportError as exc:
        raise BundleError("packaging is required on the connected build host") from exc

    lines: list[str] = []
    for wheel in sorted(wheels.glob("*.whl")):
        name, version, _build, _tags = parse_wheel_filename(wheel.name)
        lines.append(
            f"{canonicalize_name(name)}=={version} --hash=sha256:{sha256(wheel)}"
        )
    if not lines:
        raise BundleError("wheelhouse is empty")
    (wheels / "requirements.lock").write_text("\n".join(lines) + "\n", encoding="utf-8")


def record(root: Path, path: Path, artifact_class: str) -> dict:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": sha256(path),
        "bytes": path.stat().st_size,
        "class": artifact_class,
    }


def build_bundle(output: Path, release_id: str) -> Path:
    version, train = release_identity()
    if output.exists() and any(output.iterdir()):
        raise BundleError(f"output directory is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    wheels = output / "wheels"
    source = output / "source"
    frontend_out = output / "frontend"
    install = output / "install"
    for directory in (wheels, source, frontend_out, install):
        directory.mkdir()

    source_tar = source / f"openjm-{version}.tar.gz"
    run(["git", "archive", "--format=tar.gz", f"--output={source_tar}", "HEAD"])

    run([
        sys.executable, "-m", "pip", "wheel",
        "--prefer-binary", "--wheel-dir", str(wheels), str(BACKEND),
    ])
    write_wheel_lock(wheels)

    run(["npm", "ci", "--no-audit", "--no-fund"], cwd=FRONTEND)
    run(["npm", "run", "build"], cwd=FRONTEND)
    dist_tar = frontend_out / "frontend-dist.tar.gz"
    with tarfile.open(dist_tar, "w:gz") as tar:
        tar.add(FRONTEND / "dist", arcname="dist")

    # Make the bundle self-contained: verification and installation cross the
    # air gap with the payload they verify/install.
    shutil.copy2(ROOT / "scripts" / "build-rel1b-bundle.py", install / "verify-bundle.py")
    shutil.copy2(ROOT / "scripts" / "install-offline.sh", install / "install-offline.sh")

    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()
    classes = {
        "source": "source",
        "wheels": "wheelhouse",
        "frontend": "frontend",
        "install": "installer",
    }
    files: list[dict] = []
    for dirname, artifact_class in classes.items():
        for path in sorted((output / dirname).rglob("*")):
            if path.is_file():
                files.append(record(output, path, artifact_class))

    manifest = {
        "manifest_version": 1,
        "product": "OpenJM Enterprise AI",
        "version": version,
        "release_id": release_id,
        "release_train": train,
        "git_commit": commit,
        "schema_head": schema_head(),
        "built_at": datetime.now(timezone.utc).isoformat(),
        "platform": platform.system().lower(),
        "arch": platform.machine().lower(),
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "files": files,
    }
    manifest_path = output / "BUNDLE-MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    checksum_paths = [manifest_path] + [output / entry["path"] for entry in files]
    (output / "SHA256SUMS").write_text(
        "".join(
            f"{sha256(path)}  {path.relative_to(output).as_posix()}\n"
            for path in checksum_paths
        ),
        encoding="utf-8",
    )
    verify_bundle(output)
    return output


def verify_bundle(bundle: Path) -> dict:
    manifest_path = bundle / "BUNDLE-MANIFEST.json"
    sums_path = bundle / "SHA256SUMS"
    if not manifest_path.is_file() or not sums_path.is_file():
        raise BundleError("bundle is missing manifest or SHA256SUMS")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    problems: list[str] = []

    for entry in manifest.get("files", []):
        path = bundle / entry["path"]
        if not path.is_file():
            problems.append(f"missing: {entry['path']}")
        elif sha256(path) != entry["sha256"]:
            problems.append(f"checksum mismatch: {entry['path']}")
        elif path.stat().st_size != entry["bytes"]:
            problems.append(f"size mismatch: {entry['path']}")

    for raw in sums_path.read_text(encoding="utf-8").splitlines():
        expected, rel = raw.split("  ", 1)
        path = bundle / rel
        if not path.is_file():
            problems.append(f"SHA256SUMS missing: {rel}")
        elif sha256(path) != expected:
            problems.append(f"SHA256SUMS mismatch: {rel}")

    if problems:
        raise BundleError("; ".join(problems))
    return {
        "ok": True,
        "version": manifest.get("version"),
        "git_commit": manifest.get("git_commit"),
        "schema_head": manifest.get("schema_head"),
        "file_count": len(manifest.get("files", [])),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--release-id", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--bundle", type=Path, required=True)
    args = parser.parse_args()

    if args.command == "build":
        print(build_bundle(args.output.resolve(), args.release_id))
    else:
        print(json.dumps(verify_bundle(args.bundle.resolve()), indent=2))


if __name__ == "__main__":
    main()
