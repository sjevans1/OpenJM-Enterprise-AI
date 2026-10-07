# BV1-A: Platform-operator, department/group and data-steward authorization foundation

Baseline: current `main` at merge commit `86af939dc5782496e141cbfe90a4d11465fd4407`
(PR #39, VS8 merged). Authoritative plan: GitHub Issue **#45** ("Hardening &
Business Value Realization"), BV1 / suggested PR sequence `BV1-A`. Subordinate:
#40–#44.

This document is the reconciliation of #45 against current `main` and the
bounded execution plan for the first reviewable increment, **BV1-A**.

## 1. Reconciliation against VS5–VS8 (reuse, do not rebuild)

The identity, tenancy, permission, Knowledge/RAG, structured-data, Hybrid,
Reports, Connectors and Operations subsystems already provide the primitives
BV1 must build on. BV1-A reuses all of them.

| Area | Accepted primitive (current `main`) | BV1-A use |
| --- | --- | --- |
| Principal | `app/core/identity.py` `Principal` (frozen): `principal_id`, `tenant_id`, `role`, `membership_id`, `auth_method`, `permissions`, derived `user_id` | extended additively with group/department/steward/platform context |
| Permissions | `app/core/permissions.py` `Permission` enum + `permissions_for_role`; unknown role → empty set | unchanged; platform capability is a **separate** axis |
| Tenancy | `app/core/tenancy.py` `LEGACY_TENANT_ID`; tenant-qualified `user_id` | reuse |
| Ownership scoping | `app/core/scoping.py` `owned_by` / `tenant_scoped` | reuse for every new tenant-owned query |
| Identity resolution | `app/services/identity.py` `authenticate`, `principal_for_account`, `_principal_for`, `get_or_create_principal`, `add_membership`, `revoke_membership`, `set_membership_role`, `record_audit` | reuse; `_principal_for` additionally resolves access context |
| Route guards | `app/api/deps.py` `get_principal`, `require(*permissions)`, `optional_principal` | add the parallel `require_platform(*capabilities)` guard |
| Migrations | `app/migrations_runner.py` `adopt_and_upgrade` (additive, idempotent per step); `app/migrations_util.py` `has_table`/`has_column`/`has_index`; revisions `0001`–`0007` | add revision `0008` (additive, rollback-aware) |
| Audit | `AuditRecord` + `record_audit` (tenant-scoped, indexed) | every BV1-A mutation audited |
| Tests | `tests/conftest.py` `file_db`/`client`/`session`, `ensure_local_identity`; `tests/oidc_testkit.py` `TestIdP`; `test_vs5_identity.py` / `test_vs5_tenant_isolation.py` | mirror patterns |

No accepted system is rebuilt. No Workspace code is touched (#9 boundary).

## 2. Boundary (the governing rule for all of BV1)

- **RBAC / capabilities** decide *what a principal may do* (Chat, Knowledge,
  Data, Reports, connectors, actions, platform machinery).
- **Source-level classification / group policy** decides *which governed
  information a principal may access*.

Unauthorized evidence must be filtered **before** it reaches RAG, SQL/Hybrid
execution, report snapshots/exports, artifacts, or the model. BV1-A delivers
the *authorization foundation* for that; the pre-retrieval enforcement itself is
BV1-B (Knowledge) and BV1-C (Data/Hybrid/Reports). BV1-A therefore does **not**
claim retrieval filtering yet, and adds no classification fields to sources.

## 3. BV1-A scope (this PR)

Deliver, with RED→GREEN tests first:

1. **Explicit platform-operator authorization**, separate from tenant
   `viewer/editor/admin/owner`. Platform capability is resolved only from
   platform grants, never from a tenant role. Metadata (control-plane) access
   and customer-**content** support access are distinct capabilities, so
   platform metadata administration never implies customer-content access.
2. **Departments and groups** (tenant-scoped), with group membership by
   principal id (never by display name or email string).
3. **Delegated data-steward capability**, scoped to a tenant, department or
   group.
4. **Server-side resolution** of a principal's effective groups, departments,
   steward scopes and platform capabilities, re-read per request (immediate
   revocation, no cache to expire).
5. **Audit** for every classification/ownership/access-policy change (platform
   grant, department/group, membership, steward grant).
6. **Additive, versioned, rollback-aware migration** `0008_bv1_authorization`.
7. **Negative cross-tenant and cross-group tests.**

Explicitly **out of scope for BV1-A** (later increments):
source classification fields and pre-retrieval enforcement (BV1-B/C), report
revalidation propagation (BV1-C), control-plane / client-admin HTTP APIs
(BV3-A/B), UI and navigation (BV2/BV4), Chat artifacts (BV5). No new public
endpoints are added in BV1-A; the capability is exercised at the service layer
and a negative test asserts the admin routes remain absent.

## 4. Phase sequence (from #45)

`BV1-A` (this PR) → `BV1-B` Knowledge classification + pre-retrieval
enforcement → `BV1-C` Data/structured classification + SQL/Hybrid enforcement +
report revalidation → `BV2` journey fixes → `BV3`+ → `BV4` → `BV5` → `BV6`.
Each stops at a merge boundary for human review. No merge without approval.

## 5. Acceptance for BV1-A

- A tenant `owner`/`admin` has **zero** platform capabilities; platform grants
  never derive from a tenant role.
- A principal holding only `platform:metadata:read` (or `...:tenants:admin`,
  `...:operators:admin`) is **denied** customer-content support; only the
  distinct `platform:content:support` capability can grant content support.
- Group/department membership is tenant-scoped: a principal in tenant A can
  never resolve, list, or mutate a group in tenant B; a cross-tenant group
  lookup denies (fail closed).
- Steward grants are scoped and revocation takes effect on the next resolution
  (no cache).
- Every mutation above writes an audit record in the correct tenant.
- Migration `0008` upgrades a real migrated database additively and downgrades
  cleanly; `adopt_and_upgrade` reaches the new head idempotently.
- Accepted VS1–VS8 behaviour remains green.
