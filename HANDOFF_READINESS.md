# OpenJM Enterprise AI — Handoff Readiness

Observed at accepted `main@6243f485ca535df9f619559ff1e03ba15e1e9142`.

This page is the current handoff position. `docs/ZTK1_ACCEPTANCE.md` is the final
zero-tribal-knowledge handoff evidence package.

## Current position

The broad post-VS8 functionality programme is substantially complete. Accepted main includes BV1-A/B/C, BV2, BV3-A/B/C, BV4, BV5-A/B/C, BV6-A/B, M1/M2/M3, INF1-A/B/C, E1/E2/E3, delegated support-content access, and REL1 preparation documentation.

See `docs/ACCEPTANCE_MATRIX.md` for the current evidence map.

## Readiness checklist

| Area | Status | Current position |
| --- | --- | --- |
| Core product functionality | COMPLETE | Major Issue #45 feature lanes (including #40-#44 surfaces) are accepted. |
| Security/governance | COMPLETE | Tenant/group/classification enforcement, report/artifact authorization and delegated support have accepted automated evidence, plus bounded live sanity checks. |
| Productization / installer | COMPLETE | REL1 implementation, package capability and the canonical production installer are accepted. |
| Final browser journeys | COMPLETE | End-user, steward, client-admin and OpenJM-operator journeys executed against the production-installed artifact (frozen REL1 artifact `709ce47b`). |
| Live OIDC/PostgreSQL support path | COMPLETE | Live Keycloak 26.7.4 OIDC authorization-code + PKCE against PostgreSQL 16.15 on a clean production host. |
| Real vector/RAG authoritative-candidate path | COMPLETE | Knowledge ingestion/indexing, governed retrieval and the unauthorized negative are accepted. |
| Detailed readiness under DB outage (D1) | COMPLETE | `/api/ready/detail` returns a bounded component report instead of 500; validated live and closed via Issue #105 / PR #106. |
| Rahkia application cutover | DEFERRED | Deliberate product decision. The only explicitly deferred specialist phase. |
| Workspace boundary | OK | Workspace remains a standalone repository/product. |
| Release identity consistency | COMPLETE | Canonical runtime/backend/frontend/npm-lock version is normalized to 0.2.0 and machine-checked. |
| Handoff qualification (ZTK1) | COMPLETE | Independent zero-tribal-knowledge examination; see `docs/ZTK1_ACCEPTANCE.md`. |

Issue status: **#79 CLOSED / COMPLETED** (clean production host + production-installed
browser/persona qualification against frozen REL1 artifact `709ce47b`). **#105 CLOSED /
COMPLETED** (D1 readiness correction). **#104** carries the ZTK1 handoff evidence.

Evidence boundary: the REL1-B4 clean production/browser live qualification belongs to
the frozen artifact `709ce47bf3ae1b7135446c376bcf312b25606b5c`. Later `main` commits do
not automatically inherit that specific live-host qualification.

## Remaining documentation

- refreshed Rahkia specialist handoff when that phase starts (explicitly deferred);
- ordinary doc upkeep as the product evolves.

Reference set for an incoming operator: `README.md` (orientation),
`docs/INSTALLATION.md`, `docs/CONFIGURATION.md`, `docs/OPERATIONS.md`,
`docs/BACKUP_RESTORE.md`, `docs/UPGRADE_ROLLBACK.md`, `docs/SECURITY_POSTURE.md`,
`docs/ACCEPTANCE_MATRIX.md`, `docs/ZTK1_ACCEPTANCE.md`.
