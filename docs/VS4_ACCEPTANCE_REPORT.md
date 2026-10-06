# VS4 Acceptance Report

Status: **IN PROGRESS — VS4-D integrated acceptance pending**  
Candidate base for VS4-D: `52e7fd8c4c514538874a6b167b9fb4bd64321806`  
Repository: `sjevans1/OpenJM-Enterprise-AI`

This report reconciles previously accepted VS4 evidence and records the final integrated VS4-D gates. It does **not** claim the entire OpenJM Enterprise AI product is complete. VS5–VS8 remain future work.

## 1. Accepted VS4 batches before D

| Batch | Accepted evidence |
| --- | --- |
| VS4-A | PR #14, merge `361c6392644bb15267eb75d0f0508dceb3ba84c4`: immutable permission-scoped saved report snapshots. |
| VS4-B1 | PR #18, merge `31807816bc4e420fd2078f677f31be932a2f5b13`: source-checked read-only rerun preview into a new Chat composer; no automatic execution. |
| VS4-B2A | PR #20, merge `3f9d1ceb016943399131fc73c4f913e55f04dce3`: immutable versioned source-bound report definition records. |
| VS4-B2B | PR #22, merge `9343f5b1e70552fba034f05e745d8f44b8bea5ed`; full main run `36975813425`: source scope enforced before Knowledge retrieval and structured planning/query execution. |
| VS4-B2C1 | PR #28, merge `de391e92b510fb38e445e382fab33ee1517a2c12`; main run `37176830805`: immutable ReportRun lifecycle/history and idempotent reservation foundation. |
| VS4-B2C2 | PR #29; accepted head `3590f2cc3bde61bfc11bea3842b57cfc5556dcad`; full CI `37414383020`; real-runtime 30/30; merge `dfbddd2308d2730409ae525bcef955cf9bbf3238`; main run `37415877675`: explicit scoped manual execution, bounded results, budgets, reauthorization, immutable success/failure. |
| VS4-C1 | PR #30; accepted head `f41271e420c8d88df8381fe8101c2b925741075a`; fast `37417795106`; full `37418485617`; ready-state CI `37420361916`; browser/local-model 35/35; merge `cb13dc6da596270a5c977537b0e87aac056366ca`; main `37420633672`: explicit run controls, idempotency/retry UX, history/failure/revocation/stale-response handling. |
| VS4-C2 | PR #31; accepted head `3b60fc2231dd2c7883f3057576ac771d92f3afc3`; fast `37424841750`; full `37424846415`; browser/export 15/15; adversarial export review cleared after Unicode formula-bypass fix; merge `52e7fd8c4c514538874a6b167b9fb4bd64321806`; post-merge main `37425676477`: authorized bounded CSV/HTML export from persisted results only. |

## 2. Security and product invariants already proven

- Saved snapshots remain historical and immutable; viewing does not execute.
- Fresh report execution is explicit, source-pinned, server-authorized and disabled by default outside isolated acceptance.
- Old Evidence SQL is never treated as executable authority.
- Knowledge and structured source scope is applied before retrieval/planning/execution, not post-hoc.
- Structured execution remains read-only and AST/policy governed.
- Dependent Hybrid rederives current grounded policy parameters and fails closed on conflict/missing/hostile policy.
- Idempotency prevents duplicate execution; uncertain retries reuse the same key while terminal retries require a new intentional run.
- Current owner/source/document/table authorization is rechecked for run/history/export reads.
- Revocation clears cached UI content and blocks later access.
- CSV/HTML exports operate only on persisted bounded results and invoke zero fresh retrieval/SQL/model work.
- Exported CSV neutralizes hostile formula-leading text while retaining actual typed numeric values.
- Exported HTML escapes untrusted content, is self-contained and blocks remote resources.
- Workspace is not a dependency and was not modified by VS4.

## 3. VS4-D integrated acceptance matrix — pending

The final candidate must prove on one isolated synthetic instance:

- [ ] historical saved snapshot remains unchanged after new runs and restart;
- [ ] pinned Knowledge run succeeds with citations;
- [ ] pinned Data run succeeds against read-only SQLite with structured provenance;
- [ ] pinned independent Hybrid succeeds only when both evidence sides complete;
- [ ] pinned dependent Hybrid uses current policy parameter/year/currency/operator and equivalent SQL AST;
- [ ] partial Hybrid, hostile/ambiguous policy and wrong-owner requests fail closed;
- [ ] duplicate and concurrent same-key submissions create one effective run;
- [ ] terminal/interrupted runs remain immutable and are not silently re-executed;
- [ ] restart persistence preserves snapshots, definitions, runs, evidence and history;
- [ ] CSV/HTML exports work from eligible persisted snapshot/run results only;
- [ ] deleted/unindexed document, disabled source, changed table grant and permission revocation block current access;
- [ ] source database remains unchanged/read-only throughout acceptance;
- [ ] citations, as-of labels, failure/revocation states and export controls are inspected in the real UI.

## 4. Upgrade and recovery — pending

- [ ] Synthetic pre-VS4/old-schema database upgraded on startup without record loss.
- [ ] Second startup/upgrade is idempotent.
- [ ] Conversations/messages/execution traces/saved reports survive.
- [ ] Expired/crashed running ReportRun is recovered to the defined terminal state without re-execution.
- [ ] Cleanup removes only test-owned resources.
- [ ] Document/source disable and recovery procedure is recorded.
- [ ] No downgrade-by-table-drop and no tests against the real development database.

## 5. Hosted CI — pending final VS4-D head

Required:
- [ ] exact-head fast PR CI — backend + frontend green;
- [ ] exact-head `workflow_dispatch suite=full` — full backend + frontend green;
- [ ] after human-authorized merge only: post-merge full main-push CI green.

## 6. Pilot observations

Observed evidence from earlier accepted batches includes:
- B2C2 final deterministic/local acceptance: 504 backend tests + 19 subtests reported locally and 30/30 real-runtime checks on the accepted head.
- C1 controlled browser/local-model acceptance: 35/35 checks.
- C2 focused export verification: 13/13 new backend tests; frontend 23/23; browser/export acceptance 15/15.
- C2 local full suite reported 517 passes plus one environment-only timeout test that also reproduced on pristine main and did not fail hosted CI.

These are acceptance/test observations, not production usage or capacity measurements. Production usage, per-customer cost, concurrency throughput and commercial SLA measurements are **unknown** and must not be inferred.

## 7. Known limitations retained after VS4

1. **Development single-user identity:** authorization still uses the development user context; trusted enterprise identity/tenant isolation is VS5.
2. **Issue #6:** cross-process document lifecycle concurrency remains open and must be addressed before production multi-user claims.
3. **Issue #7:** versioned application-metadata migrations/PostgreSQL compatibility remain open; VS4-D only proves the current backward-compatible synthetic upgrade path.
4. **Downloaded exports cannot be recalled** after later permission/source revocation; reauthorization protects generation/download time, not already-delivered files.
5. VS6 agent/action execution, VS7 connectors/automations and VS8 packaging/operations are outside VS4.
6. Workspace remains a separate product; no shared database/runtime/secrets/deployment is introduced here.

## 8. Interruption/resume outcome

The VS4 lifecycle pilot included a mandatory real Hermes restart/resume during B2C1. The resumed session reconciled the existing branch/PR and continued without duplicate branch/PR creation or unrelated-file loss. A stale committed pre-resume checkpoint was identified as a process limitation; subsequent batches used explicit GitHub/head reconciliation and exact-head CI evidence.

## 9. Final verdict

**Not yet accepted.** Complete Sections 3–5 on the final VS4-D candidate head, record exact runtime/model/fixture/cleanup evidence, then request human review. No merge or VS5 work before that review.
