# CA2-B consumer map: existing contracts the candidate gate must integrate with

Repository: sjevans1/OpenJM-Enterprise-AI
Base: CA2-A accepted head `8557487` (PR #94)
Package: CA2-B, integration prototype, isolated worktree, stacked draft PR
Status: **prototype. Off by default. No production default is changed and nothing merges.**

This document is the required first step: the existing consumer contracts, mapped before any
integration, so the smallest safe change is visible and the legacy path stays available for
baseline comparison.

## 1. The single governed candidate seam

Every consumer obtains its Knowledge candidate set from one place:

`app/services/document_policy.py:169 governed_tenant_documents(db, access) -> list[Document]`

It does three things today:

1. loads every tenant retrievable document, ordered `created_at DESC` (line 185-195);
2. computes the tenant's connector-origin document ids (`connector_origin_document_ids`);
3. if any connector-origin document exists, calls
   `authorized_connector_document_ids_for_context(db)` (line 200-206) and drops every
   connector-origin document not in that set (line 212).

Step 3 is the measured defect from CA2-A phase 1: that gate enumerates the corpus and stops at
`MAX_AUTHORIZATION_CHECKS = 100` without ordering, so a connector-origin document that is
genuinely authorized but sits outside the window is dropped **silently**. The measured baseline
was 100 of 150 reachable with *zero denials recorded* for the 50 missing, because unexamined and
denied were the same thing.

## 2. Consumers

### 2.1 Knowledge / Chat / Hybrid retrieval

`app/services/orchestrator.py`

- `_ready_documents()` (line 190) is the request path. With a request identity (`access` present)
  it calls `governed_tenant_documents` (line 202) and, when a `ReportSourceScope` is pinned,
  narrows to the pinned ids and raises `ReportScopeError` if the pinned set is not fully present
  (line 203-207).
- Its legacy branch (line 210-243, no request identity) queries documents directly and intersects
  `authorized_connector_document_ids_for_context` at line 223, with the same 100 cap.
- `_execute_knowledge_search()` (line 435) runs the `knowledge.search` tool and returns evidence.

Consumer type: **exploratory**, except when a report scope is pinned, which is **authoritative**.

### 2.2 `knowledge.search` tool

`app/services/tools.py:247 KnowledgeSearchTool`

- identity branch (line 279-296): `governed_tenant_documents`, then pinned narrowing with
  `ToolPermissionError` if the pinned set is incomplete;
- legacy branch (line 297-330): direct query plus `authorized_connector_document_ids_for_context`
  at line 299, same cap, pinned check at line 325-330;
- the retrieved evidence is then integrity-checked: every `source_id` and every
  `equivalent_sources` id must be inside `authorized_source_ids` (line 331-393), otherwise
  `ToolPermissionError`. **This is the enforcement point that makes the candidate set load
  bearing**: evidence outside the authorized set is refused, not filtered.
- there is already a default-off additive hook for report candidates (line 394 onward) gated by
  `settings.report_authoritative_candidates_enabled`, which establishes the precedent for
  additive, default-off integration in this file.

Consumer type: **exploratory**, **authoritative** when `context.report_scope` is set.

### 2.3 Catalog listing

`app/api/knowledge.py:70 list_documents`

- `GET /documents`, `response_model=list[DocumentOut]`, returns
  `[DocumentOut]` from `governed_tenant_documents` (line 79).
- Same cap, same silent omission, and no coverage signal reaches the client.

Consumer type: **catalog**.

### 2.4 Pinned reports, reruns, snapshots, exports

- `ReportSourceScope` pins document ids; completeness is enforced by set comparison in
  `orchestrator._ready_documents` (line 206) and `tools.KnowledgeSearchTool` (line 293, 325).
- Both currently derive the pinned set from the **corpus window**, so a pinned scope of 65 to 100
  sources can fail spuriously when the tenant has more than 100 connector resources: the pinned
  document simply was not examined. It fails closed, which is correct, but the failure is for the
  wrong reason and gives no diagnosis.
- `app/services/notifications.py:366` already uses
  `require_current_authorization` to re-check a specific resource, which is the closest existing
  precedent for consumption-time revalidation.

Consumer type: **authoritative**.

### 2.5 CA1 guarantees that must survive

`app/services/connectors/authorization.py`:

- tenant isolation is re-asserted per resource inside `authorize_resource` (line 256-260);
- a denial never mutates shared content (CA1 non-mutation);
- a provider outage fails closed and returns nothing rather than a cached yes;
- `require_current_authorization` (line 383) fails closed when it cannot prove a yes.

## 3. Constraints discovered

| Constraint | Consequence for CA2-B |
| --- | --- |
| `governed_tenant_documents` returns `list[Document]` and is used by three consumers | its signature cannot change; the integration must be behind a flag inside it |
| `GET /documents` is `response_model=list[DocumentOut]` | it must keep returning a list; pagination and coverage must be **additive**, on a new surface |
| the tool refuses evidence outside the authorized set | the candidate set must be exactly the authorized set, not a superset with filtering later |
| report scopes must stay byte-identical in a scoped run | the authoritative path must not reorder or extend a pinned result |
| `revalidate_document_ids` resolves **connector** rows only | a pinned scope can contain native documents, which have no provider resource and must be governed by local policy alone |
| `DEFAULT_PAGE_BUDGET` (32) and the legacy cap (100) | a request-level cap must sit **above** 100, or enabling candidate mode could check fewer resources than the path it replaces |

## 4. Integration decision

Smallest safe change, all of it additive and default-off:

1. **New module** `app/services/retrieval_candidates.py` holds the integration seam. It is the
   only place that knows about both the CA2-A contract and the existing consumers.
2. **Mode flag** `retrieval_candidate_mode`, default `"legacy"`. In legacy mode the module
   delegates to `governed_tenant_documents` and reports coverage as *unknown* rather than
   claiming completeness it cannot observe.
3. **`governed_tenant_documents`** consults the flag through a local import and delegates. Its
   signature, return type and legacy behaviour are unchanged.
4. **Exploratory consumers** (`knowledge.search`, and the catalog's additive surface) use
   `governed_candidates`, which returns documents **plus** truthful coverage and an
   application-generated partial-coverage notice carrying no protected metadata.
5. **Authoritative consumers** (pinned reports, reruns, snapshots, exports) use
   `strict_pinned_documents`, which revalidates the exact pinned scope through
   `revalidate_document_ids` + `require_strict_scope`, and never assembles a result from
   authorizations collected on earlier pages. Native sources in the pinned set keep local-policy
   semantics, because they have no provider resource to revalidate.
6. **Catalog** keeps `GET /documents` exactly as it is and gains an additive paginated surface
   that carries coverage and the notice. Compatibility is preserved by not touching the existing
   response model.

## 5. What CA2-B will not do

- No replacement of Chroma, DB-GPT, the embedding model, the Rahkia boundary or identity.
- No change to the legacy path's behaviour when the flag is off.
- No activation as a production default; the flag default is `legacy`.
- No merge. PR #94 and the CA2-B PR remain drafts. CA2-C stays separate.
