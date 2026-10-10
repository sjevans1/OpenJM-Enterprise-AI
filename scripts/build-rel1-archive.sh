#!/usr/bin/env bash
# OpenJM Enterprise AI — deterministically archive a verified REL1 bundle.
#
# REL1-B4 (B4-F): turn an assembled bundle directory into a single, byte-for-
# byte reproducible `openjm-rel1-<version>.tar.gz`. The archive is only produced
# AFTER the bundle has been verified with scripts/release-integrity.py, so an
# unverified (tampered, incomplete or unexpected) payload is never sealed into a
# distributable artifact.
#
# Usage:
#   scripts/build-rel1-archive.sh [--expected-manifest-sha256 SHA256] [--output-dir DIR] <bundle_dir>
#
#   <bundle_dir>  an assembled bundle directory that ALREADY contains
#                 RELEASE-MANIFEST.json and RELEASE-MANIFEST.sha256.
#   --expected-manifest-sha256 SHA256
#                 optional trusted manifest digest (out-of-band trust anchor).
#                 When supplied it is passed straight through to the verifier,
#                 which fails closed on a mismatch.
#   --output-dir DIR
#                 directory to write the archive into. Defaults to
#                 ${OPENJM_ARCHIVE_DIR:-${TMPDIR:-/tmp}}, which is outside the
#                 repository tree. Writing into the repository is refused.
#
# Determinism guarantees (the archive is reproducible for a given payload):
#   * entries are sorted        (--sort=name)
#   * every mtime is epoch 0    (--mtime=@0)
#   * uid/gid/owner are 0/root  (--numeric-owner --owner=0 --group=0)
#   * stable container format   (--format=gnu)
#   * no gzip timestamp/name    (gzip -n)
#   * archive members are relative to the bundle's top-level directory
#     `openjm-rel1-<version>/`, so no machine-specific absolute path leaks in.
#
# The version is read from backend/app/version.py PRODUCT_VERSION — never
# hardcoded. Archives are large build artifacts and are never committed to Git.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INTEGRITY="$ROOT/scripts/release-integrity.py"
VERSION_FILE="$ROOT/backend/app/version.py"

BUNDLE_DIR=""
OUTPUT_DIR="${OPENJM_ARCHIVE_DIR:-${TMPDIR:-/tmp}}"
TRUSTED_SHA=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --expected-manifest-sha256) TRUSTED_SHA="${2:-}"; shift 2 ;;
    --output-dir) OUTPUT_DIR="${2:-}"; shift 2 ;;
    -h|--help) grep '^# ' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' | head -n 30; exit 0 ;;
    --*) echo "unknown argument: $1" >&2; exit 2 ;;
    *) BUNDLE_DIR="$1"; shift ;;
  esac
done

[[ -n "$BUNDLE_DIR" ]] || { echo "ERROR: bundle directory argument is required" >&2; exit 2; }
[[ -d "$BUNDLE_DIR" ]] || { echo "ERROR: bundle directory not found: $BUNDLE_DIR" >&2; exit 2; }
BUNDLE_REAL="$(readlink -f "$BUNDLE_DIR")"

# --- Resolve the release version from the single source of truth -------------
VERSION="$(python3 - "$VERSION_FILE" <<'PY'
import re, sys
text = open(sys.argv[1], encoding="utf-8").read()
m = re.search(r'^PRODUCT_VERSION\s*=\s*"([^"]+)"', text, re.MULTILINE)
if not m:
    sys.exit("unable to read PRODUCT_VERSION from version.py")
print(m.group(1))
PY
)" || { echo "ERROR: could not determine PRODUCT_VERSION" >&2; exit 1; }

# --- Guard: never write the archive into the repository tree -----------------
OUT_REAL="$(readlink -f "$OUTPUT_DIR" 2>/dev/null || echo "$OUTPUT_DIR")"
case "$OUT_REAL/" in
  "$ROOT"/*) echo "ERROR: output directory must be outside the repository tree" >&2; exit 2 ;;
esac
mkdir -p "$OUTPUT_DIR"

# --- Verify the payload BEFORE archiving anything ---------------------------
echo "== verify bundle ($BUNDLE_REAL) =="
if [[ -n "$TRUSTED_SHA" ]]; then
  python3 "$INTEGRITY" verify "$BUNDLE_REAL" --expected-manifest-sha256 "$TRUSTED_SHA" \
    || { echo "ERROR: bundle verification failed; refusing to archive" >&2; exit 1; }
else
  python3 "$INTEGRITY" verify "$BUNDLE_REAL" \
    || { echo "ERROR: bundle verification failed; refusing to archive" >&2; exit 1; }
fi

MANIFEST_SHA="$(sha256sum "$BUNDLE_REAL/RELEASE-MANIFEST.json" | awk '{print $1}')"

# --- Deterministic archive ---------------------------------------------------
BUNDLE_PARENT="$(dirname "$BUNDLE_REAL")"
BUNDLE_NAME="$(basename "$BUNDLE_REAL")"
ENTRY_PREFIX="openjm-rel1-$VERSION"
ARCHIVE="$OUTPUT_DIR/$ENTRY_PREFIX.tar.gz"

# Members are the bundle's top-level directory renamed to the canonical
# `openjm-rel1-<version>/`; -C makes every entry relative (no absolute paths).
# All volatile metadata is pinned so two runs over the same payload are
# byte-identical.
tar -C "$BUNDLE_PARENT" \
  --sort=name \
  --mtime=@0 \
  --numeric-owner --owner=0 --group=0 \
  --format=gnu \
  --transform "s,^${BUNDLE_NAME},${ENTRY_PREFIX}," \
  -cf - "$BUNDLE_NAME" | gzip -n > "$ARCHIVE"

ARCHIVE_BYTES="$(stat -c '%s' "$ARCHIVE")"
ARCHIVE_SHA="$(sha256sum "$ARCHIVE" | awk '{print $1}')"

echo "archive:        $ARCHIVE"
echo "archive bytes:  $ARCHIVE_BYTES"
echo "archive sha256: $ARCHIVE_SHA"
echo "manifest sha256:$MANIFEST_SHA"
