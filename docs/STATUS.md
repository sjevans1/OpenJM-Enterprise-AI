# VS4 pilot checkpoint

| Field | Current value |
| --- | --- |
| Repository | sjevans1/OpenJM-Enterprise-AI |
| Plan revision | vs4-pilot-1 |
| State | correction pass in progress (review findings on cc9ebdc); regression tests + root-cause fixes staged; final CI + evidence pending |
| Current batch / phase | VS4-B2C1 / PR #28 correction pass: 5 reviewed defects + contract gap + missing acceptance evidence |
| Active writer | Hermes |
| Branch | milestone/vs4-b2c1-run-history |
| PR | #28 draft (open, mergeable); first TDD commit a30636c; history API cc9ebdc under review |
| Starting main SHA | 208d329735348ac4cee7b7adfe9a62037bc5b7c2 |
| Last verified product SHA | 208d329735348ac4cee7b7adfe9a62037bc5b7c2 |
| Last verified product CI | 36983493324 — full main-push success; `python scripts/pilot_ci_gate.py --main` accepted |
| Setup CI | PR #27 merged; main gate passed |
| Setup local check | 23 CI-gate + 3 real-process launcher tests passed; shell syntax and documentation links valid |
| Current failure fingerprint / repair count | none / 0 |
| Total repair attempts in active batch | 0 |
| Local product-runtime verification | Not required for B2C1 (non-executing batch); read sentinels + upgrade fixtures in pytest |
| Next batch | VS4-B2C1; docs/plan/VS4_B2C1.md; issue #23 |
| Next action | Finish correction pass: commit scoped fixes + regressions, run final-head fast PR CI, dispatch ci.yml suite=full, verify with pilot_ci_gate.py, update PR evidence, STOP for review |
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

2026-10-02T08:24:56Z | VS4-B2C1 startup | `pilot_ci_gate.py --main` reported
`GitHub returned invalid JSON` | service/access prerequisite, not a code repair |
the `/home/sjeva/.local/bin/gh` path was an npm package shadowing the official CLI |
replaced with official gh 2.102.0 and authenticated with the existing git-stored token |
gate now accepted (main run 36983493324, both jobs green).

## Batch ledger

| Batch | State | PR / evidence |
| --- | --- | --- |
| VS4-B2C1 | correction pass in progress after review of cc9ebdc: 5 defects + revoke-route scope issue; fixes + regressions staged locally; awaiting final CI + PR evidence update (STOP after) |
| VS4-B2C2 | planned | Not started; intentionally blocked until B2C1 acceptance |
| VS4-C1 | planned | Not started |
| VS4-C2 | planned | Not started |
| VS4-D | planned | Not started |

When a reviewer merges a PR, this branch-local checkpoint can still say
awaiting_review. Verify the live merge and full main CI before reconciling it in
the next branch. Never create a bookkeeping-only main push to alter that state.
