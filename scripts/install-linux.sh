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

[[ "$PROFILE" == "development" || "$PROFILE" == "production" ]] || {
  echo "ERROR: --profile must be development or production" >&2
  exit 2
}

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
  [[ "$NODE_MAJOR" -eq 22 ]] || fail "Node.js 22 is required (found $(node --version))"
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
echo "pip: $(python -m pip --version)"

# Committed hash-pinned locks are the dependency authority. Production installs
# the exact production lock (no dev extras); development installs the exact
# development lock. Both enforce hashes so a tampered or unlisted wheel fails.
if [[ "$PROFILE" == "production" ]]; then
  LOCK_FILE="requirements.lock"
else
  LOCK_FILE="requirements-dev.lock"
fi
[[ -f "$LOCK_FILE" ]] || fail "committed lock missing: backend/$LOCK_FILE"

# The release bundle carries python/ as a SIBLING of the app tree (this script
# runs from the app root, app/): python/requirements.lock, python/wheelhouse/
# and python/app/<application wheel>. When present, install strictly from the
# bundled wheelhouse so the offline host never reaches a package index.
BUNDLE_PY="$ROOT/../python"
LOCK_EXTRA=()
if [[ -d "$BUNDLE_PY/wheelhouse" ]]; then
  LOCK_EXTRA+=(--no-index --find-links "$BUNDLE_PY/wheelhouse")
fi
python -m pip install --quiet --require-hashes -r "$LOCK_FILE" "${LOCK_EXTRA[@]}"

# Application install. Production installs the pre-built application wheel that
# ships in the bundle (python/app/openjm_enterprise_ai_backend-<version>-*.whl):
# the backend build backend (hatchling) is deliberately absent from the runtime
# wheelhouse, so an editable or build-isolated install cannot run on a
# registry-blocked host. Development keeps the editable source install.
if [[ "$PROFILE" == "production" ]]; then
  mapfile -t APP_WHEELS < <(find "$BUNDLE_PY/app" -maxdepth 1 -type f -name 'openjm_enterprise_ai_backend-*.whl' 2>/dev/null | sort)
  [[ "${#APP_WHEELS[@]}" -eq 1 ]] || fail "production install requires the bundled application wheel at python/app/openjm_enterprise_ai_backend-<version>-*.whl"
  python -m pip install --quiet --no-deps "${APP_WHEELS[0]}"
else
  python -m pip install --quiet --no-deps -e "."
fi
echo "backend dependencies installed from committed lock and application ($PROFILE profile)"

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

VERIFY_ARGS=(--profile "$PROFILE")
if [[ "$SKIP_FRONTEND" -eq 1 ]]; then
  VERIFY_ARGS+=(--skip-frontend)
fi
backend/.venv/bin/python scripts/verify-rel1a-install.py "${VERIFY_ARGS[@]}"
echo "REL1-A post-install verification passed"

cat <<'EOF'

== Install complete ==
Start the backend:
  cd backend && source .venv/bin/activate && \
    env -u PYTHONPATH -u PYTHONHOME .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
Serve the built frontend behind your reverse proxy/TLS boundary (see docs/INSTALLATION.md).
Verify: curl -s http://127.0.0.1:8000/api/ready
EOF
