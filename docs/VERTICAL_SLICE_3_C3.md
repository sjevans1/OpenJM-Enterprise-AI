# VS3-C3 — Dependent Hybrid (policy threshold → governed SQL)

**State:** implementation committed on feature/hybrid-vertical-slice-3; runtime acceptance remains pending until the complete backend regression, UI build and live gated trials have been rerun on the actual target workstation. This file describes code and contracts, not a claim of final acceptance.

## Why separate C3 from independent Hybrid B3

B3 combines two separately answered questions using authorized Knowledge and Data evidence. C3 is different: the *value used by the database query* depends on a passage in an authorized Knowledge document.

Example supported intent:
> Which customers exceed the **annual revenue threshold** in the policy?

The user must explicitly specify the revenue threshold, policy/document authority, and comparison direction. If the business period is specified, it must match an explicit period in the policy. Missing or contradictory facts cause a transparent controlled failure, **not a fallback to an LLM-provided number**.

## C3 execution architecture

\`\`\`text
User chooses HYBRID and asks policy-dependent question
   │
   ▼
Deterministic C3 intent, comparison and period checks
   │
   ├── Not a supported dependent revenue request ──► Existing B3 path
   ▼
knowledge.search (authorized user-scoped documents)
   │
   ▼
Policy threshold resolver
   ├─ Currency and exact numeric amount required
   ├─ One unambiguous policy revenue threshold
   └─ Requested annual/monthly/quarterly period must agree
   │
   ├── Missing/conflicting ──► fail closed (NO SQL call)
   ▼
Structured planner (authorized schema; user wording retained;
no document text; placeholder identifier, NEVER raw policy text)
   │
   ▼
SQLGlot AST dependent binding
   ├─ Exactly one SELECT and one placeholder
   ├─ Only direct revenue-column comparator, sole WHERE/HAVING
   ├─ Correct strict/inclusive comparison and metric period
   ├─ No union/OR/join/bypass/derived-metric inference
   └─ Replace identifier with Decimal-derived numeric SQL literal
   │
   ├── Invalid proposal ──► fail closed (NO SQL call)
   ▼
structured.query (unchanged existing governed capability)
   ├─ User/source authorization
   ├─ SQLGlot read-only validation and authorized schema
   ├─ Row bound, timeout, read-only DB connection
   └─ Existing ExecutionTrace
   │
   ├── Failure ──► no dependent data result claimed
   ▼
Authorized DOC evidence + DATA evidence with bound-threshold
provenance → grounded Hybrid synthesis → UI citations
\`\`\`

## Explicit security constraints

1. Evidence is data, never authority to run commands. Retrieved passages are **not** included in the structured planner prompt and are never interpolated into SQL.
2. The binder accepts only a server-defined \`__OPENJM_POLICY_THRESHOLD__\` token as a SQL AST identifier in a verified revenue comparison. It replaces this with a parsed positive \`Decimal\`, not user text.
3. The original \`structured.query\` tool remains the only executor; the binder does not bypass source authorization, SQL policy, audit, credential storage or row/time limits.
4. SQL execution must not happen if Knowledge retrieval, threshold extraction, period matching, planner proposal, AST binding or source validation fails.
5. Independent B3 Hybrid requests retain their existing execution route.
6. SQL provenance and policy source/evidence ID/citation/period/operator/value are included in returned data evidence. Raw source passages are not written into SQL.
7. Responses must not present partial Knowledge success as a completed dependent database answer.

## Limits: deliberate, not hidden

- V1 only supports clearly phrased *revenue* thresholds, explicit currency-denominated values, and exact direct revenue columns with matching period names (e.g. \`annual_revenue\`). It will not interpret policy tables, multiple eligible tiers, conversion across periods or currencies, date-derived annual aggregates, joins, vague "qualifies" semantics, percentage thresholds, competing policy versions, or ambiguous comparator phrases.
- The deterministic resolver is not a general policy rules engine. Near-term enhancement requires separate benchmark fixtures and a source-authority/version contract, *before* wider extraction.
- The general SQL planner still proposes SQL; the independent policy and AST checks must remain in force.
- Knowledge citation numbers follow document Evidence order; DATA citation numbers follow structured Evidence order independently.

## Test and acceptance matrix

| Gate | Expected proof |
| --- | --- |
| K1 | One authorized annual policy value resolves with exact source/evidence ID |
| K2 | No policy / no currency / mixed period / contradictory values → no planner and no SQL |
| K3 | Explicit > vs >= retained; ambiguous operator fails |
| K4 | Model-proposed hardcoded number, wrong metric, OR, UNION, JOIN, multi-statement or DML never execute |
| K5 | Hostile document passage is not included in structured planner prompt or SQL |
| K6 | Successful query passes \`structured.query\`; returned Evidence carries SQL and threshold provenance |
| K7 | Planner/schema/SQL tool failure does not synthesize a dependent data result |
| K8 | Independent B3 hybrid, GENERAL, KNOWLEDGE, DATA regressions remain green |
| K9 | Live SQLite fixture with annual_revenue = {299,300,301}: > returns only 301, >= returns 300 and 301 |
| K10 | End-to-end client UI labels and inline [DOC N]/[DATA N] agree; no credentials leaked |

A lightweight GitHub Actions safety check executes deterministic resolver/AST tests. It is **not** a substitute for the full backend suite or a real target-host model + document + SQLite integration. Never mark VS3-C3 accepted solely because syntax, mocked tool tests, or CI passed.

## Next checkpoint

- Review CI result and resolve any actual failures.
- Add isolated live C3 acceptance script with owned-only fixtures, guaranteed cleanup, and no modifications to pre-existing Knowledge/Data catalogs.
- Repeat full backend tests, frontend TypeScript build, and a real model response on target runtime.
- Do not merge into main until all acceptance gates are evidenced.
