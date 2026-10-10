#!/usr/bin/env python3
"""REL1-A post-install verification.

Uses only the Python standard library so it can run immediately after the
backend environment is installed. It verifies release identity and, unless
--static-only is used, the expected local install artifacts.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"


def fail(message: str) -> None:
    raise SystemExit(f"REL1-A VERIFY FAILED: {message}")


def _read_env_profile() -> str | None:
    env_file = ROOT / ".env"
    if not env_file.is_file():
        return None
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "OPENJM_DEPLOYMENT_PROFILE":
            return value.strip().strip('"').strip("'")
    return None


def verify_release_identity() -> str:
    sys.path.insert(0, str(BACKEND))
    from app.version import PRODUCT_VERSION, RELEASE_TRAIN

    with (BACKEND / "pyproject.toml").open("rb") as handle:
        backend_version = tomllib.load(handle)["project"]["version"]

    frontend_package = json.loads(
        (FRONTEND / "package.json").read_text(encoding="utf-8")
    )
    frontend_lock = json.loads(
        (FRONTEND / "package-lock.json").read_text(encoding="utf-8")
    )
    versions = {
        "runtime": PRODUCT_VERSION,
        "backend_package": backend_version,
        "frontend_package": frontend_package["version"],
        "frontend_lock": frontend_lock["version"],
        "frontend_lock_root": frontend_lock["packages"][""]["version"],
    }
    if len(set(versions.values())) != 1:
        fail(f"release versions disagree: {versions}")
    if RELEASE_TRAIN != "REL1":
        fail(f"release train must be REL1, found {RELEASE_TRAIN!r}")
    return PRODUCT_VERSION


def verify_toolchain(*, skip_frontend: bool) -> None:
    if sys.version_info[:2] != (3, 11):
        fail(f"Python 3.11 required, found {sys.version.split()[0]}")
    if skip_frontend:
        return
    try:
        node = subprocess.run(
            ["node", "-p", "process.versions.node"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        fail(f"Node.js 22 is required: {exc}")
    if node.split(".", 1)[0] != "22":
        fail(f"Node.js 22 required, found {node}")


def verify_install(*, profile: str, skip_frontend: bool) -> None:
    if not (ROOT / ".env").is_file():
        fail(".env is missing")
    if not (BACKEND / ".venv").is_dir():
        fail("backend/.venv is missing")
    if not skip_frontend and not (FRONTEND / "dist").is_dir():
        fail("frontend/dist is missing")
    configured_profile = _read_env_profile()
    if configured_profile and configured_profile != profile:
        fail(
            f"requested profile {profile!r} disagrees with .env "
            f"OPENJM_DEPLOYMENT_PROFILE={configured_profile!r}"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--profile", choices=("development", "production"), default="development"
    )
    parser.add_argument("--skip-frontend", action="store_true")
    parser.add_argument(
        "--static-only",
        action="store_true",
        help="verify release identity/toolchain only; do not require an installed tree",
    )
    args = parser.parse_args()

    version = verify_release_identity()
    verify_toolchain(skip_frontend=args.skip_frontend)
    if not args.static_only:
        verify_install(profile=args.profile, skip_frontend=args.skip_frontend)

    print(
        f"REL1-A VERIFY OK: version={version} profile={args.profile} "
        f"frontend={'skipped' if args.skip_frontend else 'present'}"
    )


if __name__ == "__main__":
    main()
