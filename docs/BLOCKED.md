# VS4 pilot blockers

## Active correction gate

VS4-B2C2 PR #29 is not merge-ready until the wall-clock budget correction passes
all final-head gates. On reviewed head `8948825fd4f4341e5224b9f31048e469781bc31b`,
`ReportRunBudget.check_wall()` was only invoked at run entry; there was no
overall remaining-deadline timeout around planning/synthesis and the success
persistence CAS did not compare `deadline_at`.

Required resolution:
1. overall planning and synthesis bounded by remaining wall time;
2. model/Knowledge/SQL boundary checks;
3. final pre-persist check and database CAS refusing late success;
4. focused/local regression;
5. exact-head fast PR CI and full hosted `workflow_dispatch`;
6. isolated real Gemma/DB-GPT/Chroma/SQLite acceptance rerun.

The prior 30/30 runtime acceptance and 504-pass local suite remain historical
evidence only. Report-run execution remains disabled by default.

## Previously active blocker (resolved)

2026-10-02T08:24:56Z / VS4-B2C1 startup / `pilot_ci_gate.py --main` returned
`GitHub returned invalid JSON`. Read-only diagnosis showed `/home/sjeva/.local/bin/gh`
was an npm package whose `gh api` command prints a Node TypeError to stdout while
exiting zero, rather than the official GitHub CLI required by the gate.

Resolved by replacing it with the official GitHub CLI 2.102.0 (checksum verified)
authenticated with the existing git-stored token, reusing the user's credential
without exposing it. The maintainer authorized installation of the official CLI.
The main gate now accepts: `python scripts/pilot_ci_gate.py --main` returned
accepted=true against main `208d329735348ac4cee7b7adfe9a62037bc5b7c2`, run 36983493324.

## Verified constraints

- Main's branch API reported `protected: false` on 2026-10-02. CI evidence and
  the no-merge rule are not a claim that server-side merge restrictions exist.
  Repository administration is outside Hermes's authority; do not change it.
- Real Gemma runtime acceptance remains a later B2C2 release-gate requirement;
  it is not part of Phase 1 and has not been simulated or claimed.
- PR #5 and PR #13 remain historical open work. Preserve them. If their files
  overlap the current batch, report that conflict rather than replacing work.

## Blocker entry template

UTC time / batch / branch / PR / code SHA:
Category: plan-change | repeated-failure | missing-access | service-unavailable |
local-verification | concurrent-writer | budget | destructive-action.
Observed failure and minimal redacted reproduction:
Attempts and links to relevant failing logs (not full logs):
Current hypothesis / proposed fix:
Exact decision or access needed:
Work preserved / exact next action:
Resolved by / date / evidence:

For a plan conflict, open one issue referencing #24 and the affected batch. Use
the `plan-change` label if it already exists; otherwise use a `[plan-change]`
title and record that the label is unavailable. Do not change repo settings or
create duplicate issues on resume. Do not silently rewrite acceptance criteria.
