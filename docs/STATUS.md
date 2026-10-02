# VS4 pilot checkpoint

| Field | Current value |
| --- | --- |
| Repository | sjevans1/OpenJM-Enterprise-AI |
| Plan revision | vs4-pilot-1 |
| State | blocked (B2C1 startup prerequisite) |
| Current batch / phase | VS4-B2C1 / startup and ownership claim |
| Active writer | Hermes — blocked before product edits |
| Branch | milestone/vs4-b2c1-run-history |
| PR | None; no product commit or useful draft PR yet |
| Starting main SHA | 208d329735348ac4cee7b7adfe9a62037bc5b7c2 |
| Last verified product SHA | 208d329735348ac4cee7b7adfe9a62037bc5b7c2 |
| Last verified product CI | 36983493324 — full main-push success |
| Setup CI | PR #27 merged; main run 36983493324 passed both required jobs |
| Setup local check | 23 CI-gate + 3 real-process launcher tests passed; shell syntax and documentation links valid |
| Current failure fingerprint / repair count | pilot_ci_gate: installed `gh` returns a Node TypeError as stdout with exit 0, causing invalid JSON / 0 |
| Total repair attempts in active batch | 0 |
| Local product-runtime verification | Not required for setup; B2C2 is not yet verified |
| Next batch | VS4-B2C1; docs/plan/VS4_B2C1.md; issue #23 |
| Next action | Install or provide the official authenticated GitHub CLI, then rerun `python scripts/pilot_ci_gate.py --main`; do not begin product edits before it passes |
| Hermes models | GPT-5.6 Sol → native free Laguna; preserve current configuration |
| Product acceptance model | Local Gemma, separate from coding provider |

## Checkpoint protocol

At every meaningful transition record the current phase, branch/PR, current code
SHA, last verified SHA/run, next exact action and any evidence still missing.
Update immediately after a failed repair, before waiting on CI/local services,
and before stopping; do not wait for credits to run out. Include checkpoint
changes with the next meaningful commit/push. If a process dies before pushing,
reconcile the local working tree and remote PR before doing anything again.

Record failed repairs below as: UTC time | phase | normalized failure signature
(test/error identity, no secrets) | attempt | hypothesis/change | result/run.
Three failed repairs of one problem, or five total in a batch, stop the batch.
Provider changes/restarts do not reset counters. A service outage has its own
single retry after recovery and is not a code-repair attempt.

## Attempt history

2026-10-02T08:24:56Z | VS4-B2C1 startup | `pilot_ci_gate.py --main`
reported `GitHub returned invalid JSON` | service/access prerequisite, not a code
repair | the installed `/home/sjeva/.local/bin/gh` is an npm package that prints
a Node `TypeError` on `gh api` while exiting zero | blocked before product edits;
direct read-only GitHub API evidence confirms main run 36983493324 passed.

## Batch ledger

| Batch | State | PR / evidence |
| --- | --- | --- |
| VS4-B2C1 | blocked | Branch claimed from verified main; official GitHub CLI prerequisite missing |
| VS4-B2C2 | planned | Not started |
| VS4-C1 | planned | Not started |
| VS4-C2 | planned | Not started |
| VS4-D | planned | Not started |

When a reviewer merges a PR, this branch-local checkpoint can still say
awaiting_review. Verify the live merge and full main CI before reconciling it in
the next branch. Never create a bookkeeping-only main push to alter that state.
