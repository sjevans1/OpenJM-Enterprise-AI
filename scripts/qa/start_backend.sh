#!/usr/bin/env bash
# OpenJM Enterprise AI - QA1 backend launcher (REL1 production install).
#
# Sources the QA1 production environment (deploy/qa/qa_env.sh), runs the
# fail-closed preflight, and serves uvicorn from the packaged-wheel virtualenv.
#
# The backend binds 0.0.0.0 so the frontend Caddy container can reach it through
# host.docker.internal. Host validation is enforced by OPENJM_TRUSTED_HOSTS, not
# by the bind address.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=/dev/null
source "$HERE/../../deploy/qa/qa_env.sh"

VENV="$OPENJM_QA_ENV_ROOT/venv"
PORT="${OPENJM_QA_BACKEND_PORT:-18010}"

mkdir -p "$OPENJM_UPLOAD_DIR" "$OPENJM_VECTOR_PATH" "$OPENJM_BACKUP_DIR" \
         "$OPENJM_ARTIFACT_DIR" "$(dirname "$OPENJM_CREDENTIAL_KEY_FILE")"

echo "== preflight (fail-closed) =="
"$VENV/bin/python" -m app.core.preflight

echo "== serving uvicorn on 0.0.0.0:$PORT =="
exec "$VENV/bin/python" -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
