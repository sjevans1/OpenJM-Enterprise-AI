# BV1-B: Knowledge classification and pre-retrieval enforcement

Base: BV1-A corrected head `3d6801c` (PR #47). Package 1 of the #45 temporary
autonomous work package. Stacked branch: `feature/bv1-b-knowledge-classification`.

## Objective

Give governed Knowledge documents a source-level classification and policy, and
enforce it **before retrieval**: an unauthorized document must never become a
retrieval candidate, evidence, citation, report pin, or model context.

## Interpretation (resolved product decision)

Superseded the earlier stricter reading. Per the human product decision on
Issue #45 and PR #48:

- a native OpenJM source classified `public` or `internal` with
  `tenant_visible=true` is available to **all active members of the tenant**,
  subject to normal capability permissions. Source ownership is a
  curation/management boundary, not the ordinary consumption boundary;
- `confidential` requires explicit authorized groups/departments/steward/admin
  policy; `highly_restricted` requires explicit narrow authorization with no
  tenant-wide fallback;
- tenant isolation and RBAC capability permission remain mandatory;
- **connector-origin content is an intersecting gate**: it must satisfy the
  current connector authorization gate AND the classification/group policy.
  Classification never widens a connector's provider-side authorization.

Enforcement selects the tenant-wide candidate set
(`document_policy.governed_tenant_documents`) and removes connector-origin
documents that the connector gate does not currently authorize.

## Classification model

Controlled initial vocabulary (no custom labels in this increment):

- `public`, `internal`: may rely on the tenant-wide fallback, but only when the
  document is explicitly `tenant_visible`.
- `confidential`, `highly_restricted`: explicit grant only (allowed group,
  owning department membership, department steward, or tenant-wide steward).

New document columns: `classification` (default `internal`), `department_id`,
`tenant_visible` (default true), `allowed_group_ids_json`. An unknown or missing
classification resolves to `highly_restricted` (fail closed).

The safe default `internal` matches accepted pre-BV1 behavior, so existing rows
do not change meaning.

## Enforcement points (all pre-retrieval)

- `orchestrator._ready_documents`: the candidate set is filtered before it is
  used, so a hidden document is never passed to vector retrieval.
- `KnowledgeSearchTool`: the candidate set is filtered before
  `knowledge_engine.retrieve`; a hidden pinned document also fails the exact
  report-scope check, so both paths close.
- Neighbor / exact-chunk expansion is per-document collection, so a document
  never passed as a ref cannot be expanded into; equivalent-source provenance is
  already validated against the authorized set.
- `GET /api/knowledge/documents`: the catalog listing applies the same filter, so
  a hidden document's existence is not leaked.

Authorization is applied on every request from current rows; there is no
authorization cache in this increment.

## Steward mutations

`PATCH /api/knowledge/documents/{id}/policy` (permission `knowledge:write`, then a
tenant-admin-or-covering-steward scope check in the service). Every change is
audited (`governance.document.policy`). A foreign-tenant document is neither
revealed nor mutated. Unknown classifications, unknown/foreign departments and
foreign allowed groups are rejected.

## Migration

`0009_bv1b_classification`: additive, idempotent per step,
rollback-aware. Existing rows receive `internal` / `tenant_visible=true` (no
access change). SQLite cannot ALTER-ADD a constraint column for the department,
so `department_id` is a plain column validated in the application.

## Tests

`backend/tests/test_bv1b_knowledge_classification.py`: predicate semantics
(default internal, unknown fails closed, tenant-wide, highly-restricted explicit
grant, group grant/revocation, department/steward visibility, cross-tenant),
pre-retrieval removal through the governed tool, neighbor/evidence leak
prevention, group revocation and department archiving removing access on the next
resolution, catalog exclusion, steward/admin mutation authorization + audit,
foreign-tenant safety, and the 0009 migration.

## NOT RUN

- Data / SQL / Hybrid / Reports source-policy propagation (Package 2, BV1-C).
- Per-object/table classification labels.
