# CA2 final evidence dossier

**Status: PROVISIONAL. Awaiting the owner's independent review. NOT merged, NOT activated.**

Repository: `sjevans1/OpenJM-Enterprise-AI`
Baseline when CA2 began: `main@8d6b7d8`. Current `main`: `bc0c458` (advanced by unrelated workstreams).

| Package | PR | Branch | Head | Base | State |
| --- | --- | --- | --- | --- | --- |
| CA2-A contract | #94 | `plan/ca2-retrieval-completeness` | `8557487` | `main` | draft, experimentally accepted |
| CA2-B integration | #98 | `feat/ca2-b-candidate-integration` | `6d532eb` | PR #94 branch | draft |
| CA2-C search | #100 | `feat/ca2c-search-completeness` | `9d30203` | `main` | draft, **CI green** |

---

## A. Executive summary

**Intended fix.** Two independent silent-omission defects.

1. The Knowledge candidate set is built through one seam, `governed_tenant_documents`, which
   intersects a corpus-wide connector authorization gate that stops at 100 checks without ordering.
   Measured on the frozen baseline at 150 authorized resources: **100 reachable, 50 never examined,
   zero denials recorded for the 50**, so authorized evidence was silently absent.
2. `connector.search` applied `.limit(200)` with no match predicate and no ordering, then matched in
   Python, so the window bounded the *table* rather than the matches. A metadata match beyond
   position 200 was permanently invisible, with no signal of omission.

**Measured result.** At 150 resources the candidate path reaches **150 of 150** where legacy reaches
**100**, recovering exactly the 50 omitted, and retrieves and cites a body-only-relevant source past
index 100 that legacy cannot reach at all. `connector.search` now finds a match at rank 281 and
reports incompleteness truthfully.

**Performance.** After one evidence-driven optimization the candidate p95 is **1.03x-1.05x** legacy
on comparable work against an unchanged **1.25x** threshold (from 1.43x before), and the gate
**passed on the GitHub runner**, not only locally.

**Open.** Live DB-GPT/Chroma verification is BLOCKED. See J.

---

## B. Implementation inventory

CA2-A (`8557487`, PR #94): `backend/app/services/connectors/candidate_authorization.py`,
`backend/tests/test_ca2_candidate_authorization.py`, `backend/tests/test_ca2_baseline_measurement.py`,
`docs/plan/CA2_RETRIEVAL_COMPLETENESS.md`, `docs/plan/CA2_BASELINE_AND_TRACEABILITY.md`.

CA2-B (PR #98): `backend/app/services/retrieval_candidates.py` (integration seam),
`backend/tests/test_ca2b_candidate_integration.py`, `backend/tests/test_ca2_measurement_harness.py`,
`docs/plan/CA2_B_CONSUMER_MAP.md`, plus additive edits to `app/core/config.py`,
`app/core/context.py`, `app/services/document_policy.py`, `app/services/tools.py`,
`app/services/orchestrator.py`, `app/api/knowledge.py`, `app/api/chat.py`, `app/schemas.py`, and a
batched-read optimization inside the CA2-A module.

CA2-C (PR #100): `backend/app/services/actions/connector_tools.py`,
`backend/tests/test_ca2c_connector_search.py`.

Affected consumers: Knowledge/Chat/Hybrid retrieval, `knowledge.search`, the document catalog, pinned
report scope preflight, and `connector.search`. Legacy behaviour is unchanged when
`retrieval_candidate_mode = legacy`, which remains the default.

---

## C. Before and after (fixture, in-process, same instance both paths)

| Scenario | Legacy | Candidate | Note |
| --- | --- | --- | --- |
| 40 / 80 / 100 resources | complete parity | complete parity | existing workflows preserved |
| 150 resources | **100** reachable | **150** | the 50 omitted are recovered |
| 400 resources, default 256 cap | **100** | **256**, partial, continuation advertised | not 400 in one request |
| 400 resources, diagnostic at MAX cap 1024 | 100 | **400** | labelled diagnostic, not the default |
| Mixed 150 connector + 5 native | **105** | **155** | native documents unaffected |
| Body-only relevant source past index 100 | not reachable | retrieved and cited | via the real tool path |
| Source beyond position 256 | not reachable | reachable by continuation | 300 resource walk |
| Pinned 75 sources | n/a | 75 authorised | strict exact-source revalidation |
| `connector.search`, match at rank 281 | invisible | returned | CA2-C |

**Provider calls per request.** Knowledge and Hybrid each perform **exactly one** exploratory
traversal (150 checks for 150 resources) against a 256 per-request cap; before the handoff wiring it
was 2 traversals and 200 calls. At 100 resources legacy and candidate each make **100** provider
calls, so at that size the candidate adds no provider work.

**SQL and database time per request at 100 resources:** 303 legacy vs **314** candidate (was 410) and
199 ms vs **221 ms** (was 365 ms).

---

## D. Acceptance scorecard

| Requirement | Verdict | Evidence |
| --- | --- | --- |
| No silent omission of authorized resources | **PASS** | 150: 150 vs 100; 400: continuation |
| Recovered body-relevant evidence past index 100 | **PASS** | tool-level test, cited |
| Beyond position 256 reachable by continuation | **PASS** | 300 resource walk |
| Truthful partial coverage, no leaked detail | **PASS** | fixed prose per reason; no ids or counts |
| Nonempty partial result does not look exhaustive | **PASS** | catalog answer appends the notice |
| Empty partial result is not proof of absence | **PASS** | answer text plus `coverage_notice` |
| Coverage on the user-visible response | **PASS** | `ChatResponse.coverage_notice`, `ExecutionPlan.coverage_notice` |
| `GET /documents` list contract preserved | **PASS** | response model asserted; header added, body untouched |
| Additive paginated catalog, non-duplicating | **PASS** | native first-page-only, `native_included` |
| Cursor refusal is a 4xx, not a 500 | **PASS** | route converts `CursorError` |
| Budget ceiling cannot be bypassed | **PASS** | enforced in the service AND at the API |
| One traversal per logical request | **PASS** | Knowledge and Hybrid, provider-instrumented |
| Strict exact-source for authoritative scopes | **PASS** | 75 pinned; revocation refuses |
| Revocation honoured at consumption | **PASS** | strict scope raises after revoke |
| Tenant and principal isolation | **PASS** | cross-tenant yields nothing, zero provider calls |
| Provider outage fails closed and reports | **PASS** | `authorization_unavailable` plus notice |
| Cursor confidentiality and tamper resistance | **PASS** | AEAD; tamper, principal, query-change refused |
| Rollback restores legacy behaviour | **PASS** | flag flip tested; no migration |
| p95 within 1.25x on comparable work | **PASS** | 1.03x / 1.05x, and green on the GitHub runner |
| Zero accepted functional regressions | **PASS after repair** | CI caught 2; fixed at `6d532eb`; see H |
| `connector.search` match beyond 200 | **PASS (CA2-C)** | rank 281 returned |
| `connector.search` truthful incompleteness | **PASS (CA2-C)** | capped and truncated cases |
| Live DB-GPT/Chroma verification | **BLOCKED** | no non-production stack, see J |
| Full CI on all three heads | **PARTIAL** | CA2-C green; CA2-B re-running at `6d532eb` |
| Merge | **NOT DONE** | owner hold binding |

---

## E. Security assessment

All passing on fixture evidence: tenant and principal isolation, current provider authorization,
denial-versus-unavailable semantics, revocation at evidence consumption, classification and group
access, cursor confidentiality and tamper resistance (AEAD, bound to tenant, principal, query and
scope), strict exact-source authorization, no partial authoritative reports, and CA1 non-mutation (a
denial during search does not change lifecycle or permission state). No information leakage was found
through notices, errors or metadata: notices are fixed application-generated prose keyed by reason,
carrying no counts, identifiers or titles.

---

## F. Compatibility assessment

| Capability | Status |
| --- | --- |
| General Chat, Data, Hybrid routing | unchanged |
| Knowledge retrieval and evidence integrity | unchanged; the integrity gate is retained |
| `GET /documents` | body and response model unchanged; additive header only |
| Pinned reports, ordering, fail-closed | preserved; ordering held at `created_at DESC` |
| Native document policy semantics | preserved |
| `connector.search` response keys | preserved; fields added only |
| `retrieval_candidate_mode = legacy` | byte-identical in the paths tested |

**One deliberate behaviour change, disclosed:** the catalog-pattern answer no longer claims absence
when coverage is partial, and appends a notice to a nonempty partial list.

**One regression found by CI and repaired:** see H.

---

## G. Performance and economics

Comparable work, 100 resources, 15 samples, warmup excluded, paths interleaved with alternating
order, per-request accounting, provider calls counted at the shared adapter:

| Stage | legacy p50 / p95 | candidate p50 / p95 | ratio p50 / p95 | SQL |
| --- | --- | --- | --- | --- |
| before optimization | 526.39 / 798.84 | 675.90 / 1141.96 | 1.284 / **1.430 FAIL** | 303 vs 410 |
| after, run 1 | 421.96 / 543.80 | 420.61 / 528.81 | 1.003 / **1.028 PASS** | 303 vs 314 |
| after, run 2 | 653.74 / 823.50 | 629.59 / 862.59 | 0.963 / **1.047 PASS** | 303 vs 314 |
| CI, GitHub runner | - | - | **PASS** | - |

**Cost.** The cost driver is provider authorization calls, equal at 100 resources and rising with the
corpus because coverage now means what it says: at 150 resources the candidate makes 150 checks where
legacy makes 100, buying the 50 resources legacy never examines. Unit pricing for the provider is
unavailable to this harness, so **no currency estimate is fabricated**. SQL and database time carry a
small residual overhead, about +3.6% statements and +11% database time.

Latency figures are in-process SQLite with a stubbed vector engine. They are decision-grade for
comparing the two paths on identical work, and are **not** production latency figures.

---

## H. Test and CI evidence

Local, at the heads above:

| Suite | Result |
| --- | --- |
| CA2-A contract | 78 passed |
| CA2-B integration plus measurement harness | 34 passed |
| CA2-C search | 13 passed |
| `test_vs5_tenant_isolation.py` | 11 passed |
| Regression selection, CA2-B touched seams | 182 passed |
| Regression selection, CA2-C touched area | 165 passed |
| ruff `E9,F63,F7,F82` | clean |

CI, `OpenJM CI` workflow:

- **CA2-C `9d30203`: run `38218611479`, success.** Frontend and Backend both green.
- **CA2-B `46a4b64`: run `38217916353`, failure** - `2 failed, 1488 passed, 19 subtests passed`. Both
  failures were mine: changing `_ready_documents` from returning a `list` to a `tuple` broke
  `tests/test_vs5_tenant_isolation.py::test_vector_retrieval_input_is_tenant_scoped` and
  `::test_mid_deletion_document_is_not_retrievable` with `'list' object has no attribute 'id'`. The
  CA2-B work moved to a new `_ready_documents_with_context` and the original method keeps its
  contract. **Fixed at `6d532eb`; CI re-running at that head.**
- Earlier CA2-B runs at `e7974fd` and `4af65ae` also failed; intermediate heads were cancelled as
  superseded.
- The 1.25x latency gate **passed on the runner** in that same run, so the optimization is not a
  workstation-only result.

**No live-stack test exists.** The vector engine is stubbed throughout, so retrieval is proven at the
candidate-selection, evidence-integrity and tool-boundary level, not against a live index.

---

## I. Rollback

Tested, not documented: setting `retrieval_candidate_mode` to `legacy` restores the 100-resource
window, `coverage = None`, no notice, and zero candidate provider calls, with no migration,
reindexing or irreversible state change. Asserted in
`test_rollback_to_legacy_restores_previous_behaviour`.

CA2-C needs no flag: it is a correctness fix inside the existing tool, and reverting the commit
restores the previous behaviour.

---

## J. Outstanding issues and limitations

1. **Live DB-GPT/Chroma verification: BLOCKED.** DB-GPT is a separate serving stack with a model
   behind it; Chroma is installable but the engine drives it through DB-GPT's HTTP API. A disposable
   equivalent requires provisioning services and models, which needs owner approval. No fixture
   result is offered as a substitute.
2. **CI for CA2-B at `6d532eb` was re-running** at the time of writing. The CA2-A head has not had a
   fresh CI run in this window.
3. **CA2-B's diff touches the CA2-A module** for the batched-read optimization. No contract element
   changed and CA2-A's 78 tests pass, but CA2-A is the accepted artifact, so this is flagged.
4. **`connector.search` scans rather than seeks**: `LIKE '%x%'` cannot use a btree index, so the query
   examines the tenant's connector rows. Bounded in rows returned, not in rows examined, and not
   measured at production scale.
5. **Non-ASCII case folding** in the CA2-C SQL predicate may differ from Python's; the Python matcher
   is retained as authoritative, so the predicate can only narrow.
6. **`connector.search` has no cursor continuation**; incompleteness is reported instead, which the
   mandate permits for this slice.
7. **No frontend changes** were required or made, so the frontend job is a no-op pass.

---

## K. Final recommendation

**REVISE — one item, then accept for owner merge review.**

The engineering is substantially complete and evidenced: both silent-omission defects are fixed and
measured, coverage is truthful and reaches the user-visible response, authoritative scopes are exact,
the single-traversal property is proven at request level for Knowledge and Hybrid, and the 1.25x
performance gate **passes on comparable work, confirmed by CI** rather than only by fixture runs. The
regression CI caught was mine, is understood, and is repaired.

The remaining item is not engineering: **live DB-GPT/Chroma verification is BLOCKED** on an
environment I am not authorized to provision. Until that is either provided or explicitly waived by
the owner, this package's acceptance rests on fixture evidence with that limitation recorded, and I do
not describe the live gate as passed.

Secondary: confirm the re-run CI at `6d532eb` is green, and obtain a fresh CI run on the CA2-A head.

**A recommendation is not permission to merge.** Nothing here is merged or activated.
