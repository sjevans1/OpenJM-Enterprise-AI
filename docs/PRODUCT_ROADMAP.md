# OpenJM Enterprise AI — Product Roadmap

## Current post-VS8 phase — observed 2026-10-08

**Inspected main:** `8626f422a663ce238157a4023df23b9a5ed43c96` (accepted BV1/BV2/M1 foundations).  
**Execution umbrella:** [Issue #45](https://github.com/sjevans1/OpenJM-Enterprise-AI/issues/45); commercialization: [Issue #46](https://github.com/sjevans1/OpenJM-Enterprise-AI/issues/46).

BV3-A/B/C remain open at inspection. The agreed next sequence is:

1. Finish BV3-C PostgreSQL acceptance and integrate the accepted BV3 train.
2. Begin **INF1-A** serving contracts/registry/routing/attribution and **M2** usage
   aggregation in parallel where schema/API ownership is reconciled.
3. Reconcile INF1 admission/reservation semantics with **M3** before commercial
   completion; follow with INF1 capacity telemetry/reconciliation qualification.

The [INF1 plan](plan/INF1.md) links architecture, contracts, A/B/C acceptance and
an executor handoff. This documentation does not mark INF1 implemented, accept
BV3 or authorize a merge. BV4/BV5/BV6 remain under Issue #45's existing scope.
Workspace remains separate.

## Historical VS4 roadmap snapshot

The remaining sections record the earlier VS4 stage and then-planned VS5–VS8
work. They preserve history and do not override the current phase above. Refresh
live PR/main evidence before execution.

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

## VS4 — accepted

VS4 closed through PR #32, merge `86164252c38c74e1d7417d61c310951472ff81f8`. Post-merge main CI run `37435164243` passed the full backend regression and frontend. Integrated VS4-D acceptance passed 39/39 assembled runtime/browser assertions plus 10/10 synthetic upgrade/recovery checks. Retained limitations are carried forward explicitly into VS5 and issues #6/#7.

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

