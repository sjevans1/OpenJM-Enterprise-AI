#!/usr/bin/env bash
# OpenJM Enterprise AI — build a deterministic, registry-independent npm cache.
#
# REL1-B3: frontend/package-lock.json stays authoritative. This script turns it
# into a release-local npm cache directory that can satisfy `npm ci` with no
# access to the public registry, and that is byte-for-byte reproducible for a
# given lock file.
#
# Usage:
#   scripts/build-npm-offline-cache.sh <out_cache_dir> [frontend_dir]
#
#   <out_cache_dir>  directory to populate (created if absent; wiped first)
#   [frontend_dir]   source of package.json/package-lock.json (default frontend/)
#
# Approach (proven, not assumed):
#   1. copy package.json + package-lock.json into an isolated workdir;
#   2. `npm ci --cache <out>` once to fetch and populate the cache;
#   3. prove the cache alone supports a registry-blocked `npm ci --offline`;
#   4. normalize the cache index (scripts/normalize-npm-cache.py) so the bytes
#      are reproducible, and emit a deterministic cache digest.
#
# The populated cache is a build artifact and is never committed to Git.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NORMALIZER="$ROOT/scripts/normalize-npm-cache.py"

OUT_DIR="${1:-}"
FRONTEND_DIR="${2:-$ROOT/frontend}"
[[ -n "$OUT_DIR" ]] || { echo "ERROR: output cache directory argument is required" >&2; exit 2; }

# A registry that refuses connections: proves the cache is the only source.
BLOCKED_REGISTRY="http://127.0.0.1:9/"

command -v node >/dev/null 2>&1 || { echo "ERROR: node is required" >&2; exit 2; }
command -v npm >/dev/null 2>&1 || { echo "ERROR: npm is required" >&2; exit 2; }
NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]')"
[[ "$NODE_MAJOR" -eq 22 ]] || { echo "ERROR: Node.js 22 is required (found $(node --version))" >&2; exit 2; }

for f in package.json package-lock.json; do
  [[ -f "$FRONTEND_DIR/$f" ]] || { echo "ERROR: missing $FRONTEND_DIR/$f" >&2; exit 1; }
done

# Locate a Python 3.11 interpreter for the stdlib normalizer; any python3 works.
PY=""
for candidate in python3.11 python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then PY="$candidate"; break; fi
done
[[ -n "$PY" ]] || { echo "ERROR: python3 is required for cache normalization" >&2; exit 2; }

rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"

WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/openjm-npmcache.XXXXXX")"
cleanup() { rm -rf "$WORKDIR"; }
trap cleanup EXIT

echo "== OpenJM npm offline cache =="
echo "lock:      $FRONTEND_DIR/package-lock.json"
echo "output:    $OUT_DIR"
echo "node:      $(node --version)  npm: $(npm --version)"

# --- 1. populate the cache from the lock (network required for this step) ----
POPULATE_DIR="$WORKDIR/populate"
mkdir -p "$POPULATE_DIR"
cp "$FRONTEND_DIR/package.json" "$FRONTEND_DIR/package-lock.json" "$POPULATE_DIR/"
(
  cd "$POPULATE_DIR"
  env -u npm_config_offline -u npm_config_registry -u npm_config_cache \
    npm ci --cache "$OUT_DIR" --no-audit --no-fund
)
echo "populated cache from package-lock.json"

# --- 2. prove the cache alone satisfies a registry-blocked offline install ---
CHECK_DIR="$WORKDIR/check"
mkdir -p "$CHECK_DIR"
cp "$FRONTEND_DIR/package.json" "$FRONTEND_DIR/package-lock.json" "$CHECK_DIR/"
(
  cd "$CHECK_DIR"
  env npm_config_registry="$BLOCKED_REGISTRY" npm_config_offline=true \
    npm ci --cache "$OUT_DIR" --no-audit --no-fund
)
echo "registry-blocked offline 'npm ci' succeeded using only $OUT_DIR"

# --- 3. normalize for byte-for-byte reproducibility -------------------------
"$PY" "$NORMALIZER" "$OUT_DIR"

# Deterministic digest of the cache: sha256 over sorted "<relpath> <file-sha256>".
CACHE_DIGEST="$(
  cd "$OUT_DIR" && find . -type f ! -name '.openjm-cache-digest' -print0 \
    | sort -z \
    | xargs -0 sha256sum \
    | sha256sum \
    | awk '{print $1}'
)"
printf '%s\n' "$CACHE_DIGEST" > "$OUT_DIR/.openjm-cache-digest"
echo "cache deterministic digest: $CACHE_DIGEST"
echo "cache size: $(du -sh "$OUT_DIR" | awk '{print $1}')"
echo "npm offline cache populated: $OUT_DIR"
