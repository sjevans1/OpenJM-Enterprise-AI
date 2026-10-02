# OpenJM Enterprise AI — Roadmap after VS3-C3

**Last verified integration:** `main` merge `5f5821f6ad1b4e3368e95a5898508e892f530588` (PR #10). This roadmap is a planning artifact: check current `main` before developing.

**Source of truth for checklist/progress:** [Roadmap issue #11](https://github.com/sjevans1/OpenJM-Enterprise-AI/issues/11). Current implementation entry point: [VS4-A issue #12](https://github.com/sjevans1/OpenJM-Enterprise-AI/issues/12).

## Delivered foundation

| Area | Delivered evidence |
| --- | --- |
| VS1: Chat + Knowledge | Server-side conversations; uploaded-file catalog; real RAG and deletion acceptance. |
| VS2: Structured Data | User-scoped relational sources, encrypted credentials, read-only SQL policy and execution traces. |
| RAG Phase E | Evidence deduplication and exact neighboring-chunk expansion; see `docs/RAG_CONTEXT_EXPANSION_EVALUATION.md`. |
| VS3: Four modes | Explicit Chat, Knowledge, Data, Hybrid; independent and document-dependent Hybrid. |
| VS3-C3 hardening | Fail-closed policy extraction, conflicting-threshold rejection, USD/JMD compatibility, SQL AST grounding, citations/provenance. |

The VS3-C3 local handoff reported 246 passing backend tests, 96 security tests, local-model/SQLite/Chroma acceptance and a successful frontend build. Those figures are **local reported evidence**, not a GitHub Actions run on the merge.

## Planned remaining vertical slices

### VS4 — Saved Reports (next)

A. Save immutable evidence-backed snapshots of completed assistant answers, with ownership, source revocation checks, as-of timestamps, no replay, and safe deletion. See issue #12.

B. Separately introduce rerunnable **governed** report templates only after proving fresh permission checks, bounded read-only execution, parameter provenance and current source availability. No replay of unverified model-generated SQL.

C. Implement Reports UI, citations, safe bounded exports and local end-to-end acceptance.

### VS5 — Identity, authorization, tenancy

Remove single-development-user assumptions; introduce server-trusted tenant/user context, identity provider integration (OIDC), permission-aware retrieval, source access, report controls, audit and two-tenant isolation tests. Do not declare production multi-tenant support before negative leakage and revocation tests pass.

### VS6 — Bounded agent execution

Add explicit tool and action policy, planning limits and budgets, read-vs-write classification, human approval for external writes, idempotency, safe retries and traceability. No arbitrary model-controlled network or SQL privileges.

### VS7 — External connectors and automations

Build permission-aware, revocable integrations and scheduling only after tenant authorization and action policy are proven. The optional Workspace integration is governed by [issue #9](https://github.com/sjevans1/OpenJM-Enterprise-AI/issues/9) and must operate over versioned APIs exclusively. **Workspace remains a separately deployed product.**

### VS8 — Product operations and delivery

Reproducible local/on-prem deployments, secure defaults, backup/restore, health/metrics, service resilience, upgrade/rollback, retention, white-label options, operational security and load acceptance.

## Parallel correctness work

- [Issue #6](https://github.com/sjevans1/OpenJM-Enterprise-AI/issues/6) — cross-process document lifecycle races; prioritize ahead of multi-user production claims.
- [Issue #7](https://github.com/sjevans1/OpenJM-Enterprise-AI/issues/7) — application-metadata schema migration/versioning, SQLite preservation, PostgreSQL acceptance.
- [Issue #9](https://github.com/sjevans1/OpenJM-Enterprise-AI/issues/9) — optional, **disabled-by-default** API-only Workspace connector.
- [PR #5](https://github.com/sjevans1/OpenJM-Enterprise-AI/pull/5) — stale Phase E draft, must be diffed against current `main`; never merge wholesale.
- [PR #8](https://github.com/sjevans1/OpenJM-Enterprise-AI/pull/8) — obsolete competing C3 implementation, **closed without merge**.

## Delivery rules

1. Create small feature branches from current `main`; no direct feature pushes to `main`.
2. Preserve unrelated local untracked files; protect user content during acceptance cleanup.
3. Prefer focused tests while iterating, a full backend regression and frontend production build before merge; run real Gemma/Chroma/SQLite gates when available.
4. Negative security cases must fail closed and assert no unauthorized tool execution.
5. Keep acceptance evidence honest: distinguish a teammate's reported local results from independently checked CI.
6. Avoid CI notification noise from repeated interim pushes; run agreed verification gates.
7. Update roadmap issue #11 and the relevant implementation issue after each accepted PR.
