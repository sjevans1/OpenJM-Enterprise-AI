# VS4-B2B — Source-scope enforcement before governed execution

> Scope plumbing and deterministic regression only. **No public report-run endpoint** is introduced. VS4-B2A definitions remain explicitly `runnable=false`.

## Why

VS4-B1's editable Chat handoff intentionally uses the user's currently authorized
sources. A reusable report is different: it must never silently replace or
broaden its pinned documents, data sources or table set. Checking Evidence
after retrieval or SQL would be too late.

## Contract

`ReportSourceScope.from_pins(document_ids, source_tables)` creates a frozen,
bounded set of source identities. The only intended production input is
`load_validated_definition_scope(db, report_id, version, user_id)`, which
re-checks B2A report ownership, current authorization, discovered schema,
immutable evidence scope, original question and original execution mode.
Neither an untrusted request nor the model may submit new pins.

**Scope propagation (internal only):**

- `OpenJMOrchestrator.plan(..., scope=...)` performs an all-sources preflight
  and refuses general Chat mode; requires documents for Knowledge and both
  documents and structured sources for Hybrid, structured sources for Data.
- `_ready_documents` restricts the document catalog; missing/revoked pins
  deny before retrieval. `KnowledgeSearchTool` selects ONLY pinned and
  current owner-owned, ready/indexed docs *before* calling DB-GPT/Chroma and
  rejects returned Evidence or equivalent_sources outside pins.
- `StructuredPlanner._sources` restricts to pinned source IDs, rejects any
  missing, disabled or changed grant/schema, and displays ONLY pinned table
  schemas/columns to the model. Missing pins do not fall back to all sources.
- `StructuredQueryTool` checks the selected source ID against pins, checks
  dependent Hybrid policy-doc ID against doc pins, revalidates current
  explicit source permissions/schema, and passes scoped tables to the SQL
  executor.
- `execute_structured_query(..., scoped_tables=...)` rechecks live grants,
  intersects policy-allowed tables and columns with pins **before** decrypting
  connection credentials/opening the source DB. SQL AST read-only, row bounds,
  timeout and grounded parameter checks stay in force.
- Dependent Hybrid runs the same scoped Knowledge retrieval and scoped
  structured planning; no old `metadata.sql` is ever replayed.

**Existing Chat compatibility:** all scope parameters default to None and do
not alter the existing `/api/chat` request schema or execution routing.
The frontend, Workspace and consumer APIs are unchanged.

## Important remaining gates before manual ReportRun API

1. B2C must load the verified definition, not accept arbitrary report scope
   JSON or source IDs from a browser.
2. B2C must preserve explicit owner authorization at execution time and must
   fail closed on **partial** Hybrid outcomes rather than returning an
   incomplete run as a successful report.
3. Re-extract dependent Hybrid policy threshold/currency/fiscal year from
   current pinned evidence every run; verify SQL AST+grounded_parameter again.
4. Persist immutable ReportRun with result evidence, version, timestamps,
   failure state, provenance and idempotency. Do not overwrite VS4-A.
5. Request-specific cross-process source revocation race remains #6; identity
   remains the single dev-user context until VS5. Migrations are #7.
6. Run real local Gemma/Chroma/SQLite acceptance in the controlled WSL test
   environment before claiming end-to-end pinned reporting.

## CI acceptance

- [ ] Focused `test_report_scope_enforcement.py` plus all existing backend
  tests pass in GitHub Actions.
- [ ] Frontend TypeScript/Vitest/build regression passes.
- [ ] Confirm no unpinned source ID or table name reaches model prompt.
- [ ] Confirm unauthorized table SQL is rejected by executor **before**
  credential decryption / SQL connection.
- [ ] Confirm revoked pinned documents reject before vector retrieval.
- [ ] Review existing-mode compatibility, especially independent/dependent
  Hybrid, without suppressing old tests.
- [ ] Maintainer review/merge approval.

Keep PR small, never enable report execution until B2C meets all gates.

## Security-review hardening: schema identity and evidence provenance

The B2B security review found two additional routes requiring fail-closed checks:

- **Schema-name ambiguity:** a pin for `finance` must not grant
  `private.finance`. Scoped SQL policy uses **exact parsed table identities**
  rather than the legacy fallback to the final name segment. A discovered
  unqualified table is rejected when multiple schemas contain the same base
  name. PostgreSQL report pins must be explicitly schema-qualified; older
  unqualified report evidence is refused until safely re-established as
  canonical schema-qualified scope.
- **CTE shadowing:** `WITH finance AS (...)` must not hide a reference to
  `private.finance`; only unqualified CTE references are excluded from base
  table authorization checks.
- **Untrusted vector provenance:** retrieved `equivalent_sources` must be a
  bounded list of valid source identities entirely contained in the pinned
  document set, and Knowledge evidence must have source type `document`.
  Malformed elements are denied rather than silently skipped.

These guards operate before source credential decryption and before executing
any query. Their deterministic security regressions are in
`backend/tests/test_report_scope_enforcement.py`. None of the changes expose
a public report execution path.


### Scoped CTE limitation

Until SQLGlot lexical CTE-scope resolution is incorporated with separate
negative security tests, **source-pinned report SQL rejects all CTEs**. This is
intentional fail-closed behavior: a nested CTE named like a real table could
otherwise conceal an out-of-scope base table from global-name matching.
Direct single-statement SELECT/aggregate queries against exactly pinned
tables remain permitted. Existing unscoped Chat's CTE support is unchanged.
B2C cannot remove this restriction without replacing the global CTE-name
check with validated per-query-scope source resolution.
