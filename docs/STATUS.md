# VS4 pilot checkpoint

| Field | Current value |
| --- | --- |
| Repository | sjevans1/OpenJM-Enterprise-AI |
| Plan revision | vs4-pilot-1 |
| State | VS4-B2C2 correction pass after reviewer finding; PR #29 open, unmerged |
| Current batch / phase | VS4-B2C2 / release-gate correction |
| Active writer | ChatGPT correction pass on existing Hermes branch |
| Branch | milestone/vs4-b2c2-manual-execution |
| PR | #29 |
| Starting main SHA | de391e92b510fb38e445e382fab33ee1517a2c12 |
| Last reviewed SHA | 8948825fd4f4341e5224b9f31048e469781bc31b |
| Last verified PR CI | Fast PR run 37408679705 green on 8948825; exact correction-head CI pending |
| Real-runtime acceptance | 30/30 accepted on 8948825 with real Gemma + isolated temp DB/vector/uploads/key; rerun required after wall-budget correction |
| Local regression | 504 passed, 19 subtests passed on 8948825 |
| Review finding | 600-second wall budget was checked only at run entry |
| Correction | Overall planning/synthesis deadlines, external-boundary checks, pre-persist wall check, and deadline-aware success CAS |
| Next action | Final correction-head tests, fast CI, full hosted workflow_dispatch, then real-runtime acceptance rerun and review |

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

## Attempt and resume history

2026-10-02T08:24:56Z | VS4-B2C1 startup | `pilot_ci_gate.py --main` reported
`GitHub returned invalid JSON` | service/access prerequisite, not a code repair |
the `/home/sjeva/.local/bin/gh` path was an npm package shadowing the official CLI |
replaced with official gh 2.102.0 and authenticated with the existing git-stored token |
gate accepted against main run 36983493324 with both jobs green.

2026-10-02 | First correction pass after review of cc9ebdc | 5 reproduced defects |
0 code-repair attempts | committed 982dc01 plus evidence/checkpoint commits 9d5482c
and 372d45e | fast PR CI 37082317533 covers 372d45e; older full run 37081272016
covers 982dc01 only and is not final-head evidence.

Mandatory B2C1 resume rehearsal | A real Hermes restart resumed
@session:default/20261002_141343_c17344 after review of cc9ebdc. Before resume:
same branch, existing PR #28, phase `review corrections required`, counters 0 and
next action the correction handoff. After resume: same branch and PR, commits
982dc01, 9d5482c and 372d45e, counters still 0, unrelated files preserved and no
duplicate branch/PR. Limitation: the committed pre-resume checkpoint was stale,
so continuity is proven by the resumed session's live reconciliation and GitHub
history rather than a correct contemporaneous checkpoint.

2026-10-03 | Second review correction pass from 372d45e | remaining authorization,
startup acceptance and resume-evidence findings reproduced and corrected | 0 failed
code repairs | local result: 83 focused tests and Ruff passed | final-head fast/full
CI and pilot gate still pending after the intended commit.

2026-10-04 | B2C1 live reconciliation | PR #28 merged and accepted; main advanced
to `de391e92b510fb38e445e382fab33ee1517a2c12`; main CI run 37176830805 green;
`python scripts/pilot_ci_gate.py --main` accepted | B2C2 claim recorded at
https://github.com/sjevans1/OpenJM-Enterprise-AI/issues/23#issuecomment-5982046257 |
counters began and remain at zero.

2026-10-04 | VS4-B2C2 Phase 1 | strict explicit submission route, trusted exact
definition resolution, reservation/replay authorization and focused HTTP regressions
implemented | 102 focused tests and Ruff critical checks passed | no execution,
public revoke, push, PR or Phase 2 work performed.

## Batch ledger

| Batch | State | PR / evidence |
| --- | --- | --- |
| VS4-B2C1 | accepted and merged; final-head fast/full and post-merge main gate passed | PR #28; main run 37176830805 |
| VS4-B2C2 | Full B2C2 implemented in PR #29; wall-budget correction under final validation, unmerged | Prior accepted head 8948825; correction head requires fast/full CI + real-runtime rerun |
| VS4-C1 | planned | Not started |
| VS4-C2 | planned | Not started |
| VS4-D | planned | Not started |

Do not create a bookkeeping-only commit after final CI. Put final SHA, run links,
gate output and ready-state evidence in PR metadata, then stop for review.