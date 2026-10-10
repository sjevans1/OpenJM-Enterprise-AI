#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
OUT_DIR="${1:-$BACKEND}"
PIP_TOOLS_VERSION="7.4.1"

python -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3,11) else 1)' || {
  echo "ERROR: Python 3.11 is required to compile OpenJM locks." >&2
  exit 2
}

python -m pip install --quiet "pip-tools==${PIP_TOOLS_VERSION}"
mkdir -p "$OUT_DIR"

COMMON=(
  --resolver=backtracking
  --generate-hashes
  --strip-extras
  --allow-unsafe
  --no-emit-index-url
  --no-emit-trusted-host
)

python -m piptools compile "${COMMON[@]}"   --output-file "$OUT_DIR/requirements.lock"   "$BACKEND/pyproject.toml"

python -m piptools compile "${COMMON[@]}"   --extra dev   --output-file "$OUT_DIR/requirements-dev.lock"   "$BACKEND/pyproject.toml"

echo "Generated:"
sha256sum "$OUT_DIR/requirements.lock" "$OUT_DIR/requirements-dev.lock"
