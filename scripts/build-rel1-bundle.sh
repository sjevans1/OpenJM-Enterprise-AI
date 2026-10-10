#!/usr/bin/env bash
# OpenJM Enterprise AI — assemble a REL1-B offline release bundle.
#
# Produces a staging directory matching docs/OFFLINE_BUNDLE.md:
#
#   openjm-rel1-<version>/
#     app/                       application/release source + operator assets
#     python/
#       requirements.lock        the committed production lock (verbatim)
#       wheelhouse/              hash-pinned production wheels (offline install)
#     npm/
#       cache/                   registry-independent npm cache from the lock
#     RELEASE-MANIFEST.json      sorted file list, sizes and SHA-256 digests
#     RELEASE-MANIFEST.sha256    SHA-256 of RELEASE-MANIFEST.json
#
# Usage:
#   scripts/build-rel1-bundle.sh [--version V] [--lock FILE] <output_dir>
#
#   --version V   must equal backend/app/version.py PRODUCT_VERSION (the value
#                 is read from that file; a mismatch is a hard error). Optional.
#   --lock FILE   hash-pinned Python lock to freeze (default: the committed
#                 production lock backend/requirements.lock).
#   <output_dir>  parent directory; the bundle is created inside it.
#
# The manifest is generated LAST, after the payload is complete, and the payload
# is verified immediately afterwards with the same stdlib tool.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INTEGRITY="$ROOT/scripts/release-integrity.py"
WHEELHOUSE_BUILDER="$ROOT/scripts/build-python-wheelhouse.sh"
NPM_CACHE_BUILDER="$ROOT/scripts/build-npm-offline-cache.sh"
STAGE_PAYLOAD="$ROOT/scripts/stage-release-payload.sh"

VERSION_ARG=""
LOCK_FILE="$ROOT/backend/requirements.lock"
OUTPUT_DIR=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) VERSION_ARG="${2:-}"; shift 2 ;;
    --lock) LOCK_FILE="${2:-}"; shift 2 ;;
    -h|--help) grep '^# ' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' | head -n 20; exit 0 ;;
    --*) echo "unknown argument: $1" >&2; exit 2 ;;
    *) OUTPUT_DIR="$1"; shift ;;
  esac
done

[[ -n "$OUTPUT_DIR" ]] || { echo "ERROR: output directory argument is required" >&2; exit 2; }
[[ -f "$LOCK_FILE" ]] || { echo "ERROR: lock file not found: $LOCK_FILE" >&2; exit 1; }

# --- Resolve the release version from the single source of truth -------------
# No hardcoded version: the value is read from backend/app/version.py.
VERSION="$(python3 - "$ROOT/backend/app/version.py" <<'PY'
import re, sys
text = open(sys.argv[1], encoding="utf-8").read()
m = re.search(r'^PRODUCT_VERSION\s*=\s*"([^"]+)"', text, re.MULTILINE)
if not m:
    sys.exit("unable to read PRODUCT_VERSION from version.py")
print(m.group(1))
PY
)" || { echo "ERROR: could not determine PRODUCT_VERSION" >&2; exit 1; }

if [[ -n "$VERSION_ARG" && "$VERSION_ARG" != "$VERSION" ]]; then
  echo "ERROR: --version $VERSION_ARG does not match PRODUCT_VERSION $VERSION" >&2
  exit 2
fi
echo "== OpenJM REL1 bundle =="
echo "version:   $VERSION"
echo "lock:      $LOCK_FILE"

# --- Guard: never stage into the repository tree ----------------------------
OUT_REAL="$(readlink -f "$OUTPUT_DIR" 2>/dev/null || echo "$OUTPUT_DIR")"
case "$OUT_REAL/" in
  "$ROOT"/*) echo "ERROR: output directory must be outside the repository tree" >&2; exit 2 ;;
esac

BUNDLE="$OUTPUT_DIR/openjm-rel1-$VERSION"
rm -rf "$BUNDLE"
mkdir -p "$BUNDLE"

# --- Locate a Python 3.11 interpreter for the wheelhouse --------------------
PY311=""
for candidate in "${OPENJM_PYTHON311:-}" python3.11 python3 python; do
  [[ -n "$candidate" ]] || continue
  if command -v "$candidate" >/dev/null 2>&1 && \
     "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3,11) else 1)' 2>/dev/null; then
    PY311="$(command -v "$candidate")"; break
  fi
done
[[ -n "$PY311" ]] || { echo "ERROR: Python 3.11 is required to build the wheelhouse" >&2; exit 2; }

# build-python-wheelhouse.sh invokes `python`; shim `python` -> the 3.11 binary.
SHIM_DIR="$(mktemp -d "${TMPDIR:-/tmp}/openjm-py-shim.XXXXXX")"
ln -sf "$PY311" "$SHIM_DIR/python"
trap 'rm -rf "$SHIM_DIR"' EXIT

# --- 1. application / operator payload --------------------------------------
"$STAGE_PAYLOAD" "$ROOT" "$BUNDLE/app"

# --- 2. python: committed lock (verbatim) + hash-pinned wheelhouse ----------
mkdir -p "$BUNDLE/python/wheelhouse"
cp "$LOCK_FILE" "$BUNDLE/python/requirements.lock"
echo "copied production lock -> python/requirements.lock"
PATH="$SHIM_DIR:$PATH" "$WHEELHOUSE_BUILDER" "$BUNDLE/python/wheelhouse" "$LOCK_FILE"

# --- 3. npm: registry-independent cache from the committed frontend lock -----
"$NPM_CACHE_BUILDER" "$BUNDLE/npm/cache" "$ROOT/frontend"

# --- 4. manifest LAST, then verify immediately ------------------------------
echo "== finalize =="
python3 "$INTEGRITY" generate "$BUNDLE" --repo-root "$ROOT"
python3 "$INTEGRITY" verify "$BUNDLE"

MANIFEST_SHA="$(cut -d' ' -f1 "$BUNDLE/RELEASE-MANIFEST.sha256")"
echo "bundle assembled: $BUNDLE"
echo "manifest sha256: $MANIFEST_SHA"
