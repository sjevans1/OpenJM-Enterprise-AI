# QA2: Persona Journeys + Delegated-Support/Security Validation

Status: **ACCEPTED with two open product findings**. Live persona and
security-sensitive journeys executed against the QA1 production-like acceptance
environment using real OIDC sessions and a real Chrome browser.

## 1. Tested identity and environment

| Field | Value |
| --- | --- |
| Repository | `sjevans1/OpenJM-Enterprise-AI` |
| Tested Git SHA | `dbc5411943d3853666562cfbdd3402addc528ba1` (current `main`, exact required baseline) |
| Product | `0.2.0` / `RELEASE_TRAIN REL1` |
| Backend | REL1 production wheel install, production profile, PostgreSQL 16.15 |
| Metadata DB | PostgreSQL, migrations at `0021_support_content_scope` |
| OIDC | Keycloak 26.7.4, realm `openjm`, authorization code + PKCE `S256` |
| Frontend | production build served by Caddy at `http://127.0.0.1:15173` |
| Browser | real Chrome 154 headless over CDP (Windows), driven from WSL |
| Model gateway | local OpenAI-compatible (`local`, fallback `none`) |
| Vector/RAG | DB-GPT + Chroma, `all-MiniLM-L6-v2` |

The running product tree is identical to `main`; PR #87 (QA1 scaffolding) is the
only delta from the previously qualified head, and it changes no product file.

Authenticated sessions are real Keycloak authorization-code + PKCE logins
exchanged through `/api/auth/oidc/callback`. The resulting OpenJM session token
is installed into the SPA's `sessionStorage` so the real browser renders the
authenticated application; the Keycloak login form is never credential-filled by
the automation.

## 2. Results summary

86 checks executed: **81 PASS, 4 FAIL, 1 NOT RUN, 0 BLOCKED**.

| Area | Checks | Result |
| --- | --- | --- |
| QA2-A end user | A-01..A-16 | login, navigation boundary, authorized/restricted Knowledge, authorized/restricted Data, Hybrid, report flow |
| QA2-B data steward | B-01..B-13 | scoped governance, policy change + audit, structured policy, report naming + curation, out-of-scope negatives |
| QA2-C client admin | C-01..C-15 | administration UI, dept/group create, grant/revoke propagation, usage scoping, cross-tenant |
| QA2-D platform operator | D-01..D-06 | control plane, metadata, audited privileged mutation, no implicit content |
| QA2-E delegated support | E-01..E-11 | grant, consumption, ceiling, audit, revocation, expiry, cleanup |
| QA2-F cross-tenant | F-01..F-17 | Tenant A ↔ Tenant B isolation matrix, non-enumerating failures |
| QA2-G revocation | G-01..G-03 | group, steward, delegated-support next-request revocation |
| QA2-H browser health | H-01..H-99 | console/network health for all five personas |

### Persona outcomes

**End user (`qa.employee`).** OIDC login PASS. Navigation is Chat + Reports only
(Knowledge/Data surfaces require write/steward/admin by design; no Administration
and no platform controls). Authorized Knowledge retrieval PASS with citation
(`[DOC 1]`) and correct tenant; the restricted HR document is not returned and its
title never appears. Restricted structured data fails closed with an explicit
"safely not answered" message and no schema/value leak. Reports: the ordinary
member cannot create a Saved Report (denied by the role model, `reports:write` is
editor+), which is the correct fail-closed outcome.

**Data steward (`qa.steward`).** Login PASS; navigation exposes Knowledge + Data
but not Administration and no platform control plane. Discovered documents are
exactly those in scope (HR visible, Finance not). Policy change on an in-scope
Knowledge source PASS and audited (`governance.document.policy`). In-scope
structured source policy PASS. Report naming PASS; curation request-review on a
report the steward owns returned 404 (see finding 2). Out-of-scope mutations
(Finance document, department-less source) are 403. No tenant-membership
administration and no platform authority.

**Client admin (`qa.clientadmin`).** Login PASS; Administration UI with Users,
Departments, Groups, Data Stewards, Data Access. Created a QA department and
group. Adding the ordinary member to HR Leadership propagated to that member's
**next request** and immediately authorized the restricted HR evidence; removing
it revoked access on the next request. Steward grant and revocation likewise
propagated next-request. Usage and entitlements are tenant-scoped and show the
admin's own tenant only. Cannot enumerate tenants and cannot reach Tenant B
client-admin surfaces.

**Platform operator (`qa.platform`).** Login PASS; capabilities are exactly
`platform:metadata:read`, `platform:tenants:admin`, `platform:operators:admin`
and never `platform:content:support`. Platform metadata (status, identity, model,
tenants) is visible per capability. A safe privileged mutation (create and
suspend a throwaway tenant) succeeded and is audited. **Mandatory negative PASS:**
reading Tenant A governed content and the support content path are both 403
without a delegation. The SPA exposes no platform surface by design; the control
plane is the accepted platform API.

**Delegated support (QA2-E).** Starting state proven (no delegation, no content).
The content-support capability was granted explicitly (never implicit), then a
narrow tenant-A delegation was created (scope `content`, ceiling `internal`).
Content became available **only** through the delegation, with the ceiling
hiding the restricted HR and Finance documents. Grant, capability grant and
consumption are all audited (`platform.support.grant`, `platform.operator.grant`,
`support.content.read`). Revocation failed closed on the very next request;
historical audit survived. A time-bound delegation expired and failed closed.
The capability and all effective delegations were cleaned up.

**Cross-tenant (QA2-F).** Tenant A principals cannot list, query, administer or
mutate Tenant B, and cannot use a Tenant B support delegation; a Tenant B
ordinary member cannot reach Tenant A equivalents. Resource-level probes are
non-enumerating (404).

**Revocation timing (QA2-G).** Group membership, steward grant and delegated
support are all next-request effective, verified through backend authorization
decisions rather than UI disappearance.

**Browser health (QA2-H).** No fatal console errors, no unexpected 4xx/5xx, no
redirect loops, no OIDC callback errors across all five personas and every
navigation surface they can see. The 401/403 responses observed are permission
boundaries (for example the employee's report save).

## 3. Findings

### Finding 1 (FAIL, needs a product decision): Data/Hybrid structured evidence is unreachable for a non-owner

`QA2-A-10`, `QA2-A-12`. Data mode and the Hybrid structured side resolve candidate
sources through `structured_planner.plan` -> `_sources(db, user_id)` with no tenant
scope, which falls back to `DataSource.user_id == user_id` (ownership). An
ordinary tenant member owns no source (creating one needs `data:write`, editor+),
so the request fails closed with "the authorized schema does not support a safe
answer" even though an `internal`, `tenant_visible`, connected source exists for
the tenant. The orchestrator's own `_structured_sources` helper documents
tenant-wide internal availability as the "resolved product decision" and the
Hybrid decomposition uses it, so the execution path is inconsistent with it.

Not corrected here: making an internal source reachable tenant-wide **widens data
authorization**, which needs an explicit product decision (and would change
accepted source-authority semantics). The capability itself works for a source
owner and is proven in QA2-B.

### Finding 2 (FAIL, corrected on the QA2 branch): Saved Reports are bound to the legacy tenant

`QA2-B-10`. The Saved Report create path wrote the tenant-qualified `user_id` but
omitted `tenant_id`, so the row fell back to the model default (`tnt-local`). A
report saved by a principal acting in any other tenant was then invisible to every
tenant-scoped read over `saved_reports`: the BV6 curation surface returned
404 "Saved report not found" for the report's own owner. The offline suite never
caught it because test requests resolve the development principal, which lives in
the legacy tenant where the default coincides with the correct value.

Impact: BV6 curation is unusable for any report created outside the legacy tenant,
and any tenant-scoped report enumeration misses those rows. Isolation itself still
holds (ownership uses the tenant-qualified key).

Correction: write `tenant_id=current_principal().tenant_id` on the row, with a
regression that drives the real endpoint while the request resolves into a
non-legacy tenant (RED: `assert 'tnt-local' == 'tnt-qa2'`; GREEN after the fix).

### Finding 3 (FAIL, corrected on the QA2 branch): Save-as-report is offered to a role that cannot use it

`QA2-A-16`. The Chat "Save as report" affordance and its naming modal are shown to
every principal, but the server requires `reports:write` (editor+). An ordinary
viewer therefore opens the modal and every save fails closed with
403 "lacks required permission 'reports:write'". Presentation only; the
authorization boundary is correct.

Correction: gate the affordance on `reports:write`, matching the existing
permission-driven navigation, with a RED->GREEN regression for both a viewer and a
principal holding the capability.

### Not run

`QA2-F-10` (open a Tenant B saved report by id). Tenant B has no governed content,
so no evidence-backed Tenant B report can be created; the resource-level
non-enumerating proof is carried by `QA2-F-11` (Tenant B artifact, 404) instead.

## 4. Repository changes

One bounded QA2 PR on `qa2/acceptance`:

* the two product corrections above, each with a focused regression test;
* QA2 environment scaffolding: a tenant-visible `internal` structured fixture
  (`deploy/qa/sql/qa_ops.sql`), a Tenant B ordinary member in the realm template,
  and a seeder update that provisions both structured fixtures.

No role semantics, authorization predicate, lock, migration or CI change.

## 5. Environment disposition

The environment is left in a reusable state for QA3: backend `/api/ready` and
`/api/health` green, frontend reachable, Keycloak healthy. The QA content-support
capability and all effective support delegations were revoked; the only remaining
delegation row is expired and inert. Intentionally retained QA mutations:
department `qa2-temp`, group `qa2-temp-group`, a suspended probe tenant, the QA2
probe documents, and the tenant-visible structured source.
