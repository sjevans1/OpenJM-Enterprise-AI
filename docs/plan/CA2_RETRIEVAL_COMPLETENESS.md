# CA2 — Bounded, query-relevant connector retrieval with no silent omission

Plan revision: `ca2-retrieval-1`
Repository: **sjevans1/OpenJM-Enterprise-AI only**
Base: `main` at `8d6b7d8`
Status: **planning only. No implementation is proposed for authorization by this document.**
Depends on: CA1 (accepted, merged `a4e1e4f`)
Authorization for planning: owner directive, 2026-10-11.

## Objective

Remove the possibility that an authorized connector resource is silently omitted from
retrieval because it falls outside an arbitrary candidate window, while keeping
authorization fail-closed and bounded.

## Confirmed evidence

Reproduced against `aacc9f2` before CA1 and re-confirmed present on current `main`:

| Bound | Location on `main` at `8d6b7d8` | Effect |
| --- | --- | --- |
| `MAX_AUTHORIZATION_CHECKS = 100` | `services/connectors/authorization.py:67`, applied as `.limit(limit)` at `:362`, default at `:340` | The authorization gate enumerates at most 100 `ExternalResource` rows per request and authorizes exactly those |
| `connector.search` `statement.limit(200)` | `services/actions/connector_tools.py:137` | The connector search tool sees at most 200 rows |

Reproduction with 150 resources all authorized for one principal:

    PROBE2: bound=100 seeded=150 authorized=100 denied=0
    assert 100 == 150

100 returned, 50 unreachable, and **no denial recorded for the 50**, so nothing surfaced the
loss. There is no ordering on that select, so which 100 is unspecified and depends on the
planner and storage order. An availability defect that is not reproducible run to run is the
worst kind, because it does not appear in a bug report.

The gate is called from four retrieval paths, so all four inherit the behaviour:
`services/orchestrator.py:223` (chat and Knowledge), `services/document_policy.py:205`,
`services/tools.py:299` (tool execution), `api/reports.py:224` (report execution).

## Why the current shape cannot be fixed by raising the number

The bound exists for a good reason, stated in the code: it stops one question from becoming an
unbounded number of outbound provider calls. But it is applied to the wrong list. Today the gate
answers "which documents is this principal authorized for", which requires enumerating the
corpus. Any fixed number then becomes a silent truncation of an authorized set. Raising it
replaces a small silent omission with a large one, and moves the provider-call cost, without
changing the failure mode.

## Scope

- The authorization/retrieval boundary for connector-sourced evidence, and the two bounds above.
- Determination of what "bounded and query-relevant" means concretely, and the reporting of any
  truncation.
- Tests at the scale and with the permission shapes that expose the defect.

## Exclusions

- No change to the CA1 semantics: authorization still decides, still never mutates shared
  content, and still fails closed.
- No new database table or migration unless a later plan revision argues for one explicitly.
- No change to the provider authorization contract, the mapping contract, or tenant isolation.
- No change to the model gateway, chunking, embeddings, or the vector store.
- Not the `connector.search` 200 bound unless it is agreed into CA2 explicitly (see open
  question 3).
- Not INF1, M3 entitlements, or Workspace integration.

## Invariants

1. **Fail-closed.** Any failure to prove authorization excludes the evidence for that request.
2. **No silent omission.** If a bound is reached, that fact is recorded and observable on the
   request. Truncation is a reported condition, never a quiet one.
3. **Deterministic.** The same corpus, permissions and question produce the same result set and
   the same truncation decision. No dependence on planner or storage order.
4. **Non-enumerating.** A denial or an outage must not reveal which other resources exist.
5. **Bounded.** Provider calls per request remain capped by an explicit, declared budget.
6. **CA1 preserved.** Authorization remains non-mutating; content-scoped withdrawal stays an
   explicit lifecycle operation.

## Design options

**A. Order and report.** Keep enumerate-then-authorize, add a deterministic `order_by`, and
record truncation when the bound bites. Smallest change. Does not remove the omission, only makes
it visible and reproducible. Still omits authorized documents.

**B. Invert the flow: authorize the candidates retrieval actually wants.** Retrieval already
filters by tenant and by chunk-level ACL inside the vector and keyword query, which is the stated
design intent ("filtered inside the query, never after top-k"). Pass the retrieved candidate set
into the authorization gate and ask "may this principal receive these N candidates", where N is
the retrieval window and not the corpus size. The bound becomes a per-request provider-call
budget on an already-relevant, already-ordered set, and corpus size stops determining what is
reachable. This removes the omission by construction rather than by raising a limit.

**C. Two-phase: cheap pre-filter then authorize.** A bounded, ordered pre-filter selects
candidates using tenant and permission-container columns already on the row, then authorizes only
those. A subset of B, useful if B's plumbing proves too invasive, at the cost of a second pass.

**Recommendation: B, with C as the fallback.** B is the only option that makes the defect
structurally impossible, it aligns with the ACL design already chosen, and it bounds provider
calls on relevant candidates rather than on an arbitrary slice. A is worth doing as a minimal
interim only if B cannot land quickly, and it must not be presented as a fix.

## Required acceptance tests

At minimum, and each at a scale that fails today:

1. **More than the bound.** At least 150 authorized resources; all 150 reachable. (The current
   code returns exactly 100.)
2. **Varied ordering.** The same set, inserted and selected in a different order, yields the
   same result set and the same truncation decision, with no dependence on planner order.
3. **Selective permissions.** Some resources authorized and some denied for the same principal:
   only the authorized ones are returned, and each denial is recorded by reason.
4. **Relevant documents outside the initial window.** A resource that is expected to answer the
   question, sitting beyond the first window, is still retrieved.
5. **Budget determinism.** When the declared provider-call budget is reached, the outcome is
   deterministic and truncation is recorded on the request.
6. **Non-enumeration.** Denial and outage paths return the same shape for a resource that exists
   and one that does not, so existence cannot be inferred.
7. **CA1 regression.** The CA1 suite still passes unchanged: a denial for one principal still
   does not reduce another principal's retrieval, and order independence still holds.

## Evidence and gates

- Exact-head `OpenJM CI` green with `Backend / Python 3.11` and `Frontend / Node 22`.
- Focused connector suites plus the scale tests above, with before/after numbers.
- Its own review and its own human merge approval. CA2 is a separate package with separate
  gates; CA1 acceptance does not transfer.
- No GLM supervisory requirement is created by this plan.
- Bounded work packages, each independently reviewable. This plan does not authorize
  implementation.

## Open questions for the owner

1. **Is inverting the flow acceptable?** Option B changes the gate's contract from "return the
   set of authorized documents" to "decide these candidates". That is a deliberate interface
   change and needs a decision, not an assumption.
2. **What should happen when the budget is reached?** Serving the authorized subset and
   recording the truncation fits the stated goal, since omission is the defect. Failing the whole
   request is stricter. This is a product call.
3. **Does the `connector.search` 200 bound belong in CA2** or in its own package? It is the same
   class of defect in a different surface.
4. **What is the deterministic ordering key?** Resource id, `last_synced_at`, or relevance score
   from retrieval. The key determines which items are dropped if a bound ever bites.
5. **What provider-call budget is acceptable per request** for the four retrieval paths, given
   they have different latency expectations (chat versus a scheduled report)?
