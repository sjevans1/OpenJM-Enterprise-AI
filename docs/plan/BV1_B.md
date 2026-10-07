# BV1-B: Knowledge classification and pre-retrieval enforcement

Base: BV1-A corrected head `3d6801c` (PR #47). Package 1 of the #45 temporary
autonomous work package. Stacked branch: `feature/bv1-b-knowledge-classification`.

## Objective

Give governed Knowledge documents a source-level classification and policy, and
enforce it **before retrieval**: an unauthorized document must never become a
retrieval candidate, evidence, citation, report pin, or model context.

## Interpretation (stricter reading, per package rule 6 and the global boundary)

The accepted data model scopes Knowledge documents to the owner
(`Document.user_id`, tenant-qualified) plus connector-authorized documents. The
package's proposed baseline says `internal` is "visible to authenticated tenant
members", which would widen retrieval from owner-scope to tenant-wide and would
also need reconciling with the connector authorization gate.

This increment therefore applies classification as an **additional restriction**
on the existing candidate set, and does **not** introduce tenant-wide sharing of
`internal` documents across owners. This is the stricter interpretation the
package instructs us to choose on conflict, and it preserves the accepted
retrieval scope. Enabling tenant-wide internal sharing is recorded as NOT RUN and
left for explicit human review.

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

- Tenant-wide `internal` sharing across owners (deliberate; needs a product
  decision and connector-gate reconciliation).
- Data / SQL / Hybrid / Reports source-policy propagation (Package 2, BV1-C).
