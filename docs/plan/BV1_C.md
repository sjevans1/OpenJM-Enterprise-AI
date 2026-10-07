# BV1-C: Data / SQL / Hybrid / Reports source-policy propagation

Base: BV1-B head `eb41ddc` (PR #48). Package 2 of the #45 temporary autonomous
work package. Stacked branch: `feature/bv1-c-data-policy`.

## Objective

Apply the same source-level classification/policy to structured Data sources and
to every downstream evidence surface, so an unauthorized source never becomes
discoverable, is never exposed to the schema/SQL planner, and a report whose
evidence is no longer authorized fails closed.

## Delivered

- `DataSource` policy columns (`classification` default `internal`,
  `department_id`, `tenant_visible`, `allowed_group_ids_json`), additive
  migration `0010_bv1c_data_classification` (idempotent, rollback-aware).
- Shared fail-closed predicate `data_source_is_visible` /
  `visible_data_sources` in `app/services/document_policy.py`, reusing the same
  rules as Knowledge (unknown class fails closed to `highly_restricted`).
- `orchestrator._structured_sources` removes unauthorized sources **before** the
  planner sees them, i.e. before any schema or SQL exposure. A policy-hidden
  pinned source fails the exact report-scope check (fail closed), never silently
  dropped.
- Report revalidation: `reports._sources_available` now re-checks current source
  policy for BOTH cited documents and cited Data sources, so open / list / export
  fail closed after a revocation.
- Steward/admin mutation `set_data_source_policy` + `PATCH /api/data/sources/{id}/policy`,
  scoped (tenant-admin or covering steward) and audited
  (`governance.data_source.policy`); a foreign-tenant source is never revealed or
  mutated.

## Interpretation (resolved product decision)

Same as BV1-B (see `docs/plan/BV1_B.md`): a native governed Data source
classified public/internal is available to all active members of the tenant
(capability permitting); confidential/highly-restricted need explicit
authorization. Connector/provider authorization remains an intersecting gate
wherever applicable. Report open/list/export revalidates the same current
policy, and a source that loses authorization fails closed.

## Tests

`backend/tests/test_bv1c_data_classification.py` (11): predicate (default
internal, highly-restricted explicit grant, cross-tenant, department/steward),
pre-planner removal through the orchestrator, report document + source
revalidation after revocation, mutation authorization + audit, foreign-tenant
safety, and the 0010 migration.

## NOT RUN

- Table/object-level (narrower than source) policy: the architecture already
  scopes through `authorized_objects_json`, but per-object classification labels
  are not added in this increment. The rule "a broader table permission must
  never override a stricter source permission" holds because the source-level
  policy is evaluated before any object list is exposed.
- Dependent-Hybrid grounded-parameter documents are covered transitively by the
  BV1-B Knowledge filter (the parameter is extracted only from retrieved,
  authorized Knowledge evidence).
- Tenant-wide `internal` sharing: implemented in this stack (see the resolved
  decision). No longer NOT RUN.
