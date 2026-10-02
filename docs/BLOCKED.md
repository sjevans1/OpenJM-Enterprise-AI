# VS4 pilot blockers

No unresolved product blocker recorded at setup. Human review of the setup PR is
a normal workflow boundary. Hermes has not been launched on the workstation.

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
