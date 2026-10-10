#!/usr/bin/env bash
# OpenJM Enterprise AI — stage the application/release source payload.
#
# Copies the repository source and operator assets into a release payload
# directory under the REL1-B exclusion policy, so a release bundle never
# carries local state, secrets, or build/test detritus.
#
# Usage:
#   scripts/stage-release-payload.sh <src_dir> <dest_dir>
#
# Included: application/release source and operator assets (backend, frontend
# source, scripts, deploy assets, docs, committed configuration templates).
#
# Excluded (must never cross the air gap):
#   .git, .hermes (Hermes state), real env/secret files (.env, .env.local, ...),
#   developer virtualenvs (.venv*), node_modules, build output (dist), Python
#   and test caches (__pycache__, *.pyc, .pytest_cache, .coverage, htmlcov),
#   generated private data (data/, *.db, *.sqlite3), and compose secrets.
#
# The committed configuration *templates* (.env.example,
# .env.production.example) ARE retained: scripts/install-linux.sh copies one of
# them to create .env on the target, so the offline install path needs them.
# They contain no secrets.
set -euo pipefail

SRC="${1:-}"
DEST="${2:-}"
[[ -n "$SRC" && -n "$DEST" ]] || {
  echo "usage: $0 <src_dir> <dest_dir>" >&2
  exit 2
}
[[ -d "$SRC" ]] || { echo "ERROR: source directory not found: $SRC" >&2; exit 1; }

command -v rsync >/dev/null 2>&1 || { echo "ERROR: rsync is required" >&2; exit 2; }

mkdir -p "$DEST"

rsync -a --delete \
  --include='.env.example' \
  --include='.env.production.example' \
  --exclude='.env' \
  --exclude='.env.*' \
  --exclude='.git/' \
  --exclude='.git' \
  --exclude='.hermes/' \
  --exclude='.hermes' \
  --exclude='.venv' \
  --exclude='.venv*/' \
  --exclude='venv/' \
  --exclude='node_modules/' \
  --exclude='dist/' \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  --exclude='*.pyo' \
  --exclude='*.egg-info/' \
  --exclude='.pytest_cache/' \
  --exclude='.ruff_cache/' \
  --exclude='.mypy_cache/' \
  --exclude='.coverage' \
  --exclude='htmlcov/' \
  --exclude='*.db' \
  --exclude='*.sqlite' \
  --exclude='*.sqlite3' \
  --exclude='data/' \
  --exclude='deploy/compose/secrets/' \
  --exclude='.DS_Store' \
  --exclude='*.log' \
  "$SRC"/. "$DEST"/

echo "staged release payload: $DEST"
