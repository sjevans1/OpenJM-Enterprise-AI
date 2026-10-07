# VS4-B2C1 — PR #28 correction pass evidence

## Outcome and scope

Batch / issue / plan revision: VS4-B2C1 (PR #28 correction pass). Issue #23. Plan revision vs4-pilot-1. Branch `milestone/vs4-b2c1-run-history`.

Problem solved and user-visible behavior: five independently reproduced defects from the review of cc9ebdc are closed. Run-history list/detail now re-check source authorization and fail closed with 409; startup recovery commits its interruption (idempotent, preserves non-expired runs); reservation binds to the trusted immutable definition and owner (caller intent and foreign-owner rows rejected); the identity trigger protects `user_id`; the result read bound matches the writer's 131,072-byte bound so a 69,172-byte persisted result reads without 422; `_canonical_key` rejects non-canonical UUID forms. The unrecorded public revoke route was deferred out of this read-only batch.

Implementation choices / approved deviations: trigger install is now drop-and-recreate (`install_report_run_triggers`) so upgraded databases receive the corrected identity trigger rather than retaining `IF NOT EXISTS`; recovery accepts an optional engine argument for isolated upgrade/repeatability tests and compares datetimes with the ORM's exact stored format (`YYYY-MM-DD HH:MM:SS.ffffff`). Per the reviewer's handoff, `revoke_report_run` remains an internal service function only (no public route) pending B2C2/C1.

Preserved boundaries and known limitations: no public run-submission endpoint, no model/retrieval/SQL-engine/trace-writing calls in any B2C1 path (enforced by read sentinels). Existing Chat/report behavior stays green. This is a non-executing batch; Real Gemma is not required. The synthetic fixtures are deterministic (no real development DB copies).

## Evidence

- Code SHA (full), PR URL, base main SHA:
  - Final committed revision: `982dc01456dc4fbe8446abf01fce17c4cdae6ca0`
  - PR https://github.com/sjevans1/OpenJM-Enterprise-AI/pull/28
  - Base main: `208d329735348ac4cee7b7adfe9a62037bc5b7c2`
- Fast PR CI run / selected backend test step / frontend result:
  - Run 37080887812 (pull_request, 982dc01) — completed, success
  - Backend / Python 3.11: Ruff critical correctness checks (success); Fast backend regression (pull requests) (success); Full backend regression (skipped — fast tier by design)
  - Frontend / Node 22 (success)
- Full dispatch run and SHA / full backend step / frontend result:
  - Run 37081272016 (workflow_dispatch suite=full, 982dc01) — completed, success
  - Backend / Python 3.11: Ruff critical correctness checks (success); Full backend regression (main or explicitly dispatched) (success)
  - Frontend / Node 22: frontend tests (success); TypeScript check and production build (success)
- Gate verification:
  - `python scripts/pilot_ci_gate.py --pr 28 --expect-head 982dc01456dc4fbe8446abf01fce17c4cdae6ca0 --full-run 37081272016` → `accepted: true` (both tier evidence blocks present: fast PR + full dispatch, both jobs, selected steps succeeded).
- Local verification:
  - Required and not-applicable with contract reason: local fast/full pytest mirrors CI tiers (this is a non-executing batch with no real Gemma dependency).
  - `python -m pytest -q --maxfail=3 --ignore=tests/test_upload_source_name_integration.py` → 410 passed, 19 subtests (fast tier, excludes live embedding integration, matches CI).
  - `python -m pytest -q --maxfail=3` → 412 passed, 1 warning, 19 subtests (full tier, includes embedding integration, matches CI full).
  - `python -m ruff check . --select E9,F63,F7,F82` → All checks passed!.
  - `frontend/npm test -- --run` → Test Files 2 passed; Tests 6 passed.
- Actual commands / environment / model / timestamp / assertion results:
  - Environment: Python 3.11 (venv), FastAPI test ASGI client, file-backed SQLite (aiosqlite 5s busy_timeout), same as CI.
  - New regression module `backend/tests/test_report_run_regressions.py` (28 tests) asserts: run reads 409 on revoked document / disabled source / removed table grant; 200 as control; startup recovery persists interruption in a fresh session; repeated recovery idempotent and preserves running rows; foreign-owner definition and report rejected; altered caller intent and altered pins rejected; identity-trigger blocks `user_id` change on a running run; drop-and-recreate replaces a legacy defective trigger; 69,172-byte persisted result reads back (200); injected oversized result 422 (defense in depth); `_canonical_key` rejects 10 non-canonical forms and accepts canonical lowercase; history reads make zero model/retrieval/SQL/trace-write calls; synthetic pre-B2C1 upgrade preserves conversations/messages/reports/definitions; report deletion cascades runs with no resurrection; no public revoke route 405/404.
- Fixture isolation, source read-only proof and test-owned cleanup:
  - Each regression uses `file_db`/`tmp_path` isolated SQLite; `seed_definition` builds synthetic Conversation/Message/SavedReport/ReportDefinitionVersion rows via the real trusted model path; no production data used; engines disposed in fixtures.
- Migration/recovery and failure-case evidence:
  - `test_recover_stale_report_runs_marks_expired` and `test_repeated_startup_recovery_is_idempotent` assert the committed interruption persists across a fresh connection and preserves non-expired running rows; `test_synthetic_pre_b2c1_upgrade_preserves_data` proves an upgrade (old DB without report_runs, pre-existing triggers) preserves all counts; `test_report_deletion_cascades_runs_and_never_resurrects` proves FK cascade + no resurrection.
- Screenshots/artifacts (synthetic data only): N/A (API-level evidence).
- Independent reviewer findings / corrections: all five findings addressed; the unrecorded public revoke route (6th handoff item) deferred with a negative test.
- Remaining manual checks: none before review.

## Pilot measurements

Started / finished (UTC): 2026-10-02 (correction commit).
Active implementation time / waiting time / elapsed time: corrections built after review; CI wall-clock dominated by the two hosted runs (fast and full dispatch). No repair attempts within this correction batch (0); the prior setup blocker (npm `gh`) was resolved in the setup phase.
Coding model(s) actually used: primary provider (per branch config), unchanged.
CI runs and durations: fast run 37080887812; full dispatch 37081272016 (supersedes prior green full dispatch 37019716700 against cc9ebdc, which is now outdated at the final revision).
Repair attempts / service retries / review corrections: 0 in-batch repairs; one transient API network blip between dispatch and poll recovered without re-dispatch.

## Decision

- [x] Final committed revision matches the attached evidence. (982dc01)
- [x] Expected jobs and selected tier steps succeeded (not skipped/neutral). (fast: Fast backend regression + Frontend; full: Full backend regression + Frontend)
- [x] Applicable local and negative acceptance gates passed. (412 local + 28 new regressions; ruff clean; read sentinels; 409/422/404 negative cases)
- [x] PR is ready for review; no auto-merge enabled.
- [-] Maintainer authorized merge (record reference; Hermes does not merge). — pending human review.
- [-] Actual merged main SHA and full post-merge CI passed. — awaiting review/merge.

Stopped at review boundary. B2C2 not started.