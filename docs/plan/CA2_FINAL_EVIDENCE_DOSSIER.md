# CA2 final evidence dossier

**Status: PROVISIONAL, ready for independent final acceptance review. NOT merged, NOT activated.**

Repository: `sjevans1/OpenJM-Enterprise-AI`
Baseline when CA2 began: `main@8d6b7d8`. Current `main`: `709ce47` (reconciled; see B).

| Package | PR | Branch | Head | Base | State |
| --- | --- | --- | --- | --- | --- |
| CA2-A contract | #94 | `plan/ca2-retrieval-completeness` | `8557487` | `main` | draft, experimentally accepted |
| CA2-B integration | #98 | `feat/ca2-b-candidate-integration` | `21294d9` (merge of main) | PR #94 branch | draft |
| CA2-C search | #100 | `feat/ca2c-search-completeness` | `1125427` | `main` | draft |

CA2-C is based on current main and needs no reconciliation. CA2-B carries a clean merge of
`main@709ce47`, so the stack is reconciled.

---

## A. Executive summary

**Intended fix.** Two independent silent-omission defects.

1. The Knowledge candidate set is built through one seam, `governed_tenant_documents`, which
   intersects a corpus-wide connector authorization gate that stops at 100 checks without ordering.
   At 150 authorized resources the legacy path reached **100, never examined 50, and recorded zero
   denials for the 50**, so authorized evidence was silently absent.
2. `connector.search` applied `.limit(200)` with no match predicate and no ordering, then matched in
   Python, so the window bounded the *table* rather than the matches. A metadata match beyond
   position 200 was permanently invisible with no signal of omission.

**Result, measured.** At 150 resources the candidate path reaches **150 of 150** where legacy
reaches 100, and **live retrieval from a real index cites the document at index 115** whose marker
exists only in its body, which legacy cannot reach at all. `connector.search` now finds a match at
rank 281 and reports incompleteness truthfully.

**The live gate is now CLOSED** (it was BLOCKED). Real DB-GPT, real Chroma and the real configured
embedding model were used in-process, with nothing stubbed in the retrieval path. 13 live checks,
0 failures.

**Performance.** The candidate p95 is **1.03x-1.05x** legacy on comparable fixture work against an
unchanged **1.25x** threshold, passing locally and in CI, and the live retrieval step measures
**1.138x** for 20% more collections examined.

---

## B. Implementation inventory

CA2-A (`8557487`, PR #94): `backend/app/services/connectors/candidate_authorization.py`,
`backend/tests/test_ca2_candidate_authorization.py`,
`backend/tests/test_ca2_baseline_measurement.py`, `docs/plan/CA2_RETRIEVAL_COMPLETENESS.md`,
`docs/plan/CA2_BASELINE_AND_TRACEABILITY.md`.

CA2-B (PR #98): `backend/app/services/retrieval_candidates.py` (integration seam),
`backend/tests/test_ca2b_candidate_integration.py`, `backend/tests/test_ca2_measurement_harness.py`,
`backend/tests/live/test_ca2_live_retrieval.py`, `backend/tests/live/README.md`,
`docs/plan/CA2_B_CONSUMER_MAP.md`, with additive edits to `app/core/config.py`,
`app/core/context.py`, `app/services/document_policy.py`, `app/services/tools.py`,
`app/services/orchestrator.py`, `app/api/knowledge.py`, `app/api/chat.py`, `app/schemas.py`, and a
batched-read optimization inside the CA2-A module.

CA2-C (PR #100): `backend/app/services/actions/connector_tools.py`,
`backend/tests/test_ca2c_connector_search.py`.

Reconciliation: `21294d9` merges `main@709ce47` into the CA2-B branch. The 17 incoming commits touch
deployment, installer, compose and documentation files only. The merge is clean, main is untouched,
and the merge lands on the CA2-B branch alone.

---

## C. Before and after

### Fixture, in-process, identical fixture for both paths

| Scenario | Legacy | Candidate | Note |
| --- | --- | --- | --- |
| 40 / 80 / 100 resources | complete parity | complete parity | existing workflows preserved |
| 150 resources | **100** | **150** | the 50 omitted are recovered |
| 400 resources, default 256 cap | **100** | **256**, partial, continuation advertised | not 400 in one request |
| 400 resources, diagnostic at MAX cap 1024 | 100 | **400** | labelled diagnostic, not the default |
| Mixed 150 connector + 5 native | **105** | **155** | native documents unaffected |
| Pinned 75 sources | n/a | 75 authorised | strict exact-source revalidation |
| `connector.search`, match at rank 281 | invisible | returned | CA2-C |

### Live, real Chroma and real embeddings, 120 synthetic documents

| Check | Result |
| --- | --- |
| candidate reaches every authorized document | 120 of 120 |
| real retrieval cites the beyond-window document at index 115 | **PASS** |
| retrieved passage is real indexed content | **PASS** |
| legacy reaches exactly 100 and cannot cite it | **PASS** |
| an indexed document withheld from the refs is never returned | **PASS** |
| revoked resource leaves the candidate set; authoritative scope refuses it; its content stops being cited | **PASS** |
| partial coverage reported with a leak-free notice | **PASS** |
| tenant isolation against the real index | **PASS** |
| rollback restores the 100-document legacy window | **PASS** |

---

## D. Acceptance scorecard

| Requirement | Verdict | Evidence |
| --- | --- | --- |
| No silent omission of authorized resources | **PASS** | 150: 150 vs 100; live 120 of 120 |
| Real indexed retrieval beyond the legacy window | **PASS** | live check, index 115 cited |
| Beyond position 256 reachable by continuation | **PASS** | 300 resource walk |
| Truthful partial coverage, no leaked detail | **PASS** | fixed prose per reason; live notice check |
| Nonempty partial result does not look exhaustive | **PASS** | catalog answer appends the notice |
| Empty partial result is not proof of absence | **PASS** | answer text plus `coverage_notice` |
| Coverage on the user-visible response | **PASS** | `ChatResponse.coverage_notice`, `ExecutionPlan.coverage_notice` |
| Coverage notice cannot leak across requests | **PASS** | reset in the chat handler plus a test |
| `GET /documents` list contract preserved | **PASS** | response model asserted; additive header only |
| Additive paginated catalog, non-duplicating | **PASS** | native first-page-only, `native_included` |
| Cursor refusal is a 4xx, not a 500 | **PASS** | route converts `CursorError` |
| Budget ceiling cannot be bypassed | **PASS** | enforced in the service AND at the API |
| One traversal per logical request | **PASS** | Knowledge and Hybrid, provider-instrumented |
| Strict exact-source for authoritative scopes | **PASS** | 75 pinned; live revocation refusal |
| Revocation honoured at consumption | **PASS** | strict scope raises; live content stops being cited |
| Tenant and principal isolation | **PASS** | live cross-tenant yields nothing, zero provider calls |
| Provider outage fails closed and reports | **PASS** | `authorization_unavailable` plus notice |
| Cursor confidentiality and tamper resistance | **PASS** | AEAD; tamper, principal, query-change refused |
| Rollback restores legacy behaviour | **PASS** | fixture and live |
| p95 within 1.25x on comparable work | **PASS** | 1.03x / 1.05x locally and in CI; live retrieval 1.138x |
| Zero accepted functional regressions | **PASS after two repairs** | see H |
| `connector.search` match beyond 200 | **PASS (CA2-C)** | rank 281 returned |
| `connector.search` truthful incompleteness | **PASS (CA2-C)** | capped and truncated cases |
| `connector.search` Unicode matching | **PASS (CA2-C)** | uppercase non-ASCII title found |
| Live DB-GPT/Chroma verification | **PASS** | 13 checks, 0 failures |
| Merge | **NOT DONE** | owner hold binding |

---

## E. Security assessment

All passing, on fixture evidence and, for the retrieval path, on live evidence: tenant and principal
isolation, current provider authorization, denial-versus-unavailable semantics, revocation at
evidence consumption, classification and group access, cursor confidentiality and tamper resistance
(AEAD, bound to tenant, principal, query and scope), strict exact-source authorization, no partial
authoritative reports, and CA1 non-mutation.

Live-specific: an indexed document withheld from the authorized refs is **not returned by real
retrieval**, which is the security property at the centre of this programme demonstrated against a
real index rather than a stub. No information leakage was found through notices, errors or metadata.

---

## F. Compatibility assessment

| Capability | Status |
| --- | --- |
| General Chat, Data, Hybrid routing | unchanged |
| Knowledge retrieval and evidence integrity | unchanged; the integrity gate retained |
| `GET /documents` | body and response model unchanged; additive header only |
| Pinned reports, ordering, fail-closed | preserved; ordering held at `created_at DESC` |
| Native document policy semantics | preserved |
| `connector.search` response keys | preserved; fields added only |
| `retrieval_candidate_mode = legacy` | byte-identical in the paths tested, fixture and live |

**One deliberate behaviour change, disclosed:** the catalog-pattern answer no longer claims absence
when coverage is partial, and appends a notice to a nonempty partial list.

**Two regressions found and repaired**, both mine, both caught by running wider selections: see H.

---

## G. Performance and economics

### Fixture, comparable work, 100 resources, 15 samples, warmup excluded, paths interleaved

| Stage | legacy p50 / p95 | candidate p50 / p95 | ratio p50 / p95 | SQL |
| --- | --- | --- | --- | --- |
| before optimization | 526.39 / 798.84 | 675.90 / 1141.96 | 1.284 / **1.430 FAIL** | 303 vs 410 |
| after, run 1 | 421.96 / 543.80 | 420.61 / 528.81 | 1.003 / **1.028 PASS** | 303 vs 314 |
| after, run 2 | 653.74 / 823.50 | 629.59 / 862.59 | 0.963 / **1.047 PASS** | 303 vs 314 |
| CI, GitHub runner | - | - | **PASS** | - |

### Live, real Chroma and embeddings, 120 documents

| Metric | Value |
| --- | --- |
| ingest | 32.2 s total, 268.6 ms per document |
| Chroma vector directory | 20.7 MB for 120 per-document collections |
| legacy query (100 refs) | 2546.7 ms |
| candidate query (120 refs) | 2898.8 ms |
| live retrieval latency ratio | **1.138x**, inside 1.25x |
| embedding model cache on disk | 174.7 MB |
| dependencies | dbgpt 0.8.2, dbgpt-ext 0.8.2, chromadb 1.5.9, sentence-transformers 6.1.0, torch 2.14.1 |

**Live capacity finding, and it matters commercially.** There is one Chroma collection per document,
so a live Knowledge query costs roughly **20-25 ms per authorized collection examined**. At 100-120
documents that is 2.5-2.9 s per query; at the default 256-check cap on a larger corpus it would
approach **6-7 s**. The authorization overhead is not the driver: the cost is proportional to the
number of collections the retrieval step must visit, which is real work the legacy window avoided
only by omitting documents. This is the cost of completeness, it is not a threshold breach, and it
is not fixable without an architectural change that is out of scope for CA2.

**Provider calls.** Equal at 100 resources; 150 vs 100 at 150 resources, buying the 50 resources
legacy never examines. Unit pricing is not available to the harness, so **no currency estimate is
fabricated**.

---

## H. Test and CI evidence

Local, at the heads above:

| Suite | Result |
| --- | --- |
| CA2-A contract | 78 passed |
| CA2-B integration plus measurement harness | 35 passed |
| CA2-C search | 15 passed |
| Live retrieval harness | 13 checks, 0 failures |
| Regression selection, CA2-B touched seams | 182 passed |
| Regression selection, CA2-C touched area | 165 passed |
| Regression selection, chat/api/orchestrator | 113 passed |
| `pytest tests/ --collect-only` | 1494 collected, no errors |
| ruff `E9,F63,F7,F82` | clean |

CI, `OpenJM CI` workflow:

| Head | Run | Result |
| --- | --- | --- |
| CA2-A `8557487` | `38220473733` | **success** |
| CA2-B `01041c3` | `38220103969` | **success** |
| CA2-B `978a2b1` | `38225750805` | **failure** (see below) |
| CA2-B `63f5f1e` | `38226882796` | fix for the above |
| CA2-C `1125427` | pending | the Unicode correction |

**Regression 1, caught by CI.** CA2-B `46a4b64` failed with `2 failed, 1488 passed`. Changing
`_ready_documents` from returning a `list` to a `tuple` broke two `test_vs5_tenant_isolation.py`
tests with `'list' object has no attribute 'id'`. Repaired by moving the CA2-B work to a new
`_ready_documents_with_context` and leaving the original contract intact.

**Regression 2, caught by a wider local selection.** Adding `tests/live/conftest.py` shadowed
`tests/conftest.py` for any module doing a bare `import conftest` (the report-run suites do), so
seven modules failed to collect. CI at `978a2b1` failed for this reason. Repaired by removing that
conftest and gating the live module with a module-level `skipif` instead.

Both were mine, both are understood, and neither survives in the current head.

---

## I. Rollback

Tested, fixture and live: setting `retrieval_candidate_mode` to `legacy` restores the 100-resource
window, `coverage = None`, no notice, zero candidate provider calls, and with the real index in
place it returns the same 100 documents. No migration, reindexing or irreversible state change.

CA2-C needs no flag: it is a correctness fix inside the existing tool, and reverting the commit
restores the previous behaviour.

---

## J. Outstanding issues and limitations

1. **The live stack is in-process, not a separate service.** DB-GPT is used as a library with a
   local persistent Chroma. That is the real configured retrieval path, but it is not a
   multi-process deployment with PostgreSQL and Keycloak in front. Those were not needed for the
   retrieval path and were not stood up.
2. **Live capacity.** 2.5-2.9 s per Knowledge query at 100-120 documents, driven by one Chroma
   collection per document, approaching 6-7 s at the default cap on a larger corpus. See G.
3. **`connector.search` scans rather than seeks**: `LIKE '%x%'` cannot use a btree index. Bounded in
   rows returned, not rows examined, and not measured at production scale.
4. **`connector.search` has no cursor continuation**; incompleteness is reported instead, which the
   mandate permits for this slice.
5. **CA2-B's diff touches the CA2-A module** for the batched-read optimization. No contract element
   changed and CA2-A's 78 tests pass, but CA2-A is the accepted artifact, so this is flagged.
6. **The live harness is excluded from CI** by design and runs only on request (`CA2_LIVE=1`), so
   regressions in it would not be caught by CI.
7. **CI for CA2-C `1125427` and the reconciled CA2-B head `21294d9`** was in flight at the time of
   writing; the table above records what had concluded.
8. **No frontend changes** were required, so the frontend job is a no-op pass.

---

## K. Final recommendation

**ACCEPT FOR OWNER MERGE REVIEW.**

Every engineering gate is now met with evidence, including the one that was previously BLOCKED:

- both silent-omission defects are fixed and measured, at fixture and live level;
- the live gate is closed with real DB-GPT, real Chroma and the real embedding model, 13 checks and
  0 failures, including real semantic retrieval of a body-only-relevant document beyond the legacy
  window and the live proof that evidence cannot come from outside the authorized refs;
- coverage is truthful and reaches the user-visible response, with request-scoped hygiene;
- authoritative scopes are exact, and revocation is honoured at consumption, live;
- the single-traversal property is proven at request level for Knowledge and Hybrid;
- the 1.25x performance gate passes on comparable work, locally and in CI, and the live retrieval
  step measures 1.138x;
- rollback is tested rather than documented;
- the stack is reconciled against current main with a clean merge;
- the two regressions introduced during this work were caught, understood and repaired, and are
  recorded rather than hidden.

The one thing a reviewer should weigh before merging is **limitation 2**: the live capacity cost of
completeness. Covering every authorized document means visiting every authorized document's
collection, which is roughly 20-25 ms each at 100-120 documents. That is the price of the fix, it is
within the agreed threshold, and it is not a regression against legacy work of the same size, but it
is a real operating consideration and it should be an informed owner decision rather than a
surprise.

**A recommendation is not permission to merge.** Nothing here is merged or activated.
