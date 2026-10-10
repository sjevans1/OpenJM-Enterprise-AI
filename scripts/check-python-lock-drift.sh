#!/usr/bin/env bash
# OpenJM Enterprise AI — REL1-B2 lock-drift gate.
#
# Compares freshly regenerated resolver output against the committed,
# authoritative locks. Any byte difference is a hard failure naming the file and
# the drift, because a green build must not silently ship locks that disagree
# with pyproject.toml or the pinned resolver toolchain.
#
# Usage:
#   scripts/check-python-lock-drift.sh [generated_dir] [committed_dir]
#
#   generated_dir  directory holding requirements.lock + requirements-dev.lock
#                  produced by scripts/compile-python-locks.sh
#                  (default: $RUNNER_TEMP/openjm-locks, then required)
#   committed_dir  directory holding the committed locks (default: backend)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

GENERATED_DIR="${1:-${RUNNER_TEMP:-}/openjm-locks}"
COMMITTED_DIR="${2:-$ROOT/backend}"

LOCKS=(requirements.lock requirements-dev.lock)
status=0

for name in "${LOCKS[@]}"; do
  gen="$GENERATED_DIR/$name"
  com="$COMMITTED_DIR/$name"

  if [[ ! -f "$gen" ]]; then
    echo "ERROR: LOCK DRIFT: generated $name is missing ($gen)" >&2
    status=1
    continue
  fi
  if [[ ! -f "$com" ]]; then
    echo "ERROR: LOCK DRIFT: committed $name is missing ($com)" >&2
    status=1
    continue
  fi

  if cmp -s "$gen" "$com"; then
    echo "OK: $name matches the committed lock"
  else
    echo "ERROR: LOCK DRIFT: $name differs from the committed lock" >&2
    echo "  generated: $gen" >&2
    echo "  committed: $com" >&2
    echo "  --- committed vs generated (first 60 lines of diff) ---" >&2
    diff -u "$com" "$gen" 2>/dev/null | head -n 60 >&2 || true
    echo "  ---------------------------------------------------------" >&2
    echo "  Refresh the committed locks (scripts/compile-python-locks.sh)" >&2
    echo "  or revert the pyproject.toml / resolver change that caused the drift." >&2
    status=1
  fi
done

if [[ "$status" -ne 0 ]]; then
  echo "LOCK DRIFT GATE FAILED" >&2
else
  echo "LOCK DRIFT GATE PASSED: committed locks are authoritative"
fi
exit "$status"
