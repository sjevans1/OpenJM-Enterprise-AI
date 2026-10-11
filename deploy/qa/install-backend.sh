#!/usr/bin/env bash
# OpenJM Enterprise AI - QA1 backend production install.
#
# Installs the accepted REL1 production package into a dedicated virtualenv:
#   * the committed production lock (backend/requirements.lock), installed
#     offline from the release wheelhouse with --require-hashes;
#   * the packaged application wheel (openjm_enterprise_ai_backend-<ver>.whl),
#     installed --no-deps (never editable, never from an index).
#
# It does NOT use an editable install, does NOT set PYTHONPATH, and does NOT
# install the [dev] extras. The resulting `app` package imports from
# site-packages, exactly as the REL1 production install does.
#
# Usage:
#   OPENJM_QA_ENV_ROOT="$HOME/openjm-qa1-env" \
#   OPENJM_QA_BUNDLE="$HOME/openjm-replay/b4-bundle/openjm-rel1-0.2.0" \
#   deploy/qa/install-backend.sh

set -euo pipefail

ENV_ROOT="${OPENJM_QA_ENV_ROOT:-$HOME/openjm-qa1-env}"
BUNDLE="${OPENJM_QA_BUNDLE:-}"
PYTHON_BIN="${OPENJM_QA_PYTHON:-python3.11}"
VENV="$ENV_ROOT/venv"

if [ -z "$BUNDLE" ] || [ ! -d "$BUNDLE" ]; then
  echo "OPENJM_QA_BUNDLE must point at an extracted REL1 release bundle" >&2
  exit 2
fi
for required in python/requirements.lock python/wheelhouse python/app; do
  [ -e "$BUNDLE/$required" ] || { echo "bundle is missing $required" >&2; exit 2; }
done

echo "== release integrity =="
expected="$(awk '{print $1}' "$BUNDLE/RELEASE-MANIFEST.sha256")"
"$PYTHON_BIN" - "$BUNDLE" "$expected" <<'PY'
import sys, hashlib, pathlib
bundle, expected = pathlib.Path(sys.argv[1]), sys.argv[2]
digest = hashlib.sha256((bundle / "RELEASE-MANIFEST.json").read_bytes()).hexdigest()
if digest != expected:
    raise SystemExit(f"manifest sha256 mismatch: {digest} != {expected}")
print(f"manifest sha256 OK: {digest}")
PY

echo "== virtualenv =="
rm -rf "$VENV"
"$PYTHON_BIN" -m venv "$VENV"
"$VENV/bin/python" -V

echo "== production lock (offline, hash-pinned) =="
"$VENV/bin/pip" install --no-index --find-links "$BUNDLE/python/wheelhouse" \
  --require-hashes -r "$BUNDLE/python/requirements.lock" >/dev/null
echo "lock installed"

echo "== packaged application wheel (no deps) =="
shopt -s nullglob
wheels=("$BUNDLE/python/app/"*.whl)
shopt -u nullglob
if [ "${#wheels[@]}" -ne 1 ]; then
  echo "expected exactly one application wheel, found ${#wheels[@]}" >&2
  exit 2
fi
"$VENV/bin/pip" install --no-deps "${wheels[0]}" >/dev/null

echo "== verification =="
"$VENV/bin/python" - <<'PY'
import importlib.metadata as md
import app
assert "/site-packages/app/" in app.__file__, app.__file__
dist = md.distribution("openjm-enterprise-ai-backend")
print("app:", app.__file__)
print("version:", dist.version)
assert not any(md.distribution(d).metadata.get("Name", "").startswith(("pytest", "pgserver"))
               for d in md.distributions()), "dev extras are present"
from app.version import PRODUCT_VERSION, RELEASE_TRAIN
print("product:", PRODUCT_VERSION, RELEASE_TRAIN)
PY
echo "backend production install OK: $VENV"
