#!/usr/bin/env bash
# OpenJM Enterprise AI — repeatable Linux install / bootstrap (VS8 Workstream B).
#
# Idempotent and fail-early. Never edits source, never overwrites an existing
# .env or production data, and never depends on a developer home directory,
# Hermes state or a previous test service.
#
# Usage:
#   scripts/install-linux.sh [--profile development|production] [--skip-frontend]
set -euo pipefail

PROFILE="development"
SKIP_FRONTEND=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile) PROFILE="$2"; shift 2 ;;
    --skip-frontend) SKIP_FRONTEND=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
echo "== OpenJM install ($PROFILE profile) at $ROOT =="

fail() { echo "ERROR: $*" >&2; exit 1; }

# --- 1. Prerequisites (version-checked, fail early) -------------------------
command -v git >/dev/null 2>&1 || fail "git is required"
PY=""
for candidate in python3.11 python3.12 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info[:2]==(3,11) else 1)' 2>/dev/null; then
      PY="$candidate"; break
    fi
  fi
done
[[ -n "$PY" ]] || fail "Python 3.11 is required (DB-GPT pins aiohttp 3.8.4; 3.12 will not build)"
echo "python: $($PY --version)"

if [[ "$SKIP_FRONTEND" -eq 0 ]]; then
  command -v node >/dev/null 2>&1 || fail "Node.js 22 is required for the frontend"
  NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]')"
  [[ "$NODE_MAJOR" -ge 20 ]] || fail "Node 20+ required (found $(node --version))"
  command -v npm >/dev/null 2>&1 || fail "npm is required"
fi

# --- 2. Configuration (copy template, never overwrite) ----------------------
if [[ -f .env ]]; then
  echo ".env present; leaving untouched"
else
  if [[ "$PROFILE" == "production" ]]; then
    cp .env.production.example .env
    echo "created .env from .env.production.example — EDIT IT with real secrets before start"
  else
    cp .env.example .env
    echo "created .env from .env.example"
  fi
fi

# --- 3. Backend dependencies in a repo-local venv ---------------------------
cd backend
if [[ ! -d .venv ]]; then
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --quiet --upgrade pip
python -m pip install --quiet --prefer-binary -e ".[dev]"
echo "backend dependencies installed"

# --- 4. Configuration preflight (fail closed on a production misfit) --------
python -m app.core.preflight || fail "configuration preflight failed; fix .env before starting"

# --- 5. Migrate the application-metadata database ---------------------------
python ../scripts/openjm_ops.py upgrade --check || true
python ../scripts/openjm_ops.py upgrade
echo "application database migrated"

# --- 6. Frontend build ------------------------------------------------------
cd "$ROOT"
if [[ "$SKIP_FRONTEND" -eq 0 ]]; then
  (cd frontend && npm ci --no-audit --no-fund && npm run build)
  echo "frontend built into frontend/dist"
fi

cat <<'EOF'

== Install complete ==
Start the backend:
  cd backend && source .venv/bin/activate && \
    env -u PYTHONPATH -u PYTHONHOME .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
Serve the built frontend behind your reverse proxy/TLS boundary (see docs/INSTALLATION.md).
Verify: curl -s http://127.0.0.1:8000/api/ready
EOF
