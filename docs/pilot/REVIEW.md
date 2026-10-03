# VS4-B2C1 — PR #28 correction pass evidence (inline, synthetic data only)

## Outcome and scope
Batch / issue / plan revision: VS4-B2C1 correction pass over cc9ebdc. Issue #23. Plan vs4-pilot-1. Branch `milestone/vs4-b2c1-run-history`.

Problem solved and user-visible behavior: five independently reproduced review defects closed — (1) run list/detail re-check source authorization and fail closed with 409 on revoked document / disabled source / removed table grant; (2) startup recovery commits its interruption inside `engine.begin()` using the ORM's exact stored datetime format, idempotent and non-expired-preserving; (3) reservation owns the trusted immutable definition+report owner and rejects foreign-owner rows and altered caller intent/pins; (4) identity trigger now compares `NEW.user_id`, installed via drop-and-recreate so upgraded DBs get the corrected body; (5) result read bound uses the writer's MAX_RESULT_BYTES (131,072) so a persisted 69,172-byte result reads without 422. Contract gap closed: `_canonical_key` accepts only canonical lowercase hyphenated UUIDs.

Implementation choices / approved deviations: `install_report_run_triggers` drops then recreates triggers (no IF NOT EXISTS) to replace legacy defective bodies on upgraded databases. `_recover_stale_report_runs(*, engine=None)` accepts an engine argument for isolated repeatable upgrade/recovery tests.

Preserved boundaries and known limitations: No public run-submission endpoint, model, retrieval, SQL-engine, or trace-writing calls in any B2C1 path (asserted by zero-side-effect read sentinels). The unrecorded public `POST /runs/{id}/revoke` route was removed from this read-only batch; `revoke_report_run` remains an internal service function for B2C2/C1. No frontend changes; no schema beyond report_runs; no provider/identity changes. Existing Chat/report behavior unchanged.

## Evidence
- Code SHA (full), PR URL, base main SHA: final `982dc01456dc4fbe8446abf01fce17c4cdae6ca0`; PR https://github.com/sjevans1/OpenJM-Enterprise-AI/pull/28; base main `208d329735348ac4cee7b7adfe9a62037bc5b7c2`.
- Fast PR CI (run 37080887812, pull_request, 982dc01): Backend success — Ruff critical correctness checks (success), Fast backend regression (pull requests) (success), Full backend regression (skipped — fast tier by design); Frontend success.
- Full dispatch (run 37081272016, workflow_dispatch suite=full, 982dc01): Backend success — Ruff (success), Full backend regression (main or explicitly dispatched) (success, not skipped); Frontend success — frontend tests (success) + TypeScript check and production build (success).
- Gate: `python scripts/pilot_ci_gate.py --pr 28 --expect-head 982dc01456dc4fbe8446abf01fce17c4cdae6ca0 --full-run 37081272016` → accepted=true (fast + full evidence blocks, both jobs, selected tier steps succeeded).
- Local verification (mirrors CI tiers; non-executing batch, no Gemma dependency): fast tier `pytest -q --maxfail=3 --ignore=tests/test_upload_source_name_integration.py` → 410 passed, 19 subtests; full tier `pytest -q --maxfail=3` → 412 passed, 1 warning, 19 subtests (includes embedding integration); `ruff check . --select E9,F63,F7,F82` → All checks passed; `frontend/npm test -- --run` → 2 files / 6 tests passed.
- Commands / environment / model / timestamp / assertions: Python 3.11 venv; FastAPI ASGI test client; file-backed SQLite (aiosqlite, 5s busy_timeout). New module `backend/tests/test_report_run_regressions.py` (28 tests) asserts: 409 on revoked document/disabled source/removed grant + 200 control; recovery persists interruption in a fresh session; repeated recovery idempotent and runs-preservative; foreign-owner definition+report rejected; altered intent+pins rejected; identity trigger blocks user_id change on a running run; drop-and-recreate replaces a legacy defective trigger; 69,172-byte persisted result reads 200; injected oversized 422; _canonical_key rejects braces/urn/compact/uppercase/whitespace/empty and accepts canonical lowercase; history reads invoke zero model/retrieval/SQL/trace calls and write no traces/messages; synthetic pre-B2C1 upgrade preserves conversation/message/report/definition counts; report deletion cascades runs with no resurrection and no public revoke route.
- Fixture isolation, source read-only proof and test-owned cleanup: each regression uses `file_db`/`tmp_path` isolated SQLite through `seed_definition` (synthetic Conversation/Message/SavedReport/ReportDefinitionVersion built through the real trusted model path); no production/neighbor data touched; engines disposed in fixtures.
- Migration/recovery and failure-case evidence: `test_repeated_startup_recovery_is_idempotent` and `test_synthetic_pre_b2c1_upgrade_preserves_data` (pre-B2C1 DB: report_runs dropped, legacy triggers present) prove committed interruption persistence and row-count preservation across upgrade; `test_report_deletion_cascades_runs_and_never_resurrects` proves FK cascade with no resurrection; `test_large_valid_result_reads_back` and `test_oversized_result_injected_directly_still_422` prove the bound boundary at the exact reported 69,172-byte size.
- Independent reviewer findings / corrections: all five defects addressed; the 6th handoff item (unrecorded public revoke route) deferred and covered by `test_public_revoke_route_absent_in_b2c1`.
- Remaining manual checks: none before review.

## Pilot measurements
Started / finished (UTC): 2026-10-02 correction commit. Active implementation time minimal (root-cause fixes are mechanical against the existing B2A/B2B pattern); waiting dominated by two hosted CI runs (fast PR + full dispatch) and one transient post-dispatch API network blip that recovered without re-dispatch (the created run 37081272016 completed successfully). Coding model(s): primary provider per branch config. Repair attempts / service retries / review corrections: 0 in-batch repairs; 1 transient API network blip (service, single retry recover). In-flight B2C1 acceptance: 412+28 tests; fast+full hosted CI green; gate accepted. Interruption/resume (B2C1 mandatory): not applicable (non-executing batch); recovery durability covered by tests.

## Decision
- [x] Final committed revision matches attached evidence (982dc01).
- [x] Expected jobs and selected tier steps succeeded (fast: Fast backend + Frontend; full: Full backend + Frontend), not skipped/neutral.
- [x] Applicable local and negative acceptance gates passed (412 local + 28 regressions; ruff clean; 409/422/404 negative cases; read sentinels).
- [x] PR ready for review; no auto-merge enabled.
- [-] Maintainer authorized merge — pending.
- [-] Actual merged main SHA + full post-merge CI — pending review/merge.

Stopped at the review boundary. B2C2 and reusable-skill work remain paused until B2C1 acceptance.