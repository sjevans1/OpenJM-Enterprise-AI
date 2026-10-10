#!/usr/bin/env python3
"""Generate or verify an OpenJM REL1 release payload manifest.

Stdlib-only by design so integrity verification does not depend on the
application environment that is being installed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path, PurePosixPath

MANIFEST_NAME = "RELEASE-MANIFEST.json"
MANIFEST_SHA_NAME = "RELEASE-MANIFEST.sha256"
SCHEMA_VERSION = 1
RESERVED = {MANIFEST_NAME, MANIFEST_SHA_NAME}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_relpath(root: Path, path: Path) -> str:
    rel = path.relative_to(root)
    posix = PurePosixPath(*rel.parts)
    if posix.is_absolute() or ".." in posix.parts:
        raise ValueError(f"unsafe release path: {rel}")
    return posix.as_posix()


def payload_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"symlinks are not allowed in release payloads: {path}")
        if path.is_dir():
            continue
        rel = canonical_relpath(root, path)
        if rel in RESERVED:
            continue
        files.append(path)
    return files


def load_release_identity(repo_root: Path) -> tuple[str, str]:
    version_file = repo_root / "backend" / "app" / "version.py"
    text = version_file.read_text(encoding="utf-8")
    product = re.search(r'^PRODUCT_VERSION\s*=\s*"([^"]+)"', text, re.MULTILINE)
    train = re.search(r'^RELEASE_TRAIN\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not product or not train:
        raise ValueError("unable to read PRODUCT_VERSION/RELEASE_TRAIN")
    return product.group(1), train.group(1)


def build_manifest(bundle_root: Path, repo_root: Path) -> dict:
    product_version, release_train = load_release_identity(repo_root)
    entries = []
    for path in payload_files(bundle_root):
        rel = canonical_relpath(bundle_root, path)
        entries.append(
            {
                "path": rel,
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "product_version": product_version,
        "release_train": release_train,
        "files": entries,
    }


def atomic_write(path: Path, data: bytes) -> None:
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_bytes(data)
    os.replace(temp, path)


def generate(bundle_root: Path, repo_root: Path) -> None:
    manifest = build_manifest(bundle_root, repo_root)
    raw = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    manifest_path = bundle_root / MANIFEST_NAME
    atomic_write(manifest_path, raw)
    manifest_sha = hashlib.sha256(raw).hexdigest()
    atomic_write(
        bundle_root / MANIFEST_SHA_NAME,
        f"{manifest_sha}  {MANIFEST_NAME}\n".encode("ascii"),
    )
    print(f"generated {MANIFEST_NAME}: {len(manifest['files'])} payload files")
    print(f"manifest sha256: {manifest_sha}")


def validate_manifest_entry(entry: object) -> tuple[str, int, str]:
    if not isinstance(entry, dict):
        raise ValueError("manifest file entry must be an object")
    path = entry.get("path")
    size = entry.get("size")
    digest = entry.get("sha256")
    if not isinstance(path, str) or not path or "\\" in path:
        raise ValueError("invalid manifest path")
    pure = PurePosixPath(path)
    if pure.is_absolute() or ".." in pure.parts or path in RESERVED:
        raise ValueError(f"unsafe manifest path: {path}")
    if not isinstance(size, int) or size < 0:
        raise ValueError(f"invalid size for {path}")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError(f"invalid sha256 for {path}")
    return path, size, digest


def verify(bundle_root: Path, expected_manifest_sha256: str | None) -> None:
    manifest_path = bundle_root / MANIFEST_NAME
    checksum_path = bundle_root / MANIFEST_SHA_NAME
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ValueError(f"missing or unsafe {MANIFEST_NAME}")
    if not checksum_path.is_file() or checksum_path.is_symlink():
        raise ValueError(f"missing or unsafe {MANIFEST_SHA_NAME}")

    raw = manifest_path.read_bytes()
    actual_manifest_sha = hashlib.sha256(raw).hexdigest()

    checksum_line = checksum_path.read_text(encoding="ascii").strip()
    match = re.fullmatch(r"([0-9a-f]{64})\s{2}RELEASE-MANIFEST\.json", checksum_line)
    if not match:
        raise ValueError(f"invalid {MANIFEST_SHA_NAME} format")
    if match.group(1) != actual_manifest_sha:
        raise ValueError("release manifest checksum mismatch")
    if expected_manifest_sha256 and actual_manifest_sha != expected_manifest_sha256.lower():
        raise ValueError("release manifest does not match trusted expected sha256")

    manifest = json.loads(raw)
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported release manifest schema")

    listed: dict[str, tuple[int, str]] = {}
    entries = manifest.get("files")
    if not isinstance(entries, list):
        raise ValueError("manifest files must be a list")
    for entry in entries:
        path, size, digest = validate_manifest_entry(entry)
        if path in listed:
            raise ValueError(f"duplicate manifest path: {path}")
        listed[path] = (size, digest)

    actual_paths = {canonical_relpath(bundle_root, p) for p in payload_files(bundle_root)}
    listed_paths = set(listed)
    missing = sorted(listed_paths - actual_paths)
    unexpected = sorted(actual_paths - listed_paths)
    if missing:
        raise ValueError(f"missing payload files: {', '.join(missing)}")
    if unexpected:
        raise ValueError(f"unexpected payload files: {', '.join(unexpected)}")

    for rel, (expected_size, expected_sha) in sorted(listed.items()):
        path = bundle_root.joinpath(*PurePosixPath(rel).parts)
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"unsafe payload file: {rel}")
        if path.stat().st_size != expected_size:
            raise ValueError(f"size mismatch: {rel}")
        if sha256_file(path) != expected_sha:
            raise ValueError(f"sha256 mismatch: {rel}")

    print(f"verified release payload: {len(listed)} files")
    print(f"manifest sha256: {actual_manifest_sha}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate")
    gen.add_argument("bundle", type=Path)
    gen.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])

    check = sub.add_parser("verify")
    check.add_argument("bundle", type=Path)
    check.add_argument("--expected-manifest-sha256")

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    bundle_root = args.bundle.resolve()
    if not bundle_root.is_dir():
        print(f"ERROR: bundle directory does not exist: {bundle_root}", file=sys.stderr)
        return 2
    try:
        if args.command == "generate":
            generate(bundle_root, args.repo_root.resolve())
        else:
            verify(bundle_root, args.expected_manifest_sha256)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
