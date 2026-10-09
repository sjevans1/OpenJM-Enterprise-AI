# Migration Inventory

The application-metadata schema is versioned by Alembic. The revisions live in
`backend/migrations/versions/` and are applied by `app.migrations_runner`
(`adopt_and_upgrade`) at startup, on SQLite (development) and PostgreSQL
(production). This file is a grounded reference for the chain as it stands at
revision `0017_inf1b_admission`: one row per revision, with what it creates or
alters, whether it is reversible, and the notes that matter for a rollback.

Source of truth is the revision files themselves. If a revision is added or
changed, update this table in the same change.

## The chain

The revisions form a single linear chain from `0001_vs4_baseline` (no parent) to
`0017_inf1b_admission`. Every revision declares its `down_revision` as the one
below it, so the head walks back unambiguously. Revision ids are stable and must
never be renamed or renumbered: `alembic_version.version_num` stores the id, and
renaming one orphans every database that recorded it.

Timestamps below are the `Create Date` in each file.

## Revisions

| id | revision | down_revision | objects | reversible | notes |
|----|----------|---------------|---------|------------|-------|
| 0001 | `0001_vs4_baseline` | (none) | Creates the VS1-VS4 baseline tables: `conversations`, `messages`, `documents`, `data_sources`, `execution_traces`, `saved_reports`, `report_definition_versions`, `report_runs` | yes (drop_all) | Definitions live in `app.migrations_schema.baseline_metadata` so the adoption path can create a single missing baseline table without replaying the revision. Moves no data. |
| 0002 | `0002_vs5_identity` | `0001_vs4_baseline` | Creates `tenants`, `principal_accounts`, `tenant_memberships`, `auth_sessions`, `audit_records`; adds `tenant_id` (+ index) to the 7 tenant-owned VS1-VS4 tables; seeds the local tenant `tnt-local` | yes | Additive backfill: `tenant_id` has `server_default` `tnt-local`, the previous `user_id` owner is untouched. Idempotent (skips existing columns/tables). |
| 0003 | `0003_document_lifecycle` | `0002_vs5_identity` | Adds `documents` columns `lifecycle_state`, `lifecycle_version`, `ingest_token`, `indexed_at`, `deleted_at`; index `ix_documents_lifecycle_state` | yes | Classifies existing rows deterministically (`indexed` true -> `ready`; `status='failed'` -> `failed`; otherwise `indexing`). Legacy `status`/`indexed` kept in sync. |
| 0004 | `0004_action_runtime` | `0003_document_lifecycle` | Creates `action_plans`, `action_approvals`, `action_executions` + indexes | yes (drop tables) | VS6 bounded action runtime. Guarded by `has_table`. No existing table altered. |
| 0005 | `0005_document_leases` | `0004_action_runtime` | Creates `document_leases` + index `ix_document_leases_expires_at` | yes (drop table) | Cross-process compare-and-swap lease, one row per document. |
| 0006 | `0006_guard_triggers` | `0005_document_leases` | SQLite triggers `trg_report_runs_protect_identity`, `trg_report_runs_terminal_immutable`, `trg_documents_no_resurrection`; PostgreSQL equivalents as functions + triggers | yes | Per-dialect. Defense-in-depth behind the application-level compare-and-swap. Downgrade drops the triggers and, on PostgreSQL, the functions. |
| 0007 | `0007_vs7_connectors` | `0006_guard_triggers` | Creates `connector_instances`, `connector_credentials`, `external_resources`, `connector_cursors`, `connector_sync_runs`, `workspace_user_mappings`, `schedules`, `schedule_runs`, `notification_channels`, `notifications` + indexes | yes (drop tables) | VS7 governed connectors and workflows. Ten tenant-owned tables, no existing table altered. |
| 0008 | `0008_bv1_authorization` | `0007_vs7_connectors` | Creates `departments`, `access_groups`, `group_memberships`, `platform_operators`, `data_stewards` + indexes | yes (drop tables) | BV1-A. `platform_operators.capability` carries the check constraint `ck_platform_operator_capability` with an immutable literal vocabulary. |
| 0009 | `0009_bv1b_classification` | `0008_bv1_authorization` | Adds `documents` columns `classification`, `department_id`, `tenant_visible`, `allowed_group_ids_json`; index `ix_documents_department_id` | yes | `classification` defaults to `internal`. Downgrade uses batch mode to drop the columns on SQLite. |
| 0010 | `0010_bv1c_data_classification` | `0009_bv1b_classification` | Adds the same four policy columns to `data_sources`; index `ix_data_sources_department_id` | yes | Mirrors 0009 for structured sources. |
| 0011 | `0011_usage_metering` | `0010_bv1c_data_classification` | Creates `model_usage_events` + indexes; SQLite trigger `trg_model_usage_events_immutable` | yes (drop trigger + table) | Append-only LLM usage ledger (M1). The trigger blocks UPDATE on SQLite; application is append-only on every dialect. |
| 0012 | `0012_support_delegations` | `0011_usage_metering` | Creates `support_delegations` + indexes | yes (drop table) | BV3-A explicit tenant-scoped support delegations. |
| 0013 | `0013_tenant_preferences` | `0012_support_delegations` | Adds `tenants.settings_json` (`server_default` `'{}'`) | yes (drop column) | BV3-B. No existing column, constraint or row altered. |
| 0014 | `0014_operations_admin_capability` | `0013_tenant_preferences` | Rewrites `ck_platform_operator_capability` to add `platform:operations:admin` | yes, with a data caveat | BV3-C. Constraint is `status='revoked' OR capability IN (...)`. Downgrade revokes rows still holding `platform:operations:admin` (sets status/revoked_at) before narrowing, so history survives but those grants are not re-activated by a later re-upgrade. |
| 0015 | `0015_inf1_inference_registry` | `0014_operations_admin_capability` | Creates `inference_model_releases`, `inference_runtime_profiles`, `inference_deployments`, `inference_tenant_bindings`, `inference_health_observations`, `inference_routing_decisions`, `inference_usage_attributions` + indexes; widens the operator capability vocabulary with `platform:inference:admin` | yes, drops tables | INF1-A. The attribution sidecar references `model_usage_events` one-to-one and never modifies it. Downgrade revokes `platform:inference:admin` rows then drops the seven tables. Where registry evidence must be preserved, revert code and leave the tables in place. |
| 0016 | `0016_m3_entitlements` | `0015_inf1_inference_registry` | Creates `entitlement_plans`, `entitlement_plan_versions`, `entitlement_price_schedules`, `entitlement_subscriptions`, `entitlement_allowances`, `entitlement_billing_periods`, `credit_ledger_entries`, `usage_reservations` + indexes; SQLite triggers `trg_credit_ledger_entries_immutable`, `trg_credit_ledger_entries_no_delete` | yes, drops tables | M3 commercial foundation. Idempotently adds `usage_reservations.execution_state`/`dispatched_at` and widens its check for a database that ran the earlier form of the revision. Append-only credit ledger. The PostgreSQL immutability trigger is documented in the file, not executed here. Where commercial state must be preserved, revert code and leave the tables. |
| 0017 | `0017_inf1b_admission` | `0016_m3_entitlements` | Creates `capacity_pools`, `capacity_scopes`, `capacity_leases`, `admission_tickets` + indexes | yes, drops tables | INF1-B admission and bounded queue. Pure additive: no existing table altered, no column added elsewhere. Where capacity evidence must be preserved, revert code and leave the tables. |

## Reversibility policy

- Every revision implements `downgrade()`. There is no revision without one.
- Structural changes (add/drop tables, columns, indexes, triggers) reverse
  cleanly. The downgrade of 0009/0010/0014 uses Alembic batch mode on SQLite,
  which recreates the table and copies every row; no row value is changed.
- Two downgrades have a deliberate data effect and are not fully reversible at
  the row level: 0014 and 0015 revoke (never delete) operator grants that hold a
  capability the downgrade retires. A re-upgrade re-widens the vocabulary but
  does not silently re-activate a revoked grant.
- The newest revisions (0015, 0016, 0017) drop their own tables on downgrade.
  That is intended for a deployment that has not yet used them; where that state
  must be preserved, the documented path is to revert executable code and leave
  the tables in place.

## No destructive conversion

No revision converts, truncates or rewrites existing application rows as a side
effect of a schema change. The only row updates a revision performs are the
deterministic classifications in 0003 (documents) and the operator revocations
in 0014/0015, both of which are additive in meaning and covered by the test
suite. The upgrade tests build a populated database at an older revision,
upgrade to head, and assert that row counts and a stored-value fingerprint are
unchanged.

## Test coverage

- `backend/tests/test_migrations.py`: builds a database at a representative old
  revision (baseline, `0006`, `0007`, `0014`) with real rows, upgrades to head,
  asserts no row is lost or rewritten, and exercises downgrade/re-upgrade round
  trips.
- `backend/tests/test_migrations_postgres.py`: the populated-schema upgrade and
  round trip on a real PostgreSQL server (`pgserver`). Skips with a reason when
  PostgreSQL is unavailable; never reported as verified when it did not run.
- Head-agnostic by design: the migration tests assert the applied revision is in
  the `down_revision` chain walked from the script head, not equal to a pinned
  literal. Assertions that pin a *targeted* revision in a downgrade-to-X or
  re-upgrade test are intentional and stay.

Focused command:

```
cd backend
PYTHONPATH=$PWD python -m pytest -q tests/test_migrations.py \
  tests/test_migrations_postgres.py tests/test_inf1b_admission.py \
  tests/test_postgres_metadata.py
```
