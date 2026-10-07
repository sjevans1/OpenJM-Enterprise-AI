# BV3-B — Client Administration APIs + Admin UX

Status: implemented, frozen for review. No merge without explicit approval.

Base: `feature/bv3-a-platform-control-plane` (frozen BV3-A head `d3daf28`)
Branch: `feature/bv3-b-client-admin`

## Goal

Give each client a safe, business-facing administration plane built on the
governance foundation already on main, and make the workspace navigation reflect
the caller's permissions while the backend stays the authority.

## Boundaries

- **Tenant permission model, never a platform capability.** Every route in
  `app/api/admin.py` requires `tenant:admin` (tenant `admin` or `owner`). An
  OpenJM platform operator gains nothing here without a tenant membership.
- **No privilege escalation.** Granting `owner` requires the caller to be an
  owner. A principal cannot change their own role, revoke their own membership or
  end their own sessions. The last active owner cannot be demoted or revoked.
- **Stewards are not admins.** A data steward keeps their ordinary role and
  manages classification only inside the department/group they were delegated.
- **No cross-tenant enumeration.** Every query is scoped to the caller's tenant
  by construction.
- **No raw infrastructure concepts in the UI.** No operation ids, scheduler
  internals, provider credentials or raw platform capabilities are rendered.
- **Workspace untouched; the Chat/Knowledge/Data/Hybrid selector is untouched.**

## Backend

### `app/services/client_admin.py`

Membership lifecycle: `list_members`, `provision_member` (idempotent per
subject), `change_role`, `set_membership_status` (revoke/reactivate),
`revoke_member_sessions`. Plus `read_preferences` / `update_preferences` against
a **whitelist** (`default_report_timezone`, `notify_on_run_failure`,
`support_contact_email`) so the surface can never become an arbitrary
configuration back door, and `usage_summary`, which returns M1 tenant totals with
an explicit plan/entitlement placeholder until M2/M3 land.

Departments, groups, group membership, steward grants and source/document policy
are **reused** from `access_governance` (BV1-A/BV1-C) rather than rebuilt.

### `app/api/admin.py` (prefix `/api/admin`)

Users: `GET|POST /members`, `PATCH /members/{id}`, `POST /members/{id}/revoke`,
`POST /members/{id}/reactivate`, `POST /members/{id}/sessions/revoke`.
Departments: `GET|POST /departments`, `PATCH /departments/{id}/status`.
Groups: `GET|POST /groups`, `PATCH /groups/{id}/status`,
`GET|POST /groups/{id}/members`, `DELETE /groups/{id}/members/{principal_id}`.
Stewards: `GET|POST /stewards`, `DELETE /stewards`.
Plus `GET /data-access`, `GET|PATCH /preferences`, `GET /usage`, `GET /audit`.

### Migration `0013_tenant_preferences`

Additive `tenants.settings_json` (default `'{}'`), following
`0012_support_delegations`. No existing column, constraint or row is altered.

## Admin UX

`frontend/src/AdminPanel.tsx` with business labels only: **Users**,
**Departments**, **Groups**, **Data Stewards**, **Data Access**, **Usage / Plan**.
`App.tsx` gains a permission-gated **Administration** navigation entry backed by
the exported `canAdminister()` helper (`tenant:admin` in the principal's
permissions, role fallback). The panel is mounted on demand; no admin endpoint is
called until the surface is opened.

## Acceptance mapping

| Acceptance criterion | Evidence |
| --- | --- |
| HR admin can create HR department/group and assign members | `test_hr_admin_creates_department_group_and_assigns_members`, `a client administrator can create a department from the surface` |
| HR steward manages governed HR sources within scope without managing tenant membership | `test_steward_manages_scoped_source_without_tenant_admin` |
| viewer/editor cannot mutate tenant governance | `test_viewer_and_editor_cannot_mutate_tenant_governance`, `test_platform_capability_does_not_grant_client_admin` |
| Membership/group/steward revocation takes effect on the next request | `test_revocation_takes_effect_on_next_request`, `test_group_revocation_takes_effect_on_next_request` |
| UI navigation reflects permissions; backend remains authoritative | `administration navigation reflects tenant permissions`, `the workspace exposes Administration on demand...` |
| Privilege escalation and self-mutation are refused | `test_self_mutation_and_owner_escalation_are_refused`, `test_last_owner_cannot_be_demoted_or_revoked` |
| No cross-tenant enumeration | `test_no_cross_tenant_enumeration` |
| Safe preferences, not a config back door | `test_preferences_whitelist_and_persistence` |
| Additive migration | `test_migration_0013_adds_tenant_preferences` |
| exact-head frontend/backend CI green | recorded on the frozen head |

## Not run in this package

- Entitlements/plan data behind the Usage / Plan surface — placeholder only until
  M2/M3.
- Relocation of raw Connectors/Operations endpoints — BV3-C.
