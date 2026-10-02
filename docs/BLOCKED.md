# VS4 pilot blockers

No unresolved product blocker recorded at setup. Human review of the setup PR is
a normal workflow boundary. Hermes has not been launched on the workstation.

## Active blocker

2026-10-02T08:24:56Z / VS4-B2C1 / `milestone/vs4-b2c1-run-history` /
no PR / `208d329735348ac4cee7b7adfe9a62037bc5b7c2`

Category: missing-access.

Observed failure: `python scripts/pilot_ci_gate.py --main` returned `GitHub
returned invalid JSON`. Read-only diagnosis found `/home/sjeva/.local/bin/gh`
is an npm package whose `gh api` command prints a Node `TypeError` to stdout
while exiting zero. It is not the official GitHub CLI required by the gate.

Attempts: one gate invocation and one read-only direct command diagnosis. No
product repair was attempted. Direct GitHub REST inspection independently shows
main run 36983493324 and both required jobs succeeded, but the normative gate
itself has not passed.

Exact access needed: provide or authorize installation of the official
authenticated GitHub CLI, with `gh api` support, without changing provider or
product configuration. Then rerun the main gate.

Work preserved: branch created from verified main; protected `.env`, `data/`,
`scripts/serve_frontend.py`, and `test-documents/` untouched. Exact next action:
rerun `python scripts/pilot_ci_gate.py --main`; only after it succeeds, begin
B2C1 tests and implementation.

## Verified constraints

- Main's branch API reported `protected: false` on 2026-10-02. CI evidence and
  the no-merge rule are not a claim that server-side merge restrictions exist.
  Repository administration is outside Hermes's authority; do not change it.
- This environment cannot establish the user's WSL Gemma runtime acceptance.
  That is a planned local gate in B2C2/C1/D, not a simulated pass.
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
