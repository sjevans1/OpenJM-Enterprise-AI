# CA2 — Bounded, query-relevant connector retrieval with no silent omission

Plan revision: `ca2-retrieval-3` (supersedes `ca2-retrieval-2`)
Repository: **sjevans1/OpenJM-Enterprise-AI only**
Base: `main` at `8d6b7d8`
Depends on: CA1 (accepted, merged `a4e1e4f`)
Status: **CA2-A contract-only implementation authorized. No merge authorized.**
Binding records: the owner integration hold, D1-D5 architecture decisions, and the Phase 1
independent plan review on PR #94.

## 0. Integration hold (binding)

**Nothing in this stream merges to `main`.** Planning PR #94, every implementation PR, and any
market-research-derived product change stay unmerged until the owner gives explicit,
head-specific authorization after reviewing comparative evidence. A draft-PR merge, a green CI
run, a completed package or a model recommendation is not authorization.

Also binding, from the hold:

1. **No wholesale rebuild.** Preserve the accepted Chroma/DB-GPT retrieval stack, model routing,
   Rahkia boundary, tenancy, and existing interfaces and schemas. Any replatform needs its own
   separately authorized architecture decision. Prefer bounded adapters and contract changes.
2. **No reduction in accepted functionality.** A frozen current-main baseline and behaviour
   matrix must cover Chat, Knowledge, Data, Hybrid, native and connector evidence,
   ACL/classification, revocation, outage fail-closed, CA1 cross-user availability, catalogs and
   pagination, `connector.search` and `connector.fetch`, actions/tools, report
   snapshots/reruns/exports, neighbour context, dedup and provenance, tenant isolation, audit,
   and client-hosted and OpenJM-managed deployments.
3. **Measurable, material improvement** on matched fixtures, with tolerances declared before
   measurement.
4. **Reversible and isolated.** Keep the old path until acceptance; provide a tested
   disable/rollback method; prove compatibility with the latest `main`, not only the plan base.
5. **Owner go/no-go** on a scorecard, regression matrix, exact-head CI, independent review,
   explicit risks and rollback evidence.

## 1. Objective

Remove the possibility that an authorized connector resource is silently omitted from retrieval,
catalog listing or search because it falls outside an arbitrary candidate window, while keeping
authorization fail-closed, bounded and leak-free.

This is a controlled enhancement to the existing product. It is not a rebuild, migration or
replacement of the established architecture.

## 2. Phase 1 seam map (read from the code, not inferred)

Read from `main` at `8d6b7d8`. This section is the finding that shapes the whole plan.

### 2.1 Retrieval is per-document, so candidates cannot be discovered from an index

`DBGPTKnowledgeEngine` gives every document **its own Chroma collection**:

    knowledge.py:75-83   _collection_name(document_id) -> f"{settings.vector_collection}_{safe}"

and retrieval iterates a caller-supplied document list, querying one collection per document:

    knowledge.py:150-172
        async def retrieve(self, query, documents: list[tuple[str, str]], ...):
            for document_id, title in documents:
                retriever = EmbeddingRetriever(top_k=..., index_store=self._store(document_id))

There is no corpus-wide index. **Relevance-first corpus-wide candidate discovery is therefore not
possible without replacing the per-document collection design, which the hold forbids.** Ranking
today is per-document and merged afterwards, so the candidate set must be enumerated from
metadata.

This is the decisive constraint, and it is why the `ca2-retrieval-1` recommendation to move
authorization after retrieval is withdrawn: there is nothing to retrieve from until the
candidates are known.

### 2.2 The candidate set is already enumerated in full; only authorization is truncated

    document_policy.py:169-216  governed_tenant_documents(db, access)
        rows = select(Document).where(tenant_id, *retrievable_filter())
                              .order_by(Document.created_at.desc())      # NO limit
        connector_origin = await connector_origin_document_ids(...)
        authorized_connector_ids = await authorized_connector_document_ids_for_context(db)
        for document in rows:
            if not document_is_visible(access, document): continue
            if document.id in connector_origin and document.id not in authorized_connector_ids:
                continue                                                  # <- the omission
            selected.append(document)

So:

- candidate discovery is **already** document-id/metadata based and **unbounded**;
- the local policy gate `document_is_visible` is complete, unbounded and fail-closed
  (classification, department, `tenant_visible`, groups);
- the **only** truncation is inside `authorized_connector_document_ids`, bounded by
  `MAX_AUTHORIZATION_CHECKS = 100` (`authorization.py:67`, applied at `:362`, default at `:340`),
  whose select has **no ordering**;
- an unexamined connector candidate is then treated as **denied** at the intersection above.

**The defect is therefore: unexamined is treated as denied, and the truncation is invisible.**
Raising the number changes the size of the omission, not its nature.

### 2.3 Blast radius

| Path | Entry | Effect of the bound |
| --- | --- | --- |
| Chat / Knowledge / Hybrid | `orchestrator.py:202` -> `governed_tenant_documents` | Authorized connector evidence beyond the window is absent from answers |
| Catalog listing | `api/knowledge.py:79` -> `governed_tenant_documents` | An authorized document is absent from the catalog, so discovery is wrong |
| Tool execution | `tools.py:283` -> `governed_tenant_documents` | Same omission inside tool results |
| Reports (pinned) | `api/reports.py:224` -> gate directly | A report over an incomplete authorization set can produce an incomplete artifact |

The catalog case matters as much as retrieval: a document that exists and is authorized is **not
listed**, which is a discovery failure rather than an answer failure.

### 2.4 Revised design conclusion

Do **not** invert the flow. Do **not** touch embeddings, Chroma, model routing or identity.

Keep candidate discovery where it is and repair the authorization step:

1. **Candidate-scoped authorization with an explicit candidate list.** Replace corpus-wide
   enumeration with an interface that accepts the candidates plus a declared budget, so the
   caller's already-complete candidate set drives the checks.
2. **Deterministic ordering.** The select must be ordered and the order stable. Since no global
   relevance score exists before retrieval, order by stable identity (`connector_instance_id`,
   `external_id`, `document_id`).
   **D4 decision: accepted.** The review resolved this explicitly: per-document Chroma
   collections have no global pre-retrieval relevance score, so stable-identity ordering is the
   deterministic baseline and no global index will be built to satisfy an ordering preference.
   For explicit `connector.search`, title/external-ID matching belongs in the database candidate
   selection **before** any page limit. For natural-language Knowledge / Chat / Hybrid, a
   title/external-ID match may only **prioritise** candidates; it must never be a hard exclusion
   of semantically relevant content.
3. **Explicit coverage outcome.** The gate returns the authorized set **and** whether coverage is
   complete or partial, with a typed reason. Exhaustion is never silent.
4. **Split semantics by request class (D2).** Exploratory requests may return a proven-authorized
   subset marked `coverage=partial`. Authoritative requests fail closed with a typed
   `authorization_incomplete` outcome and no immutable snapshot.
5. **Never claim absence.** A truncated search must not report that no documents exist.
6. **Safe continuation.** Stable ordering alone does not make items after the first window
   discoverable. A progressive-scanning strategy is required so a candidate beyond the initial
   authorization window remains reachable on a subsequent request even when its title does not
   match the query. This is the core of the CA2-A contract, not a refinement of it.

This is the "focused two-phase metadata prefilter and candidate authorization solution" the owner
permitted as the fallback. The seam analysis shows it is the correct primary approach, not a
fallback.

## 3. Budgets (D5) — EXPERIMENTAL TARGETS, NOT DEFAULTS

**Critical baseline-parity correction from the Phase 1 review.** The current production path may
revalidate up to 100 resources per request. The D5 pilot figures of 32 (chat/search) and 64
(reports) are **lower** than that. Enforcing them as new defaults would make previously
accessible documents disappear from ordinary queries and would break pinned report runs
involving 65 to 100 resources, which violates the no-functional-regression hold.

Therefore:

- 32 and 64 are **experimental configurations for benchmark and tuning**, not accepted defaults
  and not product limits.
- The existing production behaviour (up to 100) remains active until matched evidence proves
  that a safe candidate-selection and continuation strategy maintains or improves existing
  successful flows.
- If that cannot be proved, stop and revise rather than reducing coverage.
- Measured baseline to compare against: **exactly 100 provider checks consumed** on a 150-item
  all-authorized corpus, with 50 authorized documents omitted (see the baseline document).

| Path | Experimental target | Concurrency | Deadline |
| --- | --- | --- | --- |
| Chat / Knowledge / Hybrid | 32 checks per user request, shared across sub-queries | max 4 | ~5s |
| `connector.search` | 32 checks per call | max 4 | interactive |
| Exact named `connector.fetch` / authorization | 1 | 1 | interactive |
| Pinned / scheduled report | 64 checks | bounded | ~30s, failing explicitly if exceeded |
| **Current production behaviour** | **100** (do not regress) | current | current |

## 4. Invariants

1. A candidate may be identified internally by a trusted retrieval process, but its content and
   protected metadata may not reach the model, user, tool output, report, artifact or
   unauthorized metadata surface until all required authorization checks have passed.
2. Fail-closed on any failure to prove authorization.
3. No silent omission; truncation is reported.
4. Deterministic: the same corpus, permissions and question produce the same result set and the
   same coverage decision.
5. Non-enumerating: denials and outages never reveal names, counts or provider details.
6. Bounded provider calls per request.
7. CA1 preserved: authorization never mutates shared content; content-scoped withdrawal stays an
   explicit lifecycle operation.
8. No loss of any accepted capability.

## 5. Packages

Adjustable if the code indicates a smaller, safer division.

**CA2-A — Candidate discovery, authorization contract, bounded execution.**
The candidate-scoped authorization interface, deterministic ordering, coverage outcome,
per-request deduplication, budget enforcement and typed results. No caller changes. Unit-testable
in isolation, and the slice that carries the contract change.

**CA2-B — Integration with Knowledge, Chat, Hybrid, catalog and authoritative report paths.**
`governed_tenant_documents` and the report path consume the new contract, propagate
`coverage=partial` into exploratory responses, fail closed with `authorization_incomplete` for
pinned reports, and leave `document_is_visible` exactly as it is.

**CA2-C — `connector.search` query filtering, deterministic ordering and pagination (D3).**
Apply title/external-id filtering **before** the limit, replace the `statement.limit(200)`
behaviour (`connector_tools.py:137`) with deterministic ordering and truthful pagination, and
expose `has_more`. Separately reviewable.

Each package: own branch, own worktree, own exact-head CI, own review, own human merge approval.
Draft PRs only. Keep the existing retrieval behaviour available for baseline comparison and
provide a tested rollback.

## 6. Phase 4 — measured comparison

Same fixtures and comparable runtime, baseline versus candidate. At least 150 authorized connector
resources, proving resources beyond the original first-100 and first-200 windows remain reachable
through relevant queries or truthful pagination. A single interactive query is not required to
authorize all 150.

Report per dimension: retrieval completeness (reachable authorized sources, omissions, coverage);
answer correctness; security (unauthorized exposure, tenant-isolation regressions); functional
parity against the frozen matrix; latency p50/p95 interactive and report; provider usage (checks,
retries, timeouts, concurrency); CPU/memory/GPU; operating economics; reliability (outage,
recovery, determinism); reversibility (demonstrated rollback).

Tolerances declared before measurement. Passing existing CI is mandatory and not sufficient.

## 7. Phase 5 — integration decision

One decision report: baseline and candidate commit hashes; architecture changes and affected
interfaces; functional parity matrix; security regression evidence; baseline-versus-candidate
benchmark table; confirmed improvements quantified; performance/capacity/cost trade-offs; rollback
evidence; unresolved regressions or limitations; and a recommendation of **ACCEPT, REVISE or
REJECT**. ACCEPT does not authorize merging.

If there is any material functional regression, unauthorized exposure, unapproved rebuild,
unbounded cost increase or unproven improvement, recommend REVISE or REJECT.

## 8. Stop conditions

- If the invariant in section 4 cannot be proven, stop and recommend a narrower approach.
- If meaningful improvement requires the replatform the hold forbids, stop.
- If a material regression appears, stop the candidate.

## 9. Accepted constraints

- GLM supervision is not a gate for this project and is not introduced here.
- No ownership of BV, REL, INF, M, OPS or other active packages. Reconcile with the latest
  accepted `main` before final evaluation.
- Framework PR #7 is a separate controller-hardening package and is not part of CA2.

## 10. CA2-A contract (authorized: contract-only, isolated, draft)

Authorization: CA2-A contract-only implementation in an isolated worktree is permitted now that
this plan reflects the Phase 1 review. It is **prototype authorization only**. No merge. CA2-B
and CA2-C must not be started until the contract and coverage strategy have been independently
reviewed.

### 10.1 Candidate state model

Four distinct internal states. Conflating any two of these is the current defect, so the contract
names them explicitly:

| State | Meaning | May be returned as evidence? |
| --- | --- | --- |
| `authorized` | Current permission proven for this principal | Yes |
| `denied` | Provider proved this principal lacks access | No, and it is not an error |
| `unexamined` | Not reached: budget exhausted or continuation pending | No, and it must never be reported as denied |
| `unavailable` | Permission could not be proven: timeout, outage, rate limit, contradictory | No, fail closed |

Only a coarse coverage state is exposed to a user: `complete` or `partial`. Never unauthorized
counts, titles, identifiers or provider detail.

### 10.2 Contract requirements

1. **Local eligibility before provider budget.** `document_is_visible` and tenant/source
   eligibility must be applied before any provider check is consumed. (The ordering inside
   `governed_tenant_documents`, which currently calls the corpus-wide provider gate before
   iterating locally visible documents, changes only in CA2-B, after independent verification.)
2. **Candidate-scoped authorization.** Direct named-resource and pinned-scope checks authorize
   the specifically requested resource ids rather than enumerating the corpus.
3. **Determinism.** Stable-identity ordering with a declared sort key, so repeated identical
   requests produce identical outcomes.
4. **Coverage reporting.** Every result carries `complete` or `partial` plus a typed reason.
   Exhaustion must never masquerade as a complete answer, and a truncated search must never claim
   that no documents exist.
5. **Per-request deduplication.** Identical provider checks within one request are performed once.
   No cross-request allow caching without a separate security review.
6. **Safe continuation.** A progressive-scanning cursor so that candidates beyond the first
   authorization window remain reachable on later requests, including candidates whose titles do
   not match the query. Stable ordering alone does not achieve this.
7. **No replatform.** The contract must not require changes to Chroma, DB-GPT, embeddings, model
   routing, identity or the Rahkia boundary. It is an adapter and contract change only.
8. **Baseline preserved.** The existing production path stays active and unmodified as the
   comparison path, and the new contract is exercised only through its own tests.
9. **No leakage.** Tests must prove that no secret, denied title, denied identifier or denied
   count reaches retrieval, model, tool, report or catalog output.

### 10.3 CA2-A exclusions

- No caller changes: `governed_tenant_documents`, the orchestrator, tools, reports and the
  catalog are untouched in CA2-A and migrated in CA2-B.
- No endpoint or response-shape change. `GET /documents` must keep returning a list, or an
  explicitly compatible versioned/paginated surface must be introduced in CA2-B. Changing a list
  to an object would itself be a regression.
- No default budget change. 32 and 64 remain experimental configuration values.
- No `connector.search` change; that is CA2-C.

### 10.4 Benchmark refinements accepted

- The 16-row matrix is a coverage checklist, not a substitute for an end-to-end test per row.
- Include an adversarial scenario where semantic relevance exists only in the document body, with
  an unrelated title, at an index beyond 100. Baseline confirms this is currently unreachable.
- Test existing successful queries with 33 to 100 eligible resources, and pinned reports with 65
  to 100, since those are the shapes the D5 budgets would have broken.
- Latency measurement with representative provider delay and timeout, not only the fast
  in-process fake.
- Report provider-call, compute and economic cost deltas, not merely the absence of a new paid
  dependency.
