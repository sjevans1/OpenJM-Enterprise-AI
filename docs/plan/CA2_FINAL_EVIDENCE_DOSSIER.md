# CA2 final evidence dossier

Repository: `sjevans1/OpenJM-Enterprise-AI`
Baseline main when CA2 began: `8d6b7d8`. Current main: `390b345` (advanced by other workstreams).
Status: **NOT MERGED. NOT ACTIVATED. Awaiting owner review.**

| Package | PR | Branch | Head | State |
| --- | --- | --- | --- | --- |
| CA2-A contract | #94 | `plan/ca2-retrieval-completeness` | `8557487` | draft, base `main`, experimentally accepted |
| CA2-B integration | #98 | `feat/ca2-b-candidate-integration` | see PR | draft, stacked on #94 |
| CA2-C | not created | - | - | not implemented |

---

## A. Executive summary

**Intended fix.** OpenJM's Knowledge candidate set is built by one seam,
`document_policy.governed_tenant_documents`, which intersects a corpus-wide connector
authorization gate that stops at 100 checks without ordering. Measured on the frozen baseline
(150 authorized resources): **100 reachable, 50 never examined, and zero denials recorded for the
50**, so authorized evidence was silently absent from Knowledge answers.

**Completed.** CA2-A rebuilt candidate authorization as bounded, resumable, namespace-aware
traversal plus an exact-source contract and consumption-time revalidation, with truthful coverage
and guaranteed fail-closed behaviour. CA2-B integrates it behind a flag that defaults to legacy,
adds user-visible coverage reporting, real catalog continuation, strict pinned-scope
authorization, and eliminates a duplicate per-request traversal. A comparative measurement
harness now exists.

**Measurable result (fixture, in-process).** At 150 connector resources: legacy reaches **100**,
candidate reaches **150**, recovering exactly the 50 that were omitted. At 400: legacy **100**,
candidate **400**. Mixed native plus connector: legacy **105**, candidate **155**. A body-only
relevant source past index 100 is now retrieved and cited, which under legacy it is not.

**Not completed.** Phases 1B (response-field propagation), 3 (optimization), 4 (CA2-C), the live
DB-GPT/Chroma smoke test, and full exact-head CI. See section J.

---

## B. Implementation inventory

CA2-A (`8557487`, PR #94) - `backend/app/services/connectors/candidate_authorization.py`,
`backend/tests/test_ca2_candidate_authorization.py`, `backend/tests/test_ca2_baseline_measurement.py`,
`docs/plan/CA2_RETRIEVAL_COMPLETENESS.md`, `docs/plan/CA2_BASELINE_AND_TRACEABILITY.md`.

CA2-B (PR #98) - `backend/app/services/retrieval_candidates.py` (the integration seam),
`backend/tests/test_ca2b_candidate_integration.py`,
`backend/tests/test_ca2_measurement_harness.py`, `docs/plan/CA2_B_CONSUMER_MAP.md`, plus additive
edits to `app/core/config.py`, `app/services/document_policy.py`, `app/services/tools.py`,
`app/services/orchestrator.py`, `app/api/knowledge.py`.

Affected consumers: Knowledge/Chat/Hybrid retrieval, the `knowledge.search` tool, the document
catalog, and pinned report scope preflight. Legacy behaviour is unchanged when
`retrieval_candidate_mode = legacy`, which is the default.

---

## C. Before and after (fixture, in-process, same instance both paths)

| Connector resources | Path | Reachable | Provider calls | SQL statements | DB ms | p50 ms |
| --- | --- | --- | --- | --- | --- | --- |
| 80 | legacy | 80 | not counted* | 243 | 79.41 | 251.94 |
| 80 | candidate | 80 | 80 | 328 | 98.46 | 314.38 |
| 100 | legacy | 100 | not counted* | 303 | 111.94 | 361.23 |
| 100 | candidate | 100 | 100 | 410 | 144.47 | 464.04 |
| 150 | legacy | **100** | not counted* | - | 88.86 | 283.61 |
| 150 | candidate | **150** | 150 | 612 | 173.73 | 555.51 |
| 400 | legacy | **100** | not counted* | - | - | - |
| 400 | candidate | **400** | 400 | - | - | - |
| mixed 150+5 native | legacy | **105** | - | - | - | - |
| mixed 150+5 native | candidate | **155** | - | - | - | - |

\* **Measurement gap, stated rather than hidden:** this harness counts provider calls only on the
candidate path, because the legacy gate performs them inside
`authorized_connector_document_ids` without exposing a counter. The CA2-A baseline harness
measured the legacy gate at **exactly 100 provider checks** on a 150 resource corpus, which is a
separately sourced figure, not one produced by this table.

**Provider calls are the cost driver**, and the candidate does more of them at larger corpora
because it examines resources the legacy window never reaches. Unit pricing was not available to
the harness, so no currency estimate is fabricated.

---

## D. Acceptance scorecard

| Requirement | Verdict | Evidence |
| --- | --- | --- |
| No silent omission of authorized resources | **PASS** | 150: candidate 150 vs legacy 100; 400: 400 vs 100 |
| Recovered body-relevant evidence past index 100 | **PASS** | body-only test: cited by candidate, absent under legacy |
| Beyond position 256 reachable via continuation | **PASS** | 300 resource walk across pages |
| Truthful partial coverage with no leaked detail | **PASS** | notice tests; fixed prose per reason, no counts or ids |
| Empty partial result is not proof of absence | **PASS (answer text)** / **FAIL (response field)** | answer path fixed; `ChatResponse` field not added |
| GET /documents list contract preserved | **PASS** | response model asserted unchanged |
| Additive paginated catalog surface | **PASS** | `/documents/page` with sealed cursor |
| Strict pinned scope for authoritative output | **PASS** | 75 pinned parity; revocation refuses |
| Revocation honoured at consumption | **PASS** | strict scope raises after revoke |
| Tenant isolation | **PASS** | cross-tenant access yields nothing, zero provider calls |
| Provider outage fails closed and reports | **PASS** | reason `authorization_unavailable`, partial, notice present |
| Cursor confidentiality, tamper and cross-principal refusal | **PASS** | AEAD sealed; tested for tamper, principal, query change |
| One candidate traversal per logical request | **PASS (implemented)** / **NOT MEASURED** | provider count across a full orchestrator request not measured |
| Rollback restores legacy behaviour | **PASS** | flag flip tested, 100 window restored, no migration |
| p95 within 1.25x on comparable work | **PASS, MARGINAL** | 5-sample harness passes; single-sample comparison at 100 resources is ~1.29x |
| Zero unauthorized evidence exposure | **PASS** | integrity gate retained and tested |
| Live vector retrieval verification | **NOT TESTED** | no DB-GPT/Chroma runtime available; stubbed engine only |
| Full exact-head CI | **NOT TESTED** | not run in this window |
| CA2-C connector search completeness | **NOT STARTED** | - |

---

## E. Security assessment

Tenant isolation, principal isolation, current provider authorization, denial versus unavailable
semantics, revocation at consumption, classification and group access, cursor confidentiality and
tamper resistance, strict exact-source authorization, and CA1 non-mutation are all covered by
passing tests. No information leakage was found through notices, errors or metadata: notices are
fixed application-generated prose keyed by reason, carrying no counts, identifiers, titles or
provider detail. An unresolved issue is **not** claimed: see J.

---

## F. Compatibility assessment

`GET /documents` is untouched including its `list[DocumentOut]` response model. The catalog
additions are new fields and a new endpoint. Pinned report ordering was preserved by returning
the exact-scope result in the existing `created_at DESC` order. Legacy-mode responses are
byte-identical for the paths tested. Two behaviour changes are deliberate and flagged: the
catalog-pattern answer no longer claims absence when coverage is partial, and pinned scopes now
resolve by exact identity rather than from a corpus window.

---

## G. Performance and economics

Fixture latency ratios at comparable work (100 resources, both paths examining 100) passed the
1.25x p95 gate over 5 samples per path, but **the margin is thin**: a single-sample comparison at
100 resources measures 464.04 ms candidate against 361.23 ms legacy, roughly **1.29x**, driven by
**+35% SQL statements** (410 vs 303) from the per-page contract reads and the corpus digest. At
150 resources the candidate is slower in absolute terms because it performs 50 additional
authorization checks; that is additional work, not overhead, and should not be read as a
regression.

**The 1.25x target is met only marginally on this evidence and has not been validated on a live
stack.** Cost tracks provider authorization calls, which rise with the corpus because coverage
now means what it says. This is the central commercial tradeoff and it needs owner judgement.

---

## H. Test and CI evidence

| Suite | Result |
| --- | --- |
| CA2-A contract | 78 passed |
| CA2-B integration | 14 passed |
| CA2 measurement harness | 16 passed |
| CA1 and connector security | 42 passed |
| Regression selection over touched seams | 182 passed |
| ruff E9,F63,F7,F82 | clean |

Environment: mocked, in-process, sqlite, stubbed vector engine, no network, no model calls. All
timings are fixture timings. **Full exact-head CI was not run**, and no live-stack or DB-GPT/Chroma
result exists. No GitHub Actions run IDs are reported because none were produced in this window.

---

## I. Rollback

Tested, not documented: setting `retrieval_candidate_mode` to `legacy` restores the 100-resource
window, `coverage=None`, no notice and zero candidate provider calls, with no migration,
reindexing or state change. Asserted in
`test_rollback_to_legacy_restores_previous_behaviour`.

---

## J. Outstanding issues and limitations

1. **B1 incomplete.** The partial-coverage notice reaches the catalog response and the
   catalog-pattern answer text, but is **not** propagated to `ExecutionPlan` or `ChatResponse`, so
   an HTTP Chat or Knowledge answer does not yet carry the notice as structured data.
2. **Legacy provider calls are not counted** by the measurement harness (see C).
3. **p95 margin is thin and fixture-only.** ~1.29x on a single-sample comparison at 100
   resources. The 5-sample gate passed, but this is not comfortable headroom and is not a
   production measurement.
4. **No live DB-GPT/Chroma verification.** The end-to-end retrieval proof is at the
   candidate-selection and evidence-integrity boundary with the engine stubbed. This remains an
   unresolved acceptance gate.
5. **Full exact-head CI not run.**
6. **Phase 3 optimization not performed.** The duplicate-SQL and per-candidate authorization
   overhead identified above were measured but not optimized.
7. **CA2-C not started.**
8. **B3 saving is implemented but not measured** across a complete orchestrator request.

---

## K. Final recommendation

**REVISE.**

The functional core is sound and evidenced: silent omission is eliminated, coverage is truthful,
authoritative scopes are exact, revocation and tenant isolation hold, and rollback is tested. The
measured benefit is material (150 of 150 instead of 100 of 150, with recovered body-relevant
evidence that legacy cannot reach at all).

It is not yet a release candidate because three acceptance gates remain open: the p95 margin on
comparable work is thin and only fixture-measured, user-visible coverage is not yet wired through
the HTTP response, and no live-stack retrieval verification exists. The realistic path to
acceptance is bounded work in this order: complete the Phase 1B response propagation, optimize the
duplicate SQL and per-candidate authorization overhead and re-measure the ratio, then obtain live
DB-GPT/Chroma evidence on a non-production stack.

A recommendation is not permission to merge, and nothing here is merged or activated.
