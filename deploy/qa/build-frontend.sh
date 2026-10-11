#!/usr/bin/env bash
# OpenJM Enterprise AI - QA1 frontend production build.
#
# Builds the production SPA (tsc --noEmit && vite build) from the accepted
# source tree and stages frontend/dist for the Caddy host container.
set -euo pipefail

ENV_ROOT="${OPENJM_QA_ENV_ROOT:-$HOME/openjm-qa1-env}"
REPO_ROOT="${OPENJM_QA_REPO:-$(cd "$(dirname "$0")/../.." && pwd)}"
NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]')"
[ "$NODE_MAJOR" = "22" ] || echo "warning: expected Node 22, found $(node -v)" >&2

echo "== npm ci =="
cd "$REPO_ROOT/frontend"
npm ci --no-audit --no-fund

echo "== vite production build =="
npm run build

echo "== stage dist =="
rm -rf "$ENV_ROOT/frontend-dist"
mkdir -p "$ENV_ROOT/frontend-dist"
cp -r "$REPO_ROOT/frontend/dist/." "$ENV_ROOT/frontend-dist/"
ls "$ENV_ROOT/frontend-dist" | head
echo "frontend build OK: $ENV_ROOT/frontend-dist"
