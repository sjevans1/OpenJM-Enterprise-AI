# VS4 pilot checkpoint

|| Field | Current value |
| --- | --- | --- |
|| Repository | sjevans1/OpenJM-Enterprise-AI |
|| Plan revision | vs4-pilot-1 |
|| State | Second review corrections implemented and verified; awaiting_ci after final-head fast + full hosted CI |
|| Current batch / phase | VS4-B2C1 / PR #28 second correction pass: run-specific authorization, real repeated startup, resume evidence |
|| Active writer | Hermes |
|| Branch | milestone/vs4-b2c1-run-history |
|| PR | #28, open draft and reused; no duplicate PR |
|| Starting main SHA | 208d329735348ac4cee7b7adfe9a62037bc5b7c2 |
Last reviewed SHA | 372d45eb39cb893e9e7ac11557d652ae51bb2c0a |
| Current PR head | c04c0bf305848aa86c9e8a05335e7277c99aa2d7 (committed + pushed; matches live PR #28) |
| Last verified product CI | Fast PR run 37144377763 succeeded on c04c0bf; full dispatch 37145053442 (suite=full) Backend + Frontend succeeded |
|| Setup CI | PR #27 merged; main gate passed |
|| Current failure fingerprint / repair count | none / 0 |
|| Total code-repair attempts in active batch | 0 |
|| Service retries | 1 transient GitHub API/network retry, recovered without redispatch |
|| Local verification | 83 focused report/definition tests passed; 39 regression cases collected; focused Ruff critical checks passed |
|| Next batch | VS4-B2C2 planned but intentionally paused |
|| Next action | Commit and push intended code/tests/docs, wait for fast CI, dispatch new full CI, run exact-head pilot gate, publish PR evidence, mark ready and stop |

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

## Batch ledger

| Batch | State | PR / evidence |
| --- | --- | --- |
| VS4-B2C1 | awaiting_review CI dispatched; final-head fast + full passes pending human review | PR #28 |
| VS4-B2C2 | planned | Not started; intentionally blocked until B2C1 acceptance |
| VS4-C1 | planned | Not started |
| VS4-C2 | planned | Not started |
| VS4-D | planned | Not started |

Do not create a bookkeeping-only commit after final CI. Put final SHA, run links,
gate output and ready-state evidence in PR metadata, then stop for review.