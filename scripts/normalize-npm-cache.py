#!/usr/bin/env python3
"""Normalize an npm cache index for deterministic, reproducible release artifacts.

npm's on-disk cache (``_cacache``) has two stores:

* ``content-v2`` — the tarball blobs, addressed by their integrity hash. These
  are already byte-for-byte deterministic for a given lock file.
* ``index-v5`` — the metadata bucket entries. Each line is ``<hash>\\t<json>``
  where the JSON carries volatile fetch metadata (``time``, ``metadata.time``,
  ``metadata.resHeaders.date``/``etag``). These change every time the cache is
  populated, so the raw cache is not reproducible.

This helper rewrites every index entry to a canonical form (``key``,
``integrity``, ``size``, and a zeroed ``time``) with the JSON produced by
``sort_keys`` and no volatile response metadata. The leading ``<hash>`` is a
function of ``key`` + ``integrity`` only, so it is preserved. The result is a
cache that (a) still satisfies ``npm ci --offline`` and (b) has the same bytes
for the same lock file on every build host.

Stdlib-only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

VOLATILE_TOP = ()  # reserved for future expansion; kept for clarity
ZERO_TIME = 0


def hash_entry(json_str: str) -> str:
    """cacache bucket hash: sha1 over the exact JSON string (see cacache
    lib/entry-index.js: ``hashEntry(stringified)``)."""
    return hashlib.sha1(json_str.encode("utf-8")).hexdigest()


def normalize_entry(hash_hex: str, raw_json: str) -> str:
    data = json.loads(raw_json)
    key = data.get("key")
    integrity = data.get("integrity")
    if not isinstance(key, str) or not isinstance(integrity, str):
        raise ValueError("cache index entry missing key/integrity")

    canonical: dict[str, object] = {
        "key": key,
        "integrity": integrity,
        "size": data.get("size", 0),
        "time": ZERO_TIME,
    }
    # The bucket hash is sha1 of the JSON string; recompute it for the
    # canonical form so cacache still accepts the entry (it re-validates on
    # read and ejects any line whose hash does not match).
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return f"{hash_entry(blob)}\t{blob}\n"


def normalize_index_file(path: Path) -> bytes:
    out_lines: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines(keepends=True):
        stripped = line.rstrip("\n")
        if not stripped.strip():
            out_lines.append(line if line.endswith("\n") else line + "\n")
            continue
        if "\t" not in stripped:
            raise ValueError(f"unexpected index line in {path}: {stripped!r}")
        hash_hex, raw_json = stripped.split("\t", 1)
        out_lines.append(normalize_entry(hash_hex, raw_json))
    return ("".join(out_lines)).encode("utf-8")


def normalize(cache_dir: Path) -> None:
    cacache = cache_dir / "_cacache"
    if not cacache.is_dir():
        raise ValueError(f"not an npm cache directory: {cache_dir}")

    # Drop volatile runtime state that must never be part of a release artifact.
    shutil.rmtree(cacache / "tmp", ignore_errors=True)
    shutil.rmtree(cache_dir / "_logs", ignore_errors=True)
    (cache_dir / "_update-notifier-last-checked").unlink(missing_ok=True)

    index_root = cacache / "index-v5"
    count = 0
    for path in sorted(index_root.rglob("*")):
        if path.is_dir():
            continue
        path.write_bytes(normalize_index_file(path))
        count += 1
    print(f"normalized {count} npm cache index entries")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cache_dir", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cache_dir = args.cache_dir.resolve()
    if not cache_dir.is_dir():
        print(f"ERROR: cache directory does not exist: {cache_dir}", file=sys.stderr)
        return 2
    try:
        normalize(cache_dir)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
