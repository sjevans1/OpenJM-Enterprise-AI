#!/usr/bin/env bash
# Install a verified REL1-B bundle without public package registries.
set -euo pipefail

BUNDLE=""
TARGET=""
PROFILE="development"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bundle) BUNDLE="$2"; shift 2 ;;
    --target) TARGET="$2"; shift 2 ;;
    --profile) PROFILE="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

fail() { echo "ERROR: $*" >&2; exit 1; }
[[ "$PROFILE" == "development" || "$PROFILE" == "production" ]] || fail "invalid profile"
[[ -n "$BUNDLE" && -n "$TARGET" ]] || fail "--bundle and --target are required"
command -v python3.11 >/dev/null 2>&1 || fail "Python 3.11 is required"
command -v sha256sum >/dev/null 2>&1 || fail "sha256sum is required"

BUNDLE="$(cd "$BUNDLE" && pwd)"
mkdir -p "$TARGET"
TARGET="$(cd "$TARGET" && pwd)"
[[ -z "$(find "$TARGET" -mindepth 1 -maxdepth 1 -print -quit)" ]] || fail "target must be empty"

python3.11 "$BUNDLE/install/verify-bundle.py" verify --bundle "$BUNDLE"
(cd "$BUNDLE" && sha256sum -c SHA256SUMS)

SOURCE_TAR="$(find "$BUNDLE/source" -maxdepth 1 -name 'openjm-*.tar.gz' -print -quit)"
[[ -n "$SOURCE_TAR" ]] || fail "source archive missing"
tar -xzf "$SOURCE_TAR" -C "$TARGET"

python3.11 -m venv "$TARGET/backend/.venv"
"$TARGET/backend/.venv/bin/python" -m pip install   --no-index --find-links "$BUNDLE/wheels" --require-hashes   -r "$BUNDLE/wheels/requirements.lock"

mkdir -p "$TARGET/frontend"
tar -xzf "$BUNDLE/frontend/frontend-dist.tar.gz" -C "$TARGET/frontend"
[[ -f "$TARGET/frontend/dist/index.html" ]] || fail "prebuilt frontend is incomplete"

if [[ ! -f "$TARGET/.env" ]]; then
  if [[ "$PROFILE" == "production" ]]; then
    cp "$TARGET/.env.production.example" "$TARGET/.env"
    echo "production .env template created; provision secrets before startup"
  else
    cp "$TARGET/.env.example" "$TARGET/.env"
  fi
fi

if [[ "$PROFILE" == "development" ]]; then
  (
    cd "$TARGET/backend"
    "$TARGET/backend/.venv/bin/python" -m app.core.preflight
    "$TARGET/backend/.venv/bin/python" "$TARGET/scripts/openjm_ops.py" upgrade
  )
else
  echo "production payload staged; provision secrets before preflight/migration"
fi

"$TARGET/backend/.venv/bin/python" "$TARGET/scripts/verify-rel1b-install.py"   --root "$TARGET" --profile "$PROFILE"

echo "REL1-B offline install staged successfully at $TARGET"
