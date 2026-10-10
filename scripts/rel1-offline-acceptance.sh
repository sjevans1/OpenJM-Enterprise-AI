#!/usr/bin/env bash
# OpenJM Enterprise AI — registry-blocked acceptance for an assembled bundle.
#
# Runs the offline install/build exactly as a target host would, using ONLY the
# artifacts inside the release bundle, with public registries disabled:
#
#   * verifies the bundle manifest first (fail closed before doing anything);
#   * Python: installs the production lock from python/wheelhouse with
#     `--no-index` (no PyPI), `--find-links`, and `--require-hashes`;
#   * npm: installs the frontend from npm/cache with `--offline` and a
#     connection-refusing registry, then runs the production build.
#
# This is the property that defines "offline": an install that succeeds with
# the network path refused. It is the acceptance gate REL1-B4 will run on a
# real host; the CI job exercises the same script at STRUCTURAL level.
#
# Usage:
#   scripts/rel1-offline-acceptance.sh <bundle_dir> \
#       [--python install|dry-run|skip] [--npm build|ci|skip]
#
# Defaults: --python install --npm build.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INTEGRITY="$ROOT/scripts/release-integrity.py"
BLOCKED_REGISTRY="http://127.0.0.1:9/"

BUNDLE="${1:-}"
[[ -n "$BUNDLE" ]] || { echo "usage: $0 <bundle_dir> [--python ...] [--npm ...]" >&2; exit 2; }
shift || true
PY_MODE="install"
NPM_MODE="build"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --python) PY_MODE="${2:-}"; shift 2 ;;
    --npm) NPM_MODE="${2:-}"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ -f "$BUNDLE/RELEASE-MANIFEST.json" ]] || { echo "ERROR: not a bundle: $BUNDLE" >&2; exit 1; }

# Track temp dirs so a single EXIT trap cleans all of them up.
CLEANUP_DIRS=()
cleanup() {
  local d
  for d in "${CLEANUP_DIRS[@]:-}"; do
    [[ -n "$d" ]] && rm -rf "$d"
  done
}
trap cleanup EXIT

echo "== REL1 offline acceptance =="
echo "bundle: $BUNDLE"

# --- 0. trust gate: verify the manifest before touching the payload ----------
python3 "$INTEGRITY" verify "$BUNDLE"

find_python311() {
  for candidate in "${OPENJM_PYTHON311:-}" python3.11 python3 python; do
    [[ -n "$candidate" ]] || continue
    if command -v "$candidate" >/dev/null 2>&1 && \
       "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3,11) else 1)' 2>/dev/null; then
      command -v "$candidate"; return 0
    fi
  done
  return 1
}

# --- 1. Python: hash-checked, index-free install from the wheelhouse ----------
if [[ "$PY_MODE" != "skip" ]]; then
  PY311="$(find_python311)" || { echo "ERROR: Python 3.11 required" >&2; exit 2; }
  VENV_PARENT="$(mktemp -d "${TMPDIR:-/tmp}/openjm-offline-venv.XXXXXX")"
  CLEANUP_DIRS+=("$VENV_PARENT")
  VENV="$VENV_PARENT/venv"
  "$PY311" -m venv "$VENV"

  PIP_ARGS=(
    install
    --no-index
    --find-links "$BUNDLE/python/wheelhouse"
    --require-hashes
    -r "$BUNDLE/python/requirements.lock"
  )
  if [[ "$PY_MODE" == "dry-run" ]]; then
    PIP_ARGS+=(--dry-run)
  fi
  echo "-- python: pip ${PY_MODE} (no-index, find-links wheelhouse, require-hashes) --"
  "$VENV/bin/python" -m pip "${PIP_ARGS[@]}"
  echo "python offline ${PY_MODE}: OK"
fi

# --- 2. npm: registry-blocked, offline install + build from the release cache -
if [[ "$NPM_MODE" != "skip" ]]; then
  command -v node >/dev/null 2>&1 || { echo "ERROR: node required" >&2; exit 2; }
  [[ "$(node -p 'process.versions.node.split(".")[0]')" -eq 22 ]] || {
    echo "ERROR: Node.js 22 required" >&2; exit 2;
  }
  WORK="$(mktemp -d "${TMPDIR:-/tmp}/openjm-offline-npm.XXXXXX")"
  CLEANUP_DIRS+=("$WORK")
  # The bundle carries the frontend source under app/frontend.
  cp -a "$BUNDLE/app/frontend/." "$WORK/"
  rm -rf "$WORK/node_modules"

  echo "-- npm: registry-blocked offline ci --"
  (
    cd "$WORK"
    env npm_config_registry="$BLOCKED_REGISTRY" npm_config_offline=true \
      npm ci --cache "$BUNDLE/npm/cache" --no-audit --no-fund
  )
  if [[ "$NPM_MODE" == "build" ]]; then
    echo "-- npm: production build --"
    (
      cd "$WORK"
      env npm_config_registry="$BLOCKED_REGISTRY" npm_config_offline=true \
        npm run build
    )
    [[ -f "$WORK/dist/index.html" ]] || { echo "ERROR: no dist/index.html produced" >&2; exit 1; }
  fi
  echo "npm offline ${NPM_MODE}: OK"
fi

echo "== REL1 offline acceptance passed (registry-blocked) =="
