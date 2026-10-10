# Product decisions: `internal` Knowledge visibility and per-table classification

Status: **accepted v1 product decisions.** Decision 1 keeps tenant-wide `public`/`internal` visibility with `tenant_visible=true`; Decision 2 keeps classification at source level for v1 and defers per-object labels until a concrete unsplittable mixed-sensitivity case requires them.

Authority: owner direction accepted during the Issue #45 close-out. The earlier BV6 next-wave plan (PR #69) raised these as SHOULD decisions; the implemented code and accepted BV1 behavior already resolve the first, while the second remains deliberately deferred for v1.

Baseline inspected:

| Item | Value |
| --- | --- |
| Branch | `docs/product-decisions-memo` |
| Accepted baseline | `a047a991cc8882f3f8c0f444387ea190acb689d4` (`main`) |

File references below describe the accepted implementation; line numbers may move as the code evolves.

---

## Decision 1. `internal` Knowledge visibility: tenant-wide or owner-scoped

### What the plan says

The plan frames this as a choice between the current stricter (owner-scoped)
behavior and an option to make `internal` tenant-wide. Its grounding is the
interpretation note at `backend/app/services/document_policy.py:8-16` and the note
in `docs/plan/BV1_B.md` that the stricter reading is NOT RUN.

### What the code actually does (this is the trap in the plan)

The stricter reading is superseded. The tenant-wide behavior is implemented and
tested at the base, and it is the behavior on every HTTP read path:

- `backend/app/core/governance.py:62-65` defines
  `TENANT_WIDE_CLASSIFICATIONS = {public, internal}`.
- `backend/app/services/document_policy.py:94-97` returns visible when
  `classification in TENANT_WIDE_CLASSIFICATIONS and tenant_visible`, with no
  owner check. `document_is_visible` (`document_policy.py:112-121`) and
  `data_source_is_visible` (`document_policy.py:124-133`) both delegate here.
- `backend/app/services/document_policy.py:170-217` `governed_tenant_documents`
  selects every retrievable document in the tenant, applies `document_is_visible`,
  and intersects connector-origin content with the connector gate. Its docstring
  states the resolved decision in plain terms: a native `public`/`internal` source
  with `tenant_visible` is available to every active member of the tenant, not only
  its uploader.
- Callers on the request path: `backend/app/services/orchestrator.py:201-202`
  (`_ready_documents`), `backend/app/services/tools.py:268-273`
  (`KnowledgeSearchTool`), and `backend/app/api/knowledge.py:79`
  (`GET /api/knowledge/documents` catalog listing). The owner/connector-scoped
  query remains only on the no-identity service path (`orchestrator.py:206-241`),
  which the HTTP path never takes.
- Defaults make this the out-of-the-box posture: `DEFAULT_SOURCE_CLASSIFICATION`
  is `internal` (`governance.py:58`) and `Document.tenant_visible` defaults true
  (`backend/app/models.py:150-152`). A newly ingested document is therefore
  tenant-wide visible unless a steward changes its classification or clears
  `tenant_visible`.
- The tests assert the tenant-wide behavior, not the stricter one:
  `backend/tests/test_bv1_tenant_wide.py` (`test_non_owner_member_retrieves_internal_document`,
  lines 80-89) proves an ordinary member retrieves a document they did not upload.

The stricter reading survives only as prose in `document_policy.py:8-16` and
`docs/plan/BV1_B.md`'s NOT RUN section. `docs/plan/BV1_B.md:12-31` records the
tenant-wide decision as resolved and superseding the stricter reading, and
`docs/plan/BV1_C.md:60-61` states tenant-wide `internal` sharing is "No longer NOT
RUN". The BV6 plan's SHOULD row is stale: it reads the stale docstring and
concludes `internal` is not tenant-wide, which contradicts the code and the two
BV1 plan documents.

### Options

- Option A (current, implemented): keep tenant-wide visibility for `public`/
  `internal` with `tenant_visible=true`, for every active member of the tenant.
- Option B (the stricter reading): narrow `internal` to the uploader (and
  connector-authorized principals), requiring an explicit group, department, or
  steward grant for any other member to reach it.

### Security implications of the tenant-wide widening (Option A)

- An ordinary tenant member, including a low-privilege viewer, can retrieve the
  content of any `public`/`internal`, `tenant_visible` document in chat, as
  evidence, and in the catalog listing. Content is shared within the tenant, not
  just metadata.
- The default is the permissive one. Because new documents default to
  `internal`/`tenant_visible=true`, a user who uploads a document expecting it to
  stay private is, by default, publishing it tenant-wide. The control depends on a
  steward raising the classification or clearing `tenant_visible`; it is not a
  privacy-by-default control.
- The widening is bounded and fail-closed at the edges: cross-tenant access is
  refused (`document_policy.py:83-84`), `tenant_visible=false` removes the
  fallback, `confidential`/`highly_restricted` never use it
  (`governance.py:62-65`), an unknown classification resolves to
  `highly_restricted` (`governance.py:71-79`), and connector-origin content must
  also pass the connector authorization gate (`document_policy.py:198-216`), so a
  tenant-wide classification never widens provider-side permission.

### Accepted decision (v1)

Keep Option A, tenant-wide `public`/`internal`, as the v1 default. It matches the
accepted product decision recorded in `docs/plan/BV1_B.md` and the intended
behavior the global acceptance implies (a member relying on another member's
handbook). It preserves the existing bounded posture: tenant isolation, the
`tenant_visible` opt-out, explicit-grant-only for the two sensitive classes, and
the connector intersecting gate. Two follow-ups make the decision safe rather
than implied:

1. Correct the stale note at `document_policy.py:8-16` so the module docstring
   matches the implemented behavior. It is the single artifact that led the BV6
   plan to record this as unresolved.
2. Keep the existing ingest default (`internal`, `tenant_visible=true`) for v1. Any future privacy-by-default change is a separate behavior-changing product decision and must not be inferred from this memo.

Reject Option B for v1: it would revert a resolved, tested decision and break the
intended cross-owner consumption behavior, and it would need a new connector and
group fallback story. If it is ever revisited, it is a deliberate narrowing that
should be its own change with its own owner decision.

---

## Decision 2. Per-table / per-object classification labels

### Current behavior

Classification is source-level only. There is no per-table or per-object
classification label anywhere in the data-governance model.

- `DataSource` carries one classification and one policy per source:
  `classification`, `department_id`, `tenant_visible`, `allowed_group_ids_json`
  (`backend/app/models.py:195-206`). A steward sets them through
  `set_data_source_policy` (`backend/app/services/access_governance.py:1160`),
  which writes the values on the source row.
- The structured planner and executor authorize at the source and the object
  allowlist, not by classification per object. `source_scope_still_authorized`
  (`backend/app/services/report_scope.py:81-113`) checks the source's
  `enabled`/`status`/`schema_json` and that the pinned tables are inside the
  source-level `authorized_objects_json`; `structured_executor._authorized_tables`
  (`backend/app/services/structured_executor.py:39-48`) derives the table set from
  the same source-level allowlist. An object not on the allowlist is invisible, but
  every object on it shares the source's single classification.
- `authorized_objects_json` (`backend/app/models.py:190`) is an object-narrowing
  allowlist, not a per-object classification. It cannot say one table is
  `internal` and another is `highly_restricted` within the same source.
- This is the recorded state: `docs/plan/BV1_C.md:50-56` lists table/object-level
  policy as NOT RUN and notes that source policy is evaluated before any object
  list is exposed, so a broader table permission cannot override a stricter source
  permission.

### When table-level classification is genuinely necessary

Source-level classification is sufficient while a source holds one sensitivity
class. Table-level labels become necessary only when a single connection genuinely
spans materially different sensitivities that must be consumed by different
audiences at the same time:

- One operational database with a `directory`/`reference` table that should be
  tenant-wide `internal` and a `compensation`/`pii` table that must be
  `highly_restricted`, where the connection cannot be split into two sources.
- A warehouse connection that serves several departments, where each schema must
  carry its own department and group policy without splitting the credential.
- A source where the object allowlist already hides most tables but the exposed
  tables still differ in sensitivity.

In each case there is an existing workaround that covers the common form of the
need: classify the source at its strictest class, and split genuinely
independent-sensitivity datasets into separate sources (separate credentials or
separate schemas), using `authorized_objects_json` to narrow the exposed table set.
Table-level classification only earns its cost when a tenant cannot split the
source and cannot accept the strictest class for the whole source.

### Complexity and security cost

- Policy cardinality moves from one row per source to one policy per (source,
  object). Every surface that today reasons about a source classification must
  instead resolve an object classification: the pre-planner filter, the schema and
  SQL exposure path, the object allowlist, report revalidation, and the catalog.
- Precedence becomes non-trivial. The rule "a broader table permission must never
  override a stricter source permission" (BV1_C.md:54-56) must be defined and
  enforced for every combination of source class, object class, group grant, and
  department. More sub-source granularity means more fail-open paths to get wrong.
- New storage and migration are required: an object-policy model keyed by qualified
  object name, with the same fail-closed defaulting (unknown resolves to
  `highly_restricted`) and the same audited steward mutation and scope checks that
  `set_data_source_policy` uses today.
- The connector intersecting gate already provides per-resource granularity where a
  connector is in play, so table-level labels are partly redundant for
  connector-origin data and mostly matter for native structured sources.

### Accepted decision (v1)

Keep source-level classification as the v1 position. Do not add per-table or
per-object classification labels in v1. Source-level classification plus the
object allowlist already answers the questions tenants ask in practice: which
source may this principal use, and which tables inside it are exposed. Where a
tenants needs finer sharing, the supported path is to classify the source at its
strictest class and split genuinely independent-sensitivity data into separate
sources, narrowing exposed tables with `authorized_objects_json`. Add per-object
labels only when a concrete tenant presents a mixed-sensitivity single-source case
that cannot be split and cannot accept the strictest class for the whole source.
Until then it is complexity and new fail-open surface without a proven need.

---

## Sources

- Historical BV6 next-wave plan, PR #69, which raised these decisions before BV6-A/B were implemented.
- [BV1_B.md](BV1_B.md) (tenant-wide `internal` decision, resolved).
- [BV1_C.md](BV1_C.md) (structured source policy, table-level NOT RUN).
- Code: `backend/app/core/governance.py`,
  `backend/app/services/document_policy.py`,
  `backend/app/services/access_governance.py`,
  `backend/app/services/report_scope.py`,
  `backend/app/services/structured_executor.py`,
  `backend/app/services/orchestrator.py`,
  `backend/app/services/tools.py`, `backend/app/api/knowledge.py`,
  `backend/app/models.py`.
- Tests: `backend/tests/test_bv1_tenant_wide.py`,
  `backend/tests/test_bv1b_knowledge_classification.py`,
  `backend/tests/test_bv1c_data_classification.py`.
