# OPS1 Acceptance Record: OpenJM Operations Control Center

- **Tested Git SHA:** `ecb7b69969a189a80aece6302836a4cd8d723a4f` (`main`; QA1, QA2 and QA3 accepted and merged).
- **Date:** 2026-10-11.
- **Package:** OPS1 (the final substantive product package before the commercial-readiness and closure review).
- **Outcome:** **OPS1 ACCEPTED. 27 live checks: 27 PASS, 0 FAIL.**
- **Rahkia:** DEFERRED. OPS1 claims no Rahkia capability.
- **Issue #79:** untouched. Host qualification remains a separate stream.
- **Issues #45 and #79:** left open.

OPS1 delivers the owner/operator experience for Plane 1 (the OpenJM platform
operator) without touching the other two planes. It builds on the accepted
control plane rather than replacing any part of it.

## Work packages

| Package | Scope | Result |
| --- | --- | --- |
| OPS1-A | Operator shell, fleet and release health | PASS |
| OPS1-B | Usage/economics, audit explorer, security | PASS |
| OPS1-C | Diagnostics, failure categories, operational-log status, alert findings | PASS |
| OPS1-D | Integrated live acceptance (API matrix + browser journey) | PASS |

## What was reused (nothing rebuilt)

- `backend/app/api/platform.py`, `platform_usage.py`, `system.py`, `operations.py`
- `backend/app/core/platform.py` (capability model) and `core/observability.py`
- the persisted `AuditRecord` trail and `ExecutionTrace` records
- usage metering and the M2 `usage_aggregation` layer
- the scheduler, connectors, notifications and INF1 inference metadata
- the existing Keycloak/OIDC identity and the existing client `AdminPanel`

No second operator identity model, RBAC system, audit model, readiness engine,
metrics registry, tenant model or usage meter was introduced.

## Backend additions

| Endpoint | Capability | Purpose |
| --- | --- | --- |
| `GET /api/platform/operations/overview` | `platform:metadata:read` | Release identity, readiness, model, fleet counts, findings |
| `GET /api/platform/operations/health` | `platform:metadata:read` | The shared per-component readiness report |
| `GET /api/platform/operations/releases` | `platform:metadata:read` | Version, release train/id, migration revision/head/drift |
| `GET /api/platform/operations/tenants` | `platform:metadata:read` | Fleet view: tenant metadata joined to aggregate usage |
| `GET /api/platform/operations/usage` | `platform:metadata:read` | Cross-tenant aggregate usage metadata |
| `GET /api/platform/audit` | `platform:metadata:read` | Bounded, filtered, paginated authoritative audit trail |
| `GET /api/platform/audit/summary` | `platform:metadata:read` | Recent-activity summary by event family |
| `GET /api/platform/security` | `platform:metadata:read` | Operators, delegations, privileged actions, denied categories |
| `GET /api/platform/diagnostics` | `platform:metadata:read` | Readiness, safe traces, failure categories, log status |

Two supporting modules were introduced:

- `app/services/readiness.py` moves the VS8 component builders out of the system
  router so `/api/ready/detail` and the control center share one implementation.
  The refactor is behaviour-preserving; the only test change is the monkeypatch
  target for the model-provider probe, whose assertion is unchanged.
- `app/services/operator_console.py` holds the safe operator projections: the
  audit allow-list, the trace projection, the security summary, the fleet join
  and the derived alert findings.

## Frontend additions

- `frontend/src/OperatorConsole.tsx`: a distinct operator surface with sections
  Overview, Tenants, Usage, Audit, Diagnostics, Security and Releases. Summary
  first, drill-down second, no raw JSON dumps.
- `frontend/src/App.tsx`: a `platform` navigation entry gated on
  `canOperatePlatform`, which keys on the same closed capability vocabulary the
  backend enforces. It is never inferred from a tenant role, an email, a domain,
  a display name or the environment.
- `frontend/src/api.ts`: the platform client methods.
- `frontend/src/auth.ts`: `platform_capabilities` carried through the session.

The Client Administration surface is untouched and remains a separate plane.

## The monitoring token is never in the browser

The control center calls capability-gated platform APIs that reuse the internal
readiness, audit and usage implementations server-side. The browser never calls
`/api/ready/detail` or `/api/metrics` with the monitoring credential.

Verified three ways:

1. unit test: no frontend source module references the monitoring token;
2. build check: the production bundle contains no such reference;
3. live check: the operator page was rendered with the token configured and the
   token value never appeared in the document.

## Live acceptance

Environment: the production-like QA environment (PostgreSQL 16.15 production
profile, Keycloak 26.7.4 OIDC, DB-GPT + Chroma vector/RAG, OpenCode Go
`glm-5.3-flash`), rebuilt from the accepted `main` tree.

| Area | Checks | Result |
| --- | --- | --- |
| Operator API capability matrix | 3 | PASS |
| Overview | 3 | PASS |
| Releases / migration | 1 | PASS |
| Fleet | 1 | PASS |
| Usage | 1 | PASS |
| Audit explorer | 5 | PASS |
| Security | 3 | PASS |
| Diagnostics | 3 | PASS |
| Leakage | 1 | PASS |
| Browser journey | 6 | PASS |

Key results:

- The platform operator reached all nine operator routes (200). The ordinary
  employee and the client admin were refused by all nine (403 each).
- `platform_capabilities` resolved to
  `platform:metadata:read`, `platform:operators:admin`, `platform:tenants:admin`
  for the operator and to an empty list for the client admin.
- Overview reported `0.2.0`, train `REL1`, profile `production`, ready true and
  model `glm-5.3-flash`, with no Rahkia claim.
- Releases reported migration revision `0021_support_content_scope` equal to the
  head, with no drift.
- The fleet view listed the seeded tenants (`acme`, `globex`) with aggregate
  usage; five tenants exist in the environment.
- The audit explorer read real QA1/QA2/QA3 history: 199 records, 31 denied
  decisions, and every required event family was found, including
  `platform.tenant.create`, `platform.operator.grant`, `platform.operator.revoke`,
  `platform.support.grant`, `platform.support.revoke`, `support.content.read`,
  `governance.data_source.policy`, `governance.group.member.add`,
  `governance.steward.grant` and
  `governance.report.curation.mark_authoritative`. No fake events were seeded.
- A refused platform access is recorded as an audited deny
  (`authorize:platform:...`).
- Security showed four operator capabilities, one support delegation (revoked and
  therefore not effective) and zero active content delegations.
- Diagnostics loaded 54 traces with a real failure category
  (`structured.query` / `ToolPermissionError`) and reported the operational-log
  status honestly.
- Following a correlation id narrowed the traces to that request.
- No operator payload carried customer content, a document passage, an SQL
  string, a model prompt or a secret.
- In the browser, the operator navigation offered "OpenJM Control Center" and the
  overview rendered live model and release data. The employee and the client
  admin saw no such entry.

## Audit, diagnostics and operational logs are distinct

| Concern | Surface | Persistence |
| --- | --- | --- |
| Audit | `GET /api/platform/audit` over `audit_records` | Persisted, append-only, authoritative governance/security trail |
| Diagnostics | `GET /api/platform/diagnostics` over readiness, `execution_traces` and failure categories | Runtime telemetry, safe metadata only |
| Operational logs | JSON structured stream on process stdout | Not copied into the metadata database |

**No new telemetry persistence was introduced.** OPS1 v1 deliberately does not
dump raw stdout into PostgreSQL to make it searchable, and it does not depend on
a proprietary observability service. The console reports the log integration
status (`aggregated_into_metadata_db: false`) and points the operator at the
audit trail and traces. External aggregation (for example OpenTelemetry/Loki)
remains optional and pluggable.

## Security regressions

- A tenant owner/admin holds no platform capability and is refused by every
  operator route.
- A metadata observer may read safe metadata and may not mutate.
- No operator payload exposes content, SQL, a prompt, a connector credential or a
  secret.
- The audit explorer projects the free-form audit metadata to a bounded
  allow-list, so an unsafe payload is never dumped to an operator screen.
- The diagnostics projection never returns plan SQL, executed SQL, the input
  hash or free-form trace metadata.
- `platform:content:support` alone grants no console content. Reaching customer
  content still requires capability, an explicit bounded delegation, a tenant,
  a scope, a classification ceiling, expiry/revocation and audit.
- A customer tenant identity cannot enumerate the platform fleet.

All 12 mandatory security regressions are covered by
`backend/tests/test_ops1_operator_console.py` (10 tests, 8 of which failed before
the implementation existed) and `frontend/src/OperatorConsole.test.tsx`.

## INF1 / inference operations

The overview surfaces the accepted model identity and provider mode. It does not
display GPU capacity, Rahkia runtime health, admission saturation or cluster
topology, because that data does not exist yet. The console is the extension
point for those after Rahkia qualification, and it does not imply they are active.

## Existing OperationsPanel

The raw scheduler/notification machinery stays where it was, under
`platform:operations:admin`, and is not merged into the control center. A
`platform:metadata:read` observer does not gain operational mutation rights by
reaching the new routes: OPS1 adds no mutation of its own.

## Repository changes

Product code plus a bounded acceptance document and one matrix row. No QA1/QA2/QA3
historical evidence was altered, and no OPS1 implementation was mixed into those
packages.

## Reproducibility

Live tooling lives at `~/openjm-ops1/tools/qa_ops1_live.py` and reuses the
QA1/QA2 helper library. Credentials stay only in the operator secrets store
(`~/openjm-qa1-env/secrets`) and are never committed. The environment was left
running and healthy for the closure review.
