#!/usr/bin/env bash
# OpenJM Enterprise AI - QA1 environment variables (sourced, not executed).
#
# Renders the accepted production profile for the QA1 acceptance environment.
# All secrets come from the operator's secret store under $OPENJM_QA_ENV_ROOT/
# secrets and are never committed. Source this from a shell or another script.
#
#   source deploy/qa/qa_env.sh

set -euo pipefail

export OPENJM_QA_ENV_ROOT="${OPENJM_QA_ENV_ROOT:-$HOME/openjm-qa1-env}"
OPENJM_QA_SECRETS="$OPENJM_QA_ENV_ROOT/secrets"
export OPENJM_QA_SECRETS

# --- secrets (operator store) ----------------------------------------------
export OPENJM_DB_PASSWORD="$(cat "$OPENJM_QA_SECRETS/db_password.txt")"
export OPENJM_OIDC_CLIENT_SECRET="$(cat "$OPENJM_QA_SECRETS/openjm_client_secret.txt")"
export OPENJM_CREDENTIAL_ENCRYPTION_KEY="$(cat "$OPENJM_QA_SECRETS/credential_key.txt")"
export OPENJM_MODEL_API_KEY="$(cat "$OPENJM_QA_SECRETS/model_api_key.txt")"
export OPENJM_OPS_TOKEN="$(cat "$OPENJM_QA_SECRETS/ops_token.txt")"

# --- profile and release identity ------------------------------------------
export OPENJM_DEPLOYMENT_PROFILE=production
export OPENJM_RELEASE_ID=rel1-qa1
export OPENJM_PRODUCT_NAME="OpenJM Enterprise AI"
export OPENJM_ORGANIZATION_NAME="OpenJM QA Acceptance"

# --- identity: real OIDC, no dev fallback ----------------------------------
export OPENJM_AUTH_MODE=oidc
export OPENJM_AUTH_ALLOW_DEV_MODE=false
export OPENJM_OIDC_ISSUER="http://127.0.0.1:18090/realms/openjm"
export OPENJM_OIDC_DISCOVERY_URL="http://127.0.0.1:18090/realms/openjm/.well-known/openid-configuration"
export OPENJM_OIDC_CLIENT_ID=openjm
export OPENJM_OIDC_AUDIENCE=openjm
export OPENJM_OIDC_REDIRECT_URI="http://127.0.0.1:15173/auth/callback"
export OPENJM_OIDC_TENANT_CLAIM=tenant
export OPENJM_TENANT_RESOLUTION=claim
export OPENJM_SESSION_TTL_SECONDS=28800

# --- PostgreSQL metadata database ------------------------------------------
export OPENJM_DATABASE_URL="postgresql+asyncpg://openjm:${OPENJM_DB_PASSWORD}@127.0.0.1:15432/openjm"

# --- persistent storage ----------------------------------------------------
export OPENJM_UPLOAD_DIR="$OPENJM_QA_ENV_ROOT/runtime/uploads"
export OPENJM_VECTOR_PATH="$OPENJM_QA_ENV_ROOT/runtime/vector"
export OPENJM_BACKUP_DIR="$OPENJM_QA_ENV_ROOT/runtime/backups"
export OPENJM_ARTIFACT_DIR="$OPENJM_QA_ENV_ROOT/runtime/artifacts"
export OPENJM_CREDENTIAL_KEY_FILE="$OPENJM_QA_ENV_ROOT/runtime/credentials.key"

# --- model serving (approved local OpenAI-compatible endpoint) -------------
export OPENJM_MODEL_PROVIDER_MODE=local
export OPENJM_MODEL_BASE_URL="http://127.0.0.1:18080/v1"
export OPENJM_MODEL_NAME="gemma-4-12b-local"
export OPENJM_MODEL_TIMEOUT_SECONDS=300
export OPENJM_MODEL_PROVIDER_FALLBACK=none
export OPENJM_MODEL_ALLOW_INSECURE_HTTP=true

# --- HTTP boundary / security ----------------------------------------------
export OPENJM_CORS_ORIGINS="http://127.0.0.1:15173,http://localhost:15173"
export OPENJM_TRUSTED_HOSTS="127.0.0.1,localhost"
export OPENJM_TRUST_PROXY_HEADERS=true
export OPENJM_SECURITY_HEADERS_ENABLED=true
export OPENJM_RATE_LIMIT_ENABLED=true
export OPENJM_RATE_LIMIT_EXPENSIVE_PER_MINUTE=60
export OPENJM_METRICS_ENABLED=true

# --- knowledge / structured / scheduler ------------------------------------
export OPENJM_KNOWLEDGE_ENABLED=true
export OPENJM_EMBEDDING_MODEL="sentence-transformers/all-MiniLM-L6-v2"
export OPENJM_VECTOR_COLLECTION=openjm_default
export OPENJM_STRUCTURED_MAX_ROWS=200
export OPENJM_STRUCTURED_TIMEOUT_SECONDS=20
export OPENJM_SCHEDULER_ENABLED=true
export OPENJM_SCHEDULER_TICK_SECONDS=30
export OPENJM_RETENTION_ENABLED=false
