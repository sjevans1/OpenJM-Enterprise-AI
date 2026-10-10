#!/usr/bin/env python3
"""Verify an installed REL1-B payload without requiring Node/npm."""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path


def fail(message: str) -> None:
    raise SystemExit(f"REL1-B VERIFY FAILED: {message}")


def env_profile(root: Path) -> str | None:
    env_file = root / ".env"
    if not env_file.is_file():
        return None
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("OPENJM_DEPLOYMENT_PROFILE="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--profile", choices=("development", "production"), required=True)
    args = parser.parse_args()
    root = args.root.resolve()

    backend = root / "backend"
    frontend = root / "frontend"
    sys.path.insert(0, str(backend))
    from app.version import PRODUCT_VERSION, RELEASE_TRAIN

    with (backend / "pyproject.toml").open("rb") as handle:
        backend_version = tomllib.load(handle)["project"]["version"]
    frontend_version = json.loads(
        (frontend / "package.json").read_text(encoding="utf-8")
    )["version"]

    if PRODUCT_VERSION != backend_version or PRODUCT_VERSION != frontend_version:
        fail("release identity mismatch")
    if RELEASE_TRAIN != "REL1":
        fail(f"unexpected release train {RELEASE_TRAIN}")
    if not (backend / ".venv").is_dir():
        fail("backend virtual environment missing")
    if not (frontend / "dist" / "index.html").is_file():
        fail("prebuilt frontend missing")
    configured = env_profile(root)
    if configured and configured != args.profile:
        fail(f"profile mismatch: requested={args.profile}, configured={configured}")

    print(
        f"REL1-B VERIFY OK: version={PRODUCT_VERSION} profile={args.profile} "
        "frontend=prebuilt"
    )


if __name__ == "__main__":
    main()
