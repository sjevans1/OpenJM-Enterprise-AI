# VS4-B2A — Immutable source-bound report definitions

> Status: implementation branch; automated GitHub CI and review required. No reporting execution endpoint exists.

## Intent

VS4-A snapshots and VS4-B1 manual Chat rerun preflights remain unchanged. This increment creates **read-only definition records** from an available source report, with immutable version `1` and exact, server-derived source scope. It does not run SQL, Knowledge search, an LLM, or any agents.

## API

- `POST /api/reports/{report_id}/definitions` with JSON **`{}` only**. Unknown fields (including SQL, model, question and source IDs) return HTTP 422.
- `GET /api/reports/{report_id}/definitions` returns authorized definitions (up to 20).
- `GET /api/reports/{report_id}/definitions/{version}` returns that version or 404.

On success, the response includes:

```json
{
  "id": "definition-uuid",
  "report_id": "saved-report-uuid",
  "version": 1,
  "question": "What is the current result using the policy?",
  "mode": "hybrid",
  "pinned_document_ids": ["policy-document-id"],
  "pinned_source_tables": {"source-id": ["finance"]},
  "created_at": "2026-10-02T01:00:00Z",
  "executes_queries": false,
  "runnable": false
}
```

It contains no secret, SQL or model output. The only update operation is **none**: there is no endpoint to modify a version, create arbitrary version 2, change pins, or execute it. Version 1 is registered idempotently with a unique `(report_id, version)` constraint. Future versions must be append-only and separately authorized, never overwrite an earlier definition.

## Trust boundaries

The server resolves a report owned by the current development identity, ensures the report's source documents and structured tables remain authorized, verifies each pinned table is still present in current discovered schema, then performs the VS4-B1 preflight to obtain the original question/mode from the server transcript. **Client cannot supply any question, source, table or SQL.**

Pins derive from full immutable Evidence, including equivalent-source references and nested grounded policy references. Caps: 32 document identifiers, 8 structured source identifiers and 32 tables total. Malformed stored JSON, altered scope, missing/changed original question, revoked document, disabled data source, changed table authorization or dropped table fail closed with 409/422, without revealing historical answers.

A definition points to its SavedReport by a SQLite-enforced cascading foreign key. Deleting a report deletes only its definitions and snapshot; source documents, database source and conversation persist. Startup adds only a new table through the current SQLite `Base.metadata.create_all()`; versioned migrations and PostgreSQL metadata remain issue #7.

## Non-goals and explicit gaps

- **No report execution** and no model re-planning yet.
- VS4-B2B (#19) must propagate pins into the Knowledge tool's candidate-document list and into the structured planner's allowed data-source/table context *before* any query or retrieval. A post-hoc filter is insufficient.
- VS4-B2C must validate source permissions **at actual execution time**, rerun policy/currency/year gates and produce a new immutable run/evidence record; never replay `Evidence.metadata.sql`.
- Existing VS4-B1 "Prepare rerun in Chat" remains an **exploratory** manual request via Chat and is not source-pinned. Do not claim this new record makes ordinary Chat calls pinned.
- Single dev identity, document lifecycle concurrency and metadata migration limitations still apply (#6, #7, VS5).
- No Workspace dependency: it is still a standalone product.

## Acceptance

- [ ] GitHub CI backend fast test + Ruff green, frontend build green.
- [ ] Deterministic tests demonstrate Data/Knowledge/Hybrid source pins, no executed traces or SQL, idempotency, read/list behavior, unknown-field rejection, post-save table/document revocation, stale schema, question tampering and cascade only for definitions.
- [ ] Validate against temporary copy of pre-B2A SQLite database and verify existing records and idempotent restart (Hermes local acceptance; not on production data).
- [ ] Review current source/permission scope race restrictions.
- [ ] Full main-branch regression after authorized merge.
- [ ] Maintainer authorizes merge.

Until accepted, this implementation is draft and **not a full VS4-B completion**.
