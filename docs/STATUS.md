# VS4 pilot checkpoint

| Field | Current value |
| --- | --- |
| Repository | sjevans1/OpenJM-Enterprise-AI |
| Plan revision | vs4-pilot-1 |
| State | awaiting_ci (pilot setup; product implementation not started) |
| Current batch / phase | SETUP / publish and verify lifecycle package |
| Active writer | ChatGPT — setup only; Hermes not launched |
| Branch | chore/vs4-hermes-lifecycle-pilot |
| PR | Resolve by exact branch on GitHub; record URL in PR evidence |
| Starting main SHA | 9343f5b1e70552fba034f05e745d8f44b8bea5ed |
| Last verified product SHA | 9343f5b1e70552fba034f05e745d8f44b8bea5ed |
| Last verified product CI | 36975813425 — full main-push success |
| Setup CI | Pending publication; final evidence belongs to the setup PR |
| Setup local check | 23 CI-gate + 3 real-process launcher tests passed; shell syntax and documentation links valid |
| Current failure fingerprint / repair count | none / 0 |
| Total repair attempts in active batch | 0 |
| Local product-runtime verification | Not required for setup; B2C2 is not yet verified |
| Next batch | VS4-B2C1; docs/plan/VS4_B2C1.md; issue #23 |
| Next action | After setup merge and green full main CI, claim B2C1 on fresh main |
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

None. No product implementation has been attempted under this pilot.

## Batch ledger

| Batch | State | PR / evidence |
| --- | --- | --- |
| VS4-B2C1 | planned | Not started |
| VS4-B2C2 | planned | Not started |
| VS4-C1 | planned | Not started |
| VS4-C2 | planned | Not started |
| VS4-D | planned | Not started |

When a reviewer merges a PR, this branch-local checkpoint can still say
awaiting_review. Verify the live merge and full main CI before reconciling it in
the next branch. Never create a bookkeeping-only main push to alter that state.
