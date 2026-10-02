# VS4-A — Saved report snapshots (draft acceptance handoff)

**Branch:** `feature/vs4-a-saved-report-snapshots` (based on merged VS3-C3 main `5f5821f6ad1b4e3368e95a5898508e892f530588`).

**Status:** Implemented and committed for review, **not yet executed/accepted**. Treat all tests as pending until Hermes runs them. Do not merge this draft just because files exist.

## What is implemented

- Backend `SavedReport` metadata table created using existing `Base.metadata.create_all` on startup. Only a new table is introduced; pre-existing conversation/document/data-source rows remain unchanged. This is still the existing SQLite bootstrap model, **not** the planned Alembic/PostgreSQL migration program (#7).
- `POST /api/reports`: save an existing, owned, persisted **assistant** message with Knowledge/Structured/Hybrid Evidence; the caller can only submit `message_id` and optional short title. No client-provided SQL, answer or Evidence are accepted as authoritative. Unique owner/message constraint makes repeated saves idempotent.
- `GET /api/reports`: bounded, owner-scoped metadata pagination, current source checks and sensitive-title masking when unavailable.
- `GET /api/reports/{id}`: historical answer + Evidence, with current authorization/availability checks for all document, SQL, equivalent-source and nested grounded-policy source identifiers. Deny stale snapshots without returning their content.
- `DELETE /api/reports/{id}`: idempotent report-only deletion; documents, data sources and chat history remain untouched.
- Frontend: Reports nav and read-only snapshot view, historical as-of label, evidence panel, save action on grounded chat messages, deletion; no auto-refresh, SQL replay, automated report generation, PDF export, agents or scheduling.

## Limitations (must remain explicit)

1. The existing app has a single development identity `settings.dev_user_id`; no claim of true user authentication or multi-tenant isolation before VS5.
2. Every source is rechecked at read/list time, but the existing document lifecycle concurrency issue #6 remains separate. Do not claim cross-process revocation guarantees until #6 resolves it.
3. Existing application-metadata initialization uses SQLite-specific assumptions; #7 covers migrations and backend PostgreSQL support.
4. Snapshots are intentionally **not live** and retain only bounded previously persisted answer/evidence; no report rerun/export in VS4-A. VS4-B is a separate gated effort.
5. No local Gemma, Chroma, frontend or full regression test has been executed in this remote GitHub editing session.

## Acceptance plan for Hermes

Use the branch and do not touch protected untracked `scripts/serve_frontend.py` or `test-documents/`.

**Gate A (focused):** `cd backend && python -m pytest -q tests/test_reports.py`. Review and fix any import/schema/test failures. Recheck source provenance validation, duplicate saves, role+owner scope, missing/disabled source, nested grounded policy, equivalent source, validation bounds, delete idempotency and historical-as-of semantics.

**Gate B (frontend):** `cd frontend && npm run test -- --run && npm run build && npm audit`. Verify save from chat, open/list/delete, user-facing stale warning and no cached Evidence shown after 409. Check there is no 204 JSON parsing error.

**Gate C (migration):** start backend against a temporary **copy** of a pre-VS4 local SQLite database, verify the saved_reports table appears after startup and existing conversation/history/source records are identical. Run startup twice. Never migrate or delete the user's actual local DB as a test fixture.

**Gate D (end-to-end):** with isolated test-owned Knowledge document and SQLite data source, create one real Knowledge answer, one read-only Data answer and one Hybrid answer via local Gemma + Chroma. Save/read each by API and UI; confirm both DOC and DATA citations and original execution-trace evidence. Disable/deindex/remove each source and confirm report read denies content without invoking the model or connected data SQL. Delete only test-owned resources and snapshots.

**Gate E (regression):** one full `python -m pytest -q` backend run, production frontend build and targeted negative tests after all focused corrections pass. Report true totals and any warnings; no repeated noisy push-based Actions runs.

## Merge gate

Do not merge until all five gates are documented with actual outputs and the maintainer authorizes. Update roadmap #11 and VS4-A issue #12 upon acceptance.
