#!/usr/bin/env bash
# OpenJM Enterprise AI — build a hash-pinned, binary-only production wheelhouse.
#
# REL1-B2: the committed backend lock is the dependency authority. This builds a
# wheelhouse that can populate an offline install without ever resolving or
# compiling a source distribution for the supported Linux release target.
#
# Usage:
#   scripts/build-python-wheelhouse.sh <output_dir> [requirements_lock] [--allow-source]
#
#   <output_dir>         directory to populate with wheels (created if absent)
#   [requirements_lock]  hash-pinned lock to consume (default backend/requirements.lock)
#   --allow-source       explicitly reviewed exception: permit source distributions
#                        (drops --only-binary=:all:). Do not use without review.
#
# The script never writes into the repository tree unless the caller points the
# output directory there; the wheelhouse itself is never committed to Git.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_LOCK="$ROOT/backend/requirements.lock"

OUT_DIR=""
LOCK_FILE="$DEFAULT_LOCK"
ALLOW_SOURCE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --allow-source) ALLOW_SOURCE=1; shift ;;
    -h|--help)
      grep '^# ' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' | head -n 20
      exit 0
      ;;
    --*) echo "unknown argument: $1" >&2; exit 2 ;;
    *)
      if [[ -z "$OUT_DIR" ]]; then
        OUT_DIR="$1"
      elif [[ "$LOCK_FILE" == "$DEFAULT_LOCK" ]]; then
        LOCK_FILE="$1"
      else
        echo "unexpected argument: $1" >&2; exit 2
      fi
      shift
      ;;
  esac
done

[[ -n "$OUT_DIR" ]] || { echo "ERROR: output directory argument is required" >&2; exit 2; }

python -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3,11) else 1)' || {
  echo "ERROR: Python 3.11 is required to build the OpenJM wheelhouse." >&2
  exit 2
}

[[ -f "$LOCK_FILE" ]] || { echo "ERROR: lock file not found: $LOCK_FILE" >&2; exit 1; }

mkdir -p "$OUT_DIR"
echo "== OpenJM wheelhouse =="
echo "lock:   $LOCK_FILE"
echo "output: $OUT_DIR"
echo "python: $(python --version 2>&1)"
echo "pip:    $(python -m pip --version)"

DOWNLOAD_ARGS=(
  download
  --require-hashes
  --dest "$OUT_DIR"
  -r "$LOCK_FILE"
)
if [[ "$ALLOW_SOURCE" -eq 0 ]]; then
  DOWNLOAD_ARGS+=(--only-binary=:all:)
else
  echo "WARNING: --allow-source set; source distributions are permitted (reviewed exception)." >&2
fi

# pip download fails closed with a non-zero exit if any pinned distribution is
# missing, hash-mismatched, or (in strict mode) has no binary wheel.
python -m pip "${DOWNLOAD_ARGS[@]}"

# --- Evidence: inventory + sha256 ------------------------------------------
mapfile -t ARTIFACTS < <(find "$OUT_DIR" -maxdepth 1 -type f ! -name 'SHA256SUMS' | sort)
[[ "${#ARTIFACTS[@]}" -gt 0 ]] || {
  echo "ERROR: no distributions were downloaded into $OUT_DIR" >&2
  exit 1
}

if [[ "$ALLOW_SOURCE" -eq 0 ]]; then
  for f in "${ARTIFACTS[@]}"; do
    [[ "$f" == *.whl ]] || {
      echo "ERROR: non-wheel artifact in a binary-only wheelhouse: $(basename "$f")" >&2
      exit 1
    }
  done
fi

echo "== inventory =="
for f in "${ARTIFACTS[@]}"; do
  printf '%12s  %s\n' "$(stat -c '%s' "$f")" "$(basename "$f")"
done

echo "== sha256 =="
( cd "$OUT_DIR" && sha256sum "${ARTIFACTS[@]##*/}" | tee SHA256SUMS )
echo "wrote $OUT_DIR/SHA256SUMS"
echo "wheelhouse populated: ${#ARTIFACTS[@]} distributions"
