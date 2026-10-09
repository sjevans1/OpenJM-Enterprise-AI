# BV6 next-wave plan: governed business-value layer and product-completion gaps

Status: **planning document only. No product code. Nothing here is implemented or
authorized for merge.**

Authority: [Issue #45](https://github.com/sjevans1/OpenJM-Enterprise-AI/issues/45)
("Hardening & Business Value Realization"), BV6 section and section 5 "Governed
reports". This document reconciles BV6 against the code at the base recorded
below. It does not widen BV6 beyond what Issue #45 states.

## Baseline inspected

| Item | Value |
| --- | --- |
| Branch | `replay/g-nextwave` |
| Base | `eafce8f3ded930511785eed72eec9c1fbc165627` (`origin/main`) |
| Migration head | `0017_inf1b_admission` |
| Reports surfaces read | `backend/app/api/reports.py`, `backend/app/services/report_scope.py`, `backend/app/services/report_runs.py`, `backend/app/services/access_governance.py`, `backend/app/services/document_policy.py`, `backend/app/models.py` (`SavedReport`, `ReportDefinitionVersion`, `ReportRun`) |
| Retrieval surfaces read | `backend/app/services/orchestrator.py`, `backend/app/services/tools.py`, `backend/app/services/knowledge.py` |

This is a dated observation. Refresh the head before any implementation starts.

---

# Part 1. BV6 governed business-value layer

Issue #45, section 5, sets the boundary in one paragraph:

- Saved Reports are evidence-backed governed snapshots, not generic Chat files.
- They retain provenance; re-check current source authorization on
  open/rerun/export; cannot bypass revoked access.
- They **may later** support explicit `Approved/Authoritative/Featured` curation
  by authorized data owners.
- Merely saving a report must **not** silently train the model or alter retrieval
  priority.

Issue #45, BV6, repeats the same four lines and adds "do not make every saved
report automatically authoritative or alter retrieval ranking merely because it
was saved", and "this can be a later sub-phase if BV1-BV5 are already
substantial".

Nothing below adds a fifth state, a second vocabulary, model training, or a
retrieval-ranking change for ordinary documents. It is bounded to a curation
state on an existing `SavedReport` plus an explicitly opt-in gate that lets an
`Authoritative` report be *offered* as a retrieval candidate.

## 1.1 Current state (why BV6 is a new layer, not a tweak)

- `SavedReport` (`backend/app/models.py:249`) has no curation, approval or
  department column. Its fields are content, provenance, `execution_class` and
  `snapshot_as_of`.
- There is no approval route, no curation state machine and no
  `authoritative/featured/curated` string anywhere in the reports code.
- Report access today is already fully revalidated: every open, rerun-preview
  and export runs `_sources_available` (`backend/app/api/reports.py:128`), which
  re-reads current document visibility, the connector gate, and current
  structured-table grants. BV6 must plug into that predicate, not replace it.
- Retrieval today has no link to reports at all. The candidate set is built by
  `governed_tenant_documents` and `visible_documents` over `Document` rows
  (`backend/app/services/orchestrator.py:202,241`; the governed tool path at
  `backend/app/services/tools.py:273`). `saved_reports` is not an input to any
  retrieval, scoring or prompt path.

## 1.2 States

One column, `curation_state`, on `SavedReport`. Ordered ladder plus a null
default. Transitions are monotonic forward only; dropping any state is the
`none` (withdrawn) transition.

| State | Meaning | Retrieval effect |
| --- | --- | --- |
| `none` (default) | An ordinary saved snapshot. This is what saving produces, always. | None. |
| `approved` | A data owner or steward has reviewed the retained evidence and method and confirmed it is a correct governed snapshot of currently authorized sources as of `snapshot_as_of`. Visible to the owning department as a vetted artifact. | None. |
| `authoritative` | Two distinct authorized principals have designated this report the canonical answer for its pinned source scope and question class. Eligible to be offered as a retrieval candidate **only** where the tenant retrieval policy opts in. | Opt-in candidate only; never a ranking change and never an authorization bypass. |
| `featured` | A tenant admin highlight for the department or tenant report catalog. Presentation only. | None. |

`approved` and `authoritative` are the only states a steward can grant. `featured`
is an admin-only catalog flag and carries no evidentiary weight.

## 1.3 Who can assign each state

Authority is the same customer-content governance axis already built in BV1-A:
tenant `admin`/`owner`, or a data steward whose scope covers the report's
governed sources. Platform operators are deliberately excluded.

| Transition | Who may perform it |
| --- | --- |
| request review (`none` -> `under_review`) | The report owner, or any steward/admin with scope over the pinned sources. |
| approve (`under_review` -> `approved`) | One steward whose scope covers the pinned sources, or a tenant admin. The report owner may not self-approve. |
| mark authoritative (`approved` -> `authoritative`) | Two distinct principals, each a steward with scope over the pinned sources or a tenant admin. The first approver and the second approver must be different principals (two-person rule). |
| feature (`approved` or `authoritative` -> `featured`) | Tenant admin only. |
| withdraw (any -> `none`) | The report owner, any covering steward, or a tenant admin. |
| auto-demote (any -> `none`) | System, on failed source revalidation (see 1.8). |

Platform operators cannot assign, request, approve or withdraw any state. Curation
is a customer-content act; giving it to platform authority would contradict
Issue #45's rule that platform administration "must not automatically grant
standing customer-content access".

## 1.4 Data-owner / steward approval flow

1. The report owner requests review of an owned, currently available snapshot.
   The request stores no new evidence and no model output; it only records intent
   and the current source-policy epoch.
2. The system revalidates the pinned scope with the existing `_sources_available`
   predicate. A snapshot whose sources already fail authorization cannot enter
   review.
3. A covering steward or tenant admin approves or rejects. Approval is refused if
   the approver is the owner.
4. For `authoritative`, a second distinct authorized principal confirms. The
   record stores both principal ids and both signatures.
5. Every step writes an audit row.
6. A report may be withdrawn at any time; re-promotion after withdrawal requires a
   fresh flow (no "resurrect" shortcut).

Rejection and withdrawal are terminal for that attempt; the underlying snapshot
is untouched.

## 1.5 Audit trail

Every transition uses the existing `record_audit` path (the same one used by
`set_document_policy` and the steward/platform mutations). Audited fields:

- action: `governance.report.curation.<transition>`;
- resource type `saved_report`, resource id;
- actor principal id, and for `authoritative` both approver ids;
- prior state and new state;
- the revalidation result at decision time (pass/fail and the reason category);
- the source-policy epoch the decision was made under;
- department id where departmental ownership applies.

The audit row is tenant-scoped and append-only. A withdrawal or auto-demotion is
itself an audited transition; history is not rewritten.

## 1.6 Revocation

Revocation is a live-state change, matching the BV1 archiving semantics: the row
is preserved, not deleted, and a repeated operation is a no-op rather than an
error.

- Explicit withdrawal returns `curation_state` to `none` and clears retrieval
  eligibility on the next evaluation. There is no cache to expire (the
  `load_principal_access` discipline: re-read per request).
- Losing the steward grant, group membership or department that authorized a
  promotion does not silently keep the state, because the next candidate
  evaluation re-runs source authorization for the *requesting* principal, not for
  the historical approver.
- Archived department/group scope neutralizes a department-scoped curation
  authority exactly as it neutralizes a department-scoped steward grant today
  (`_effective_steward_scopes`, `backend/app/services/access_governance.py:214`).
- Deleting the report, or losing the parent conversation/message the snapshot was
  taken from, invalidates eligibility (the existing `_available` /
  `_source_message_exists` checks, `backend/app/api/reports.py:199`).

## 1.7 Departmental ownership

- A report takes a `department_id` owner. It is derived from the department of its
  pinned sources where they agree, or set explicitly by an approver whose steward
  scope covers that department.
- Departmental ownership decides who may review or approve (`authoritative`,
  `approved`) and whose catalog the report appears in.
- Cross-department promotion is refused: a steward scoped to HR cannot promote a
  Finance-owned report.
- Where a pinned scope spans more than one department, ownership is the narrowest
  department common to the pinned sources; if there is none, only a tenant admin
  may set ownership.
- Department archival removes the report from that department's stewardship
  surface but preserves the row (no destructive cascade).

## 1.8 Source-policy revalidation

BV6 adds no new authorization predicate. It reuses the existing one at two
moments:

1. At every curation transition, the pinned scope must pass
   `_sources_available` under the actor's current access. A promotion fails closed
   if any pinned document, connector-origin document, or structured table is no
   longer authorized.
2. At every retrieval-candidate consideration, the same predicate runs under the
   *requesting* principal's access. A report that fails is skipped before it can
   contribute a title, a snippet or evidence.

Because revalidation is a live read over current rows, a change in a source's
classification, allowed groups, connector gate, or structured-table grant takes
effect on the next request with nothing to expire. This is the same no-cache
contract that already governs Knowledge and Data policy.

## 1.9 Preferred retrieval candidate (bounded)

This is the only place BV6 may touch retrieval, and it is opt-in and narrow.

- Only `authoritative` reports are eligible. `approved` and `featured` are never
  candidates.
- Eligibility is gated by a versioned per-tenant policy flag, default **off**.
  With the flag off, an authoritative report has zero retrieval presence.
- Even when the flag is on, an authoritative report is offered as a candidate
  only when all of the following hold:
  1. the report is in the requesting principal's tenant;
  2. the report's pinned sources currently pass `_sources_available` for that
     principal;
  3. the report is not withdrawn, deleted, auto-demoted or policy-stale.
- A candidate report may only **reference** sources the requesting principal is
  already separately authorized to read. It is never allowed to introduce a
  document, table or source that failed the existing gate, and it can never widen
  the candidate source set.
- The candidate is additive: the ordinary governed document set is still built
  first, and a curated report is attached as an additional evidence reference. It
  does not replace, reorder or re-score ordinary documents.
- Any candidate contribution must be recorded in the execution trace so the
  ranking decision is auditable.

The flag, the eligibility predicate and the trace field are the whole retrieval
surface BV6 owns.

## 1.10 Why saving a report alone never changes ranking

This is a structural fact of the current code, and BV6 keeps it:

- Saving writes one `SavedReport` row (`backend/app/api/reports.py:253`). It does
  not write to any retrieval index, embedding store or score table.
- The retrieval candidate set is built exclusively from `Document` rows via
  `governed_tenant_documents` and `visible_documents`
  (`backend/app/services/orchestrator.py:202,241`;
  `backend/app/services/tools.py:273`). `saved_reports` is not an input to that
  path.
- Saving assigns `curation_state = none`. Only an explicit two-person
  `authoritative` transition, plus an opt-in tenant flag, can make a report
  visible to retrieval at all.

Therefore the observable behavior is: the retrieval result set and its ordering
are byte-identical before and after a save. The required regression test asserts
exactly that identity.

## 1.11 How stale or revoked authoritative reports stop influencing retrieval

- Revalidation is re-run at every candidate consideration under the requesting
  principal's access. A report whose sources fail is skipped, so a revoked
  authoritative report contributes nothing on the next request.
- A source-policy change (classification, allowed groups, connector gate, table
  grants) makes dependent authoritative reports fail closed with no admin action.
- The auto-demote path clears `curation_state` to `none` on the next evaluation
  when a pinned source fails, and writes an audit row. The historical row is kept.
- Deleting the report, withdrawing it, or losing the parent message invalidates
  eligibility immediately.
- The retrieval policy flag can be turned off, which removes all curated-report
  candidates at once without touching report rows.

## 1.12 Invariants

1. Saving a report assigns no curation state and changes no retrieval behavior;
   the pre-save and post-save retrieval result sets are identical.
2. Curation state is tenant-scoped customer content. Platform operators cannot
   assign it and it confers no customer-content access.
3. No curation state bypasses source authorization. Every transition and every
   candidate consideration re-runs the current source predicate.
4. `authoritative` requires two distinct authorized principals; self-approval is
   impossible.
5. A curated report never widens the retrieval candidate set. It may only
   reference sources the requesting principal is separately authorized to read.
6. Every transition is audited with prior state, new state, actor(s) and the
   revalidation result.
7. Revocation and auto-demotion are live-state changes. History is preserved, with
   no destructive cascade, matching the BV1 archive semantics.
8. Withdrawal or auto-demotion takes effect on the next authorization/retrieval
   evaluation, with no cache to expire.
9. Curation is additive to, and never a substitute for, the existing
   open/rerun/export revalidation.
10. Retrieval use is off by default and enabled only by an explicit versioned
    tenant policy.
11. Curation is never used to train, fine-tune or otherwise influence the model;
    it changes only who may be offered a report as a governed snapshot.

## 1.13 Required negative tests (RED -> GREEN)

- save a report, then assert no curation state and an identical retrieval result
  set and ordering.
- the report owner cannot self-approve `authoritative`.
- a single principal cannot satisfy the two-person rule alone.
- a viewer/editor without steward or admin authority cannot request, approve or
  feature.
- a platform operator without content support receives 403 on every curation
  route.
- cross-tenant: a principal in tenant A cannot curate a report pinned to tenant B
  sources; the failure reveals no foreign title or evidence.
- promotion is refused when any pinned source currently fails authorization.
- source revocation after promotion causes the report to be skipped as a
  candidate on the next request and auto-demoted, with the history row preserved.
- group-membership revocation removes candidate eligibility and demotes on the
  next request.
- a withdrawn or deleted report produces no candidate and leaks no evidence.
- an authoritative report never introduces a source outside the existing
  authorized candidate set (no widening).
- with the retrieval policy flag off, an authoritative report contributes zero
  candidates.
- revalidating an authoritative report for a principal who lacks source access
  skips it with no title or evidence leakage.

## 1.14 Explicitly out of scope

No automatic promotion from usage or telemetry; no model training or fine-tuning
from reports; no cross-tenant curation; no ranking change for ordinary documents;
no evidence rewriting; no custom tenant vocabularies; no scheduled auto-refresh
of curation state; no change to `ReportRun` execution semantics; no change to the
existing export revalidation. No installer, refactor, Rahkia integration or BV6
implementation is in this document.

---

# Part 2. Remaining completion gaps vs the Issue #45 global acceptance

Only real, repo-grounded gaps. "Grounding" is a file, route or grep result at the
baseline. Classification uses four bands: MUST (before feature complete), SHOULD
(before productization), DEFER-Rahkia (specialist inference phase), DEFER-post-v1.

## 2.1 MUST before feature complete

| Gap | Grounding | Why it blocks feature complete |
| --- | --- | --- |
| BV5 Chat artifacts (Issue #43). No artifact model, storage, route or renderer. | `grep -rni artifact backend/app`: every match is the inference registry's `artifact_ref` (a model-deployment reference), at `backend/app/services/inference/registry.py:95,126`, `backend/app/services/inference/routing.py:388`, `backend/app/api/inference.py:47,246`, `backend/app/models.py:1402`. No Chat-artifact table, route or renderer. `grep -rni artifact frontend/src` returns nothing. | The global acceptance "End user" clause requires "can create real downloadable artifacts", and the Security clause requires "report/artifact downloads enforce current authorization". With no artifact surface the clause is unmet, and its authorization requirement is untestable. |
| BV6 governed report curation (this document). | `SavedReport` (`backend/app/models.py:249`) has no curation/state column; `grep -rn "curation\|approved\|authoritative\|featured" backend/app/models.py` returns no report field; no approval route exists. | The Data steward acceptance requires governed report creation and "source/report access follows group/classification policy". The stewardship model for reports is the BV6 layer; without it reports remain per-owner snapshots with no governed curation. |

## 2.2 SHOULD before productization

| Gap | Grounding | Note |
| --- | --- | --- |
| BV4 Chat-first permission-aware navigation (Issue #44). Knowledge and Data surfaces are still always visible; only the Admin panel is gated. | `frontend/src/App.tsx:60-62` lists `chat/knowledge/data` as unconditional nav values; `canAdminister` gates only `AdminPanel` (`App.tsx:313,1654`). | Issue #45 BV4 wants Knowledge/Data governance surfaces hidden unless the user has stewardship/admin capability. The mode selector may stay; the stewardship-surface gating is missing. |
| OpenJM support content-support read path (BV3-A residual). | `support_scopes` is resolved and stored (`backend/app/core/identity.py:98,200`; `backend/app/services/identity.py:365`) but `grep -rn "has_support_scope\|support_scope" backend/app` shows no consumer in the application read paths. | The OpenJM-admin acceptance requires support of initial setup "through explicit audited authority". Delegation grant/revoke exists; a content-support read path that consumes an active delegation does not. |
| Tenant-wide `internal` sharing product decision (BV1-B NOT RUN). | `backend/app/services/document_policy.py:8-16` documents the stricter reading; docs/plan/BV1_B.md records it NOT RUN. | A product decision, not a defect. Leaving it unresolved means `internal` is not tenant-wide, which may not match the intended product behavior the global acceptance implies. |
| Per-object/table classification labels (BV1-C NOT RUN). | Policy is source-level only: `set_data_source_policy` (`backend/app/services/access_governance.py:1160`) writes classification on the source; `report_scope.source_scope_still_authorized` checks source-level `authorized_objects_json` only. | Source-level enforcement exists; narrower table-level labels do not. Adequacy depends on whether tenants need sub-source granularity. |

## 2.3 DEFER to the Rahkia specialist phase

| Gap | Grounding |
| --- | --- |
| INF1 production inference cutover and real runtime/GPU/model qualification. | Migrations `0015_inf1_inference_registry` and `0017_inf1b_admission` are present, so the INF1-A/B contract foundation landed; Issue #45 "Product decision - Rahkia production cutover deferred" assigns the runtime integration to the specialist team. INF1.md acceptance A14 remains NOT RUN. |
| Model-accurate local token counting (currently a coarse fallback estimate). | Issue #45 sovereign requirements say local usage should "eventually use a model-appropriate tokenizer/runtime-reported counts rather than the current coarse fallback estimate". |

## 2.4 DEFER post-v1

| Gap | Grounding |
| --- | --- |
| Automatic natural-language mode routing. | Issue #45 BV4 explicitly defers it; the explicit selector remains (`frontend/src/App.tsx:60-62`). |
| Cross-tenant operator targeting on relocated connector/operations routes. | docs/plan/BV3_C.md "Not run / limitations" records this as deliberately not invented. |
| XLSX artifact format. | Issue #45 BV5 lists XLSX as "can follow" after HTML/PDF/DOCX/Markdown/CSV. |
| Dead frontend panels (`ConnectorsPanel`, `OperationsPanel`) still compiled in. | `frontend/src/App.tsx:45-46,1650-1652` still imports and renders them; docs/plan/BV3_C.md notes they are not reachable from navigation and calls removal frontend cleanup. Maps to REL1-D repository normalization. |
| REL1 packaging, installer, upgrade/rollback tooling and repository refactor. | Issue #45 REL1 workstream, explicitly "should not begin until active product-functionality gates are substantially complete". Out of scope for this document. |

## 2.5 Classification counts

- MUST before feature complete: 2 (BV5 Chat artifacts; BV6 governed report
  curation).
- SHOULD before productization: 4 (BV4 navigation gating; BV3-A content-support
  read path; tenant-wide `internal` product decision; per-object classification
  labels).
- DEFER Rahkia specialist phase: 2 (INF1 production cutover/qualification; local
  tokenizer accuracy).
- DEFER post-v1: 5 (automatic mode routing; cross-tenant operator targeting; XLSX
  artifacts; dead frontend panels; REL1 packaging/refactor).

Total: 13 grounded gaps.

## 2.6 Already met (not gaps)

- Report/export reauthorization on open, rerun-preview and export is implemented
  and re-checks documents, the connector gate and structured-table grants
  (`backend/app/api/reports.py:128`; `backend/app/services/report_exports.py`).
- M1 usage metering, M2 aggregation and M3 entitlements have landed (migrations
  `0011`, `0016`; `backend/app/services/usage_aggregation.py`,
  `backend/app/api/platform_usage.py`).
- BV3 control-plane relocation is implemented on the backend
  (`backend/app/api/connectors.py`, `backend/app/api/operations.py`,
  migration `0014`).
- E1/E2/E3 correctness lanes are merged on the base (git log: issues #6 and #7
  closed).

---

# Verification and limitations

- This document runs only document/link checks. It does not claim any product,
  PostgreSQL, runtime, GPU or CI acceptance.
- Negative tests in 1.13 are a required contract for a future BV6 implementation;
  none are written here. Nothing in this document is implemented.
- Gap grounding is a dated observation at `eafce8f`. Refresh the head before
  acting: migration head, routes and issue states may have moved.
