# VS4 Acceptance Report

Status: **AWAITING REVIEW — VS4-D integrated acceptance complete**  
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

## 3. VS4-D integrated acceptance matrix — complete

Final integrated acceptance was executed on frozen D head `8e2a4db5facecfb03602d3c5128ea802bcf33564`. Application/runtime code is byte-identical to accepted C2 main `52e7fd8c4c514538874a6b167b9fb4bd64321806`; D changes documentation only.

Observed environment: Python 3.11.15, Node 22.23.2, real local `gemma-4-12b-local`, local MiniLM embeddings, real Windows Chrome 154 driven over CDP, isolated temp metadata/vector/uploads/key stores, and a synthetic read-only SQLite finance source.

Integrated result: **39/39 assertions passed** across runtime, restart and browser checks.

- [x] historical saved snapshots remained unchanged after new runs and restart; opening/viewing caused zero run execution;
- [x] pinned Knowledge run succeeded with pinned document evidence/citations only;
- [x] pinned Data run succeeded with structured provenance against read-only SQLite;
- [x] pinned independent Hybrid succeeded with both Knowledge and Structured evidence;
- [x] pinned dependent Hybrid rederived the current threshold/operator/fiscal-year/currency and executed an AST-equivalent predicate;
- [x] policy revision from threshold 300 to 500 changed only the new run; the earlier run and snapshot remained byte-identical;
- [x] partial Hybrid and hostile/conflicting policy failed closed;
- [x] wrong-owner snapshot/definition/run/history/export reads failed closed with no tool execution;
- [x] same-key retry and near-concurrent same-key submissions resolved to one effective run;
- [x] terminal/interrupted runs remained immutable and were not silently re-executed;
- [x] restart preserved snapshots, definitions, run history, result/evidence/citations and failure state; full-state digest `b35f90860f47fb6e` was identical across restart;
- [x] CSV/HTML export from eligible persisted results caused zero fresh model/retrieval/SQL work;
- [x] deleted/unindexed document, disabled source, revoked table grant and permission/source revocation all failed closed without stale UI evidence;
- [x] the synthetic source database remained unchanged throughout (sha256 `a8ce00c98a9e34f46b4e1a5236d1b3c5351743e5eaa41aff550dd1f204fc9562`);
- [x] real Chrome inspection confirmed as-of labels, no-query-on-view messaging, pinned version/mode/status/timestamps, citations/evidence, explicit confirmation, exports and revocation clearing.

Acceptance artifacts were retained only under test-owned scratch paths. No customer data or real development DB was opened.

## 4. Upgrade and recovery — complete

- [x] Synthetic old-schema database upgraded on startup without record loss.
- [x] Second startup/upgrade was idempotent.
- [x] Conversations/messages/execution traces/saved reports survived unchanged.
- [x] Additive current columns/tables/triggers were initialized compatibly.
- [x] Expired/crashed running ReportRun reconciled to `interrupted / deadline_expired` without re-execution.
- [x] Existing completed runs/snapshots remained unchanged during recovery.
- [x] Test document/source disable and recovery procedure was recorded.
- [x] No downgrade-by-table-drop and no test against the real development database.

## 5. Hosted CI

Acceptance execution head `8e2a4db5facecfb03602d3c5128ea802bcf33564`:
- [x] fast PR CI `37426623882` — backend + frontend green;
- [x] `workflow_dispatch suite=full` `37431715041` — full backend + frontend green.

Final documentation-only review head must repeat exact-head fast/full CI after this report is finalized. After human-authorized merge only, post-merge full main-push CI must be green before VS4 is recorded accepted.

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

**VS4-D runtime acceptance is complete and the batch is ready for final review once the documentation-only final head passes exact-head fast/full hosted CI.**

The assembled VS4 feature set has passed the required isolated integrated runtime, browser, restart, export, authorization, upgrade and recovery checks. This does not remove the retained VS5 identity, issue #6 lifecycle, issue #7 migration, export-recall, or VS6–VS8 limitations.

Do not merge or begin VS5 until human review authorizes merge. After merge, require a green full main-push CI before recording VS4 accepted.
