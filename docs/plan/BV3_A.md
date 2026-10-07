# BV3-A — OpenJM Platform Control Plane APIs + Secure Bootstrap

Status: implemented, frozen for review. No merge without explicit approval.

Base: `main@8626f422a663ce238157a4023df23b9a5ed43c96`
Branch: `feature/bv3-a-platform-control-plane`

## Goal

Give OpenJM an explicit platform control plane for provisioning and operating
tenants, built on the authority, tenancy, governance, audit and identity
primitives already accepted in BV1-A, BV1-B, BV1-C and M1. Nothing in this
package grants a tenant role any platform authority, and nothing in it returns
customer content.

## Boundaries held

1. **Platform authority is separate from tenant admin/owner.** The control plane
   is guarded by explicit `platform:*` capabilities. A tenant `owner` or `admin`
   receives `403` on every route in this package (`test_tenant_owner_has_zero_platform_rights`).
2. **Control-plane metadata access never implies customer-content access.**
   `platform:metadata:read` reads tenant metadata only. No route under
   `/api/platform` returns documents, sources, reports, chat or evidence, and the
   content predicate `capabilities_grant_content_access()` returns `False` for
   every capability except `platform:content:support`.
3. **Customer-content support stays an explicit capability.** `platform:content:support`
   is never implied, never bootstrapped, and can only be delegated by an operator
   who already holds it (see support delegations below).
4. **No production dev-auth fallback and no permanent bootstrap password.**
   Bootstrap is a host-side CLI command with no HTTP endpoint and no stored
   credential. Local development keeps using the accepted `auth_mode='dev'`
   identity path, which is separate and unchanged.
5. **Raw Connectors and Operations belong to the control plane.** BV3-A adds the
   platform authority axis they will move behind; the endpoint relocation itself
   is BV3-C (recorded as NOT RUN below so the boundary is not claimed early).
6. **Workspace is untouched**; the Chat/Knowledge/Data/Hybrid selector is untouched.

## What was added

### Platform control-plane API — `app/api/platform.py`

| Route | Capability |
| --- | --- |
| `GET /api/platform/status` | `metadata:read` |
| `GET /api/platform/identity` | `metadata:read` |
| `GET /api/platform/model` | `metadata:read` |
| `GET /api/platform/tenants` | `metadata:read` |
| `GET /api/platform/tenants/{id}` | `metadata:read` |
| `POST /api/platform/tenants` | `tenants:admin` |
| `POST /api/platform/tenants/{id}/suspend` | `tenants:admin` |
| `POST /api/platform/tenants/{id}/reactivate` | `tenants:admin` |
| `POST /api/platform/tenants/{id}/memberships` | `tenants:admin` |
| `GET /api/platform/operators` | `operators:admin` |
| `POST /api/platform/operators` | `operators:admin` |
| `DELETE /api/platform/operators/{principal_id}` | `operators:admin` |
| `GET /api/platform/tenants/{id}/support` | `metadata:read` |
| `POST /api/platform/tenants/{id}/support` | `operators:admin` |
| `DELETE /api/platform/tenants/{id}/support/{principal_id}` | `operators:admin` |

Identity and model status report configuration *state* only: booleans for issuer,
client id, base URL and credential presence. No secret value, endpoint URL or
credential is returned.

### Platform administration service — `app/services/platform_admin.py`

Tenant create is idempotent by slug so a retried provisioning call cannot create a
duplicate tenant. Suspend/reactivate write `Tenant.status`; suspension takes effect
on the tenant's next request because the accepted identity layer already refuses a
non-active tenant (`tenant_inactive`), so no session or cache needs to expire.
Initial owner/admin provisioning is idempotent per subject and audited. Every
privileged mutation writes an `AuditRecord` under `platform.tenant.*`.

### Secure bootstrap — `app/services/platform_bootstrap.py`

```
python -m app.services.platform_bootstrap <validated-oidc-subject>
```

- No HTTP route exists (`test_bootstrap_has_no_http_endpoint`).
- Refuses when any active operator already holds `operators:admin`, so a second
  run cannot be replayed into additional privilege (`test_bootstrap_establishes_first_operator_and_cannot_be_replayed`).
- Never grants `content:support`, even when asked explicitly
  (`test_bootstrap_never_grants_content_support`).
- Creates the principal account for the subject and audits `platform.bootstrap`.
  The account carries no credential of its own; afterwards the operator
  authenticates through normal OIDC or a session.

### Support and delegation foundation — migration `0012_support_delegations`

One `support_delegations` row per `(tenant, operator, scope)`; `scope` is
`metadata` or `content`, rows are revocable and optionally time-bounded. An
operator can only delegate an authority they hold: `operators:admin` is required
to delegate, and `content` support additionally requires the actor to hold
`content:support`. Resolution is per request, filters revoked and expired rows,
and surfaces on `Principal.support_scopes` for later phases to consume.
Mutations are audited (`platform.support.grant` / `platform.support.revoke`).

## Acceptance mapping

| Acceptance criterion | Evidence |
| --- | --- |
| Tenant owner has zero platform-admin rights | `test_tenant_owner_has_zero_platform_rights` |
| Metadata operator cannot read customer content | `test_metadata_operator_cannot_read_customer_content`, `test_content_support_capability_is_separate` |
| Tenant create/suspend/reactivate only via platform authority | `test_tenant_create_suspend_reactivate_via_platform_authority`, `test_tenant_mutation_requires_tenants_admin` |
| Initial owner provisioning works and is audited | `test_initial_owner_provisioning_is_audited`, `test_provisioning_rejects_tenant_roles` |
| Bootstrap cannot be replayed | `test_bootstrap_establishes_first_operator_and_cannot_be_replayed` |
| Operator grant/revoke applies on the next authorized request | `test_operator_grant_and_revoke_apply_on_next_request` |
| Support delegation is scoped, revocable and expiring | `test_support_delegation_is_scoped_resolved_and_revocable`, `test_expired_support_delegation_is_not_resolved`, `test_content_support_cannot_be_delegated_without_holding_it` |
| Cross-tenant metadata without content | `test_platform_metadata_lists_all_tenants_without_content` |
| Additive migration | `test_migration_0012_adds_support_delegations` |

## Not run in this package

- **Relocation of the raw Connectors/Operations endpoints** to the control plane.
  The platform authority axis they will sit behind lands here; moving and
  locking the legacy tenant-facing routes is BV3-C. BV3-A does not claim that
  acceptance line.
- **A customer-content support request path.** This package lays the delegation
  record and resolution; the capability-checked read path that consumes
  `support_scopes` with an active, unexpired delegation is later BV3-C/BV4 work.
- **Commercial entitlements, pricing, invoices, payments.** Out of scope for the
  whole BV3-A package.

## Rollback

Revert the branch. The migration is additive and reversible; no existing table,
row or endpoint was modified.
