# BV3-C — Control-plane relocation of Connectors and Operations

Status: implemented, frozen for review. No merge without explicit approval.

Base: `feature/bv3-b-client-admin` (frozen BV3-B head `d8b8862`)
Branch: `feature/bv3-c-control-plane-relocation`

## Goal

Complete the backend side of the BV2 navigation cleanup: raw connector
administration and raw scheduler/notification machinery leave the tenant plane and
move behind explicit OpenJM platform authority.

## Why the boundary is where it is

Two different authority axes already existed and this package stops them being
conflated:

- **Platform authority** (`platform:*` capabilities) governs platform machinery.
- **Tenant permissions** (`connector:read`, `schedules:write`, ...) govern what a
  customer's own users may do inside their tenant.

Raw connector administration, sync scheduling and notification-channel
configuration are platform machinery: they carry provider credentials, retry
policy, target payloads and scheduler internals. A tenant `admin`/`owner` no
longer inherits them merely because the legacy permission existed.

## What changed

### New capability — `platform:operations:admin`

Added to `app/core/platform.py`. It is deliberately absent from **every** platform
tier, exactly like `content:support`, so it can only ever be granted explicitly
and never arrives as part of a bundle. It grants no access to customer content
(`capabilities_grant_content_access` is false for it).

### Routes

`app/api/connectors.py` (16 routes) and `app/api/operations.py` (8 routes) now
require `require_platform(PlatformCapability.OPERATIONS_ADMIN)` instead of the
legacy tenant permissions. This is enforced by the backend on every request;
hiding the surface in the UI is not a security boundary.

There is no new bootstrap or fallback path: the capability is granted and revoked
through the existing platform operator plane, which keeps the trust-root guard
added in the #52 correction.

### Migration `0014_operations_admin_capability`

Widens the `platform_operators` capability check constraint. Revision `0008` keeps
an immutable literal so history replays identically, so the widening is its own
additive revision. No row is rewritten and no data is moved: the constraint only
widens what the column may hold. SQLite cannot alter a constraint in place, so
Alembic's batch mode recreates the table and copies rows; PostgreSQL drops and
recreates the constraint.

Downgrade **revokes** rather than deletes any row holding the removed capability,
so a downgrade cannot silently destroy an audit trail.

### What deliberately did not change

- The tenant `CONNECTOR_*`, `SCHEDULES_*` and `NOTIFICATIONS_*` permissions stay in
  the role model. They still gate the governed **action runtime**
  (`app/services/actions/connector_tools.py`) and the scheduler's internal
  authorization, which are the customer-facing outcome controls.
- Connector-backed retrieval is untouched: the retrieval paths reach connectors
  through the service layer via the connector authorization gate, not through
  these routes.
- Scheduled report rerun and notification execution are untouched.
- No pricing, entitlement or payment work.
- Workspace is untouched.

## Acceptance mapping

| Acceptance criterion | Evidence |
| --- | --- |
| Raw Connectors/Operations endpoints reject ordinary tenant roles | `test_tenant_owner_is_rejected_on_raw_connector_routes`, `test_tenant_owner_is_rejected_on_raw_operations_routes`, `test_tenant_admin_role_alone_is_not_enough` |
| An authorized platform operator can administer them | `test_platform_operator_with_operations_admin_is_authorized` |
| The capability is explicit, not implied | `test_operations_admin_is_never_implied_by_a_tier`, `test_metadata_and_tenant_capabilities_do_not_authorize_machinery`, `test_content_support_does_not_authorize_machinery` |
| Existing end-user connector-backed retrieval still works | `test_connector_backed_retrieval_does_not_require_platform_authority` plus the unchanged VS7 connector security suite (retrieval authorization, quarantine, tenancy) |
| No regression to scheduled report/notification execution | `test_scheduled_execution_registry_is_intact` plus the unchanged VS7 scheduler, notification and action-schedule suites |
| Audit privileged changes | `test_raw_scheduler_administration_is_still_audited`; connector and schedule services keep their existing `record_audit` calls |
| Additive migration | `test_migration_0014_widens_the_platform_operator_constraint` |
| exact-head CI green | recorded on the frozen head |

## Not run / limitations

- **Cross-tenant operator targeting.** The relocated routes stay scoped to the
  caller's resolved tenant, so an operator administers the connectors of the
  tenant they are resolved into. Administering another tenant's connectors still
  goes through the BV3-A support-delegation path or a membership. A platform-level
  tenant selector on these routes is deliberately not invented here.
- **The frontend panels.** `ConnectorsPanel.tsx` and `OperationsPanel.tsx` still
  exist but are not reachable from navigation (removed in BV2). Removing the dead
  components is frontend cleanup, not the backend relocation this package owns.
- Customer-facing outcome terminology ("refresh this source nightly") is the
  action-runtime surface that already exists; BV3-C does not add new abstractions.
