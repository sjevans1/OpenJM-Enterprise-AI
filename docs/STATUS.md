# VS4 pilot checkpoint

|| Field | Current value |
| --- | --- | --- |
|| Repository | sjevans1/OpenJM-Enterprise-AI |
|| Plan revision | vs4-pilot-1 |
|| State | correction pass committed and pushed; fast PR CI green, full dispatch green, pilot_ci_gate accepted; STOP for review |
|| Current batch / phase | VS4-B2C1 / PR #28 correction pass: 5 reviewed defects + contract gap + acceptance evidence |
|| Active writer | Hermes |
|| Branch | milestone/vs4-b2c1-run-history |
|| PR | #28 (open, mergeable); final commit 9d5482c (evidence) on 982dc01 (corrections + regressions) |
|| Starting main SHA | 208d329735348ac4cee7b7adfe9a62037bc5b7c2 |
|| Last verified product SHA | 9d5482cd065cad5e1ae62218b1c2fb97584fc65b |
|| Last verified product CI | Fast PR run 37080887812 (success); full dispatch 37081272016 (success, Full backend step ran) |
|| Setup CI | PR #27 merged; main gate passed |
|| Setup local check | 23 CI-gate + 3 real-process launcher tests passed; shell syntax and documentation links valid |
|| Current failure fingerprint / repair count | none / 0 |
|| Total repair attempts in active batch | 0 |
|| Local product-runtime verification | Not required for B2C1 (non-executing batch); 412 full + 410 fast pytest + 28 regressions + ruff clean + frontend 6/6 |
|| Next batch | VS4-B2C1 awaiting review; VS4-B2C2 planned |
|| Next action | Awaiting review of PR #28 (STOP at review boundary). Do NOT begin B2C2 or merge |

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

2026-10-02 | VS4-B2C1 correction pass | 5 defects reproduced from review of cc9ebdc |
0 in-batch repairs | fixes applied to api/report_runs.py, app/db.py,
app/services/report_runs.py, docs/pilot/REVIEW.md; regressions added in
tests/test_report_run_regressions.py | committed 982dc01 + 9d5482c (evidence);
fast PR CI 37080887812 success, full dispatch 37081272016 success,
pilot_ci_gate accepted=true; existing 59 report-run tests updated to the
trusted-intent contract.

## Batch ledger

| Batch | State | PR / evidence |
| --- | --- | --- |
| VS4-B2C1 | committed (982dc01 + 9d5482c); fast+full CI green; pilot_ci_gate accepted; awaiting review (STOP at review boundary) | PR #28 |
| VS4-B2C2 | planned | Not started; intentionally blocked until B2C1 acceptance |
| VS4-C1 | planned | Not started |
| VS4-C2 | planned | Not started |
| VS4-D | planned | Not started |

When a reviewer merges a PR, this branch-local checkpoint can still say
awaiting_review. Verify the live merge and full main CI before reconciling it in
the next branch. Never create a bookkeeping-only main push to alter that state.
