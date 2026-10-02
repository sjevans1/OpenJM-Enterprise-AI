#!/usr/bin/env bash
# Launch one bounded Hermes pilot session per host/repository (Linux / WSL).
set -euo pipefail

repo='sjevans1/OpenJM-Enterprise-AI'
root=$(git rev-parse --show-toplevel)
remote=$(git -C "$root" remote get-url origin)
case "$remote" in
  "https://github.com/$repo"|"https://github.com/$repo.git"|"git@github.com:$repo.git") ;;
  *) printf '%s\n' 'Refusing: origin is not the OpenJM Enterprise AI repository.' >&2; exit 2 ;;
esac
if [ "$#" -eq 0 ]; then
  printf '%s\n' 'Usage: bash scripts/pilot_session.sh hermes [arguments]' >&2
  exit 2
fi
for prerequisite in flock timeout; do
  command -v "$prerequisite" >/dev/null || {
    printf 'Missing prerequisite: %s\n' "$prerequisite" >&2
    exit 2
  }
done
command -v "$1" >/dev/null || { printf '%s\n' 'Requested executable is unavailable.' >&2; exit 2; }
umask 077
lock_dir="${XDG_STATE_HOME:-$HOME/.local/state}/openjm-vs4-pilot"
mkdir -p "$lock_dir"
# Stable per-user path also serializes separately cloned copies on this host.
exec 9>"$lock_dir/session.lock"
flock -n 9 || { printf '%s\n' 'Another OpenJM VS4 pilot session owns this host lock.' >&2; exit 3; }
cd "$root"
printf '%s\n' 'Pilot lock acquired. Checkpoint by 75 minutes; session limit is 90 minutes.'
# Keep FD 9 and this parent shell alive for the whole foreground session.
# timeout sends INT for a clean checkpoint opportunity, then KILL after 60s.
set +e
timeout --foreground --signal=INT --kill-after=60s 90m "$@"
result=$?
set -e
printf 'Pilot session ended (exit %s). Preserve/reconcile the checkpoint before resuming.\n' "$result"
exit "$result"
