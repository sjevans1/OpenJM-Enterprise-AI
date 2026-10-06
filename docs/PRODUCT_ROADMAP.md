# OpenJM Enterprise AI — Product Roadmap

**Current accepted main:** `52e7fd8c4c514538874a6b167b9fb4bd64321806` (VS4-C2 merge).  
**Active batch:** VS4-D integrated acceptance and closure.  
**Checklist source:** roadmap issue #11 and the current batch contracts in `docs/plan/`.

## Delivered foundation

| Area | Delivered state |
| --- | --- |
| VS1 — Chat + Knowledge | Server-owned conversations, document catalog, real RAG evidence/citations and deletion acceptance. |
| VS2 — Structured Data | User-scoped relational sources, encrypted credentials, schema discovery, governed read-only SQL and execution traces. |
| RAG Phase E | Evidence deduplication and bounded neighboring-chunk expansion. |
| VS3 — Explicit execution modes | Chat, Knowledge, Data, independent Hybrid and policy-dependent Hybrid with grounded SQL/policy controls. |
| VS4-A | Immutable permission-scoped saved snapshots. |
| VS4-B1/B2A/B2B | Explicit Chat preflight, immutable source-bound definitions and source scope propagated before retrieval/planning/execution. |
| VS4-B2C1/B2C2 | Immutable ReportRun lifecycle/history and explicit manual governed execution with idempotency, budgets, reauthorization and bounded results. |
| VS4-C1 | Manual Reports UX: definition selection, explicit confirmation, retry/idempotency handling, immutable run history/failure/revocation UX. |
| VS4-C2 | Governed exports of already-persisted bounded results: structured CSV and escaped self-contained HTML/print artifacts. |

## VS4-D — current

VS4-D does not add a new broad product surface. It reconciles prior evidence, runs the assembled isolated acceptance flow, proves synthetic upgrade/recovery behavior, and produces the final VS4 acceptance package. VS4 is not accepted until D merges by maintainer authorization and post-merge main CI is green.

## Planned remaining vertical slices

### VS5 — Identity, authorization and tenancy
Replace the development `dev_user_id` assumption with trusted identity/tenant context, OIDC/SSO and negative isolation/revocation tests. Issue #6 document lifecycle concurrency and #7 versioned application-metadata migrations remain prerequisites for production multi-user claims.

### VS6 — Bounded actions / agent runtime
Add explicit read/write tool classes, planning budgets, approval boundaries for mutation, idempotency and reconciliation. No model-direct arbitrary network or SQL authority.

### VS7 — Connectors and automations
Permission-aware, revocable integrations and scheduling only after VS5/VS6 controls. Workspace remains a separately deployed optional product connected only through versioned APIs under issue #9.

### VS8 — Product operations and delivery
Repeatable on-prem packaging, observability, backup/restore, update/rollback, retention, secure defaults, performance/load acceptance and commercialization gates.

## Known parallel correctness work

- Issue #6 — cross-process document lifecycle correctness.
- Issue #7 — versioned SQLite/PostgreSQL application-metadata migrations.
- Issue #9 — optional disabled-by-default Workspace API boundary.
- PR #5 — stale Phase E draft; reconcile/close, never merge wholesale.
- PR #13 — stale documentation draft. Its useful roadmap/README content is being reconciled into VS4-D rather than merged blindly.

## Delivery rules

1. Branch from current `main`; no direct feature pushes to main.
2. Preserve unrelated local files and use isolated synthetic acceptance fixtures.
3. Focused tests during iteration; exact-head fast/full hosted CI at review gates.
4. Negative security cases fail closed and prove no unauthorized tool execution.
5. Real-model/browser evidence is required where the contract calls for it.
6. Do not claim VS5–VS8 or production multi-user readiness from VS4 acceptance.
