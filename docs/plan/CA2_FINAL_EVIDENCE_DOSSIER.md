# CA2 final evidence dossier

**Status: PROVISIONAL, ready for independent final acceptance review. NOT merged, NOT activated.**

Repository: `sjevans1/OpenJM-Enterprise-AI`

| Package | PR | Branch | Head | Base | State |
| --- | --- | --- | --- | --- | --- |
| CA2-A contract | #94 | `plan/ca2-retrieval-completeness` | `3ce6ccc` | `main@709ce47` | draft, experimentally accepted |
| CA2-B integration | #98 | `feat/ca2-b-candidate-integration` | `6d30b6b` | PR #94 branch | draft, conditionally accepted |
| CA2-C search | #100 | `feat/ca2c-search-completeness` | `5d8f7fa` | `main@709ce47` | draft, Unicode correction applied |

Baseline when CA2 began: `main@8d6b7d8`. Current `main`: `709ce47`.

---

## A. Executive summary

**Two independent silent-omission defects, both fixed and measured.**

1. The Knowledge candidate set is built through one seam, `governed_tenant_documents`, which
   intersects a corpus-wide connector authorization gate that stops at 100 checks without ordering.
   At 150 authorized resources the legacy path reached **100, never examined 50, and recorded zero
   denials for the 50**, so authorized evidence was silently absent.
2. `connector.search` applied `.limit(200)` with no match predicate and no ordering, then matched in
   Python, so the window bounded the *table* rather than the matches. A metadata match beyond
   position 200 was permanently invisible with no signal of omission.

**Result.** At 150 resources the candidate path reaches **150 of 150** where legacy reaches 100, and
**live retrieval from a real index cites the document at index 115** whose marker exists only in its
body, which legacy cannot reach at all.

**Live gate: PASS.** Real DB-GPT, real Chroma and the real configured embedding model, in-process,
nothing stubbed in the retrieval path. 13 checks, 0 failures.

**Performance: PASS at 1.028x / 1.047x p95 on comparable fixture work against an unchanged 1.25x
threshold.** The live 1.138x is **not** p95 evidence and is not presented as such here. See G.

### Corrections applied for this review round

| Finding | Disposition |
| --- | --- |
| CA2-B: coverage warning lost when a conversation is reopened | **Fixed.** Persisted with the turn and replayed through `MessageOut`, no migration. See D2. |
| CA2-C: incomplete-scan note asserted that unseen matches exist | **Fixed.** Per-cause non-asserting wording, with a regression. See H. |
| CA2-C: three-arm SQL prefilter not a superset of the Python matcher (ẞ/ß, İ/i̇, ǅ/ǆ) | **Fixed.** Predicate removed, matching is Python-only over a bounded ordered scan. 20 tests. |
| CA2-B: no real handler/API regression for the notice lifecycle | **Fixed.** Two HTTP regressions, one falsified. See H. |
| CA2-B: frontend notice rendering unverified | **Verified and it was absent.** Rendered now, including on replay. See D. |
| CA2-B: PR #98 diff contained unrelated main deployment changes | **Fixed.** CA2-A reconciled onto current main; the stack diff is now the CA2 delta. See B. |
| CA2-C: PR #100 four commits behind main | **Fixed.** Reconciled. |
| Dossier stale heads and run IDs | **Updated.** |
| Live 1.138x presented as p95 evidence | **Corrected.** It is one retrieval-only timing per mode over different work. |

---

## B. Implementation inventory and merge strategy

CA2-A (`3ce6ccc`, PR #94): `backend/app/services/connectors/candidate_authorization.py`,
`backend/tests/test_ca2_candidate_authorization.py`,
`backend/tests/test_ca2_baseline_measurement.py`, `docs/plan/CA2_RETRIEVAL_COMPLETENESS.md`,
`docs/plan/CA2_BASELINE_AND_TRACEABILITY.md`.

CA2-B (PR #98): `backend/app/services/retrieval_candidates.py`,
`backend/tests/test_ca2b_candidate_integration.py`, `backend/tests/test_ca2_measurement_harness.py`,
`backend/tests/live/test_ca2_live_retrieval.py`, `backend/tests/live/README.md`,
`docs/plan/CA2_B_CONSUMER_MAP.md`, `frontend/src/api.ts`, `frontend/src/App.tsx`,
`frontend/src/App.test.tsx`, `frontend/src/styles.css`, with additive edits to
`app/core/config.py`, `app/core/context.py`, `app/services/document_policy.py`,
`app/services/tools.py`, `app/services/orchestrator.py`, `app/api/knowledge.py`, `app/api/chat.py`,
`app/schemas.py`, and a batched-read optimization inside the CA2-A module.

CA2-C (PR #100): `backend/app/services/actions/connector_tools.py`,
`backend/tests/test_ca2c_connector_search.py`.

### Merge hygiene, resolved

PR #94 was based on `main@8d6b7d8`. Because #98 is stacked on #94 and had merged current main, the
GitHub diff for #98 contained 29 files including unrelated main deployment, installer and REL1
changes. Reconciling CA2-A onto `main@709ce47` (`3ce6ccc`) fixed #94's own diff but not #98's: with
#94's branch head no longer an ancestor of the CA2-B branch, the merge base fell back to the old
pre-reconciliation commit, so the deployment changes reappeared. The CA2-B branch therefore also
merges the reconciled CA2-A head. Both branches now contain `main@709ce47` and share it as the merge
base, so:

| PR | Diff vs its base | Unrelated deployment files |
| --- | --- | --- |
| #94 | 5 files, CA2-A only | 0 |
| #98 | 21 files, CA2-B only | 0 |
| #100 | 2 files, CA2-C only | 0 |

Every merge was clean, none of the incoming files touches a CA2 path, and no CA2 code changed as a
result. Main itself is untouched by all of this: the merges land on the CA2 branches only.

### Sequential merge strategy for #94, #98, #100

Order matters; #98 is stacked, #94 and #100 are independent.

1. **#94 first**, onto `main@709ce47`. Its branch already carries that merge and is clean. Its diff is
   the CA2-A contract only. Merging it does not activate anything: the contract has no production
   caller and CA2-B remains its only consumer.
2. **#98 second**, onto the merged CA2-A. With #94 landed, the #98 diff resolves to the CA2-B delta
   alone (service, tests, additive API/schema edits, the frontend notice). GitHub retargets its base
   from the #94 branch to `main` automatically once #94 is merged.
3. **#100 third**, independently onto `main`. It touches `connector_tools.py` only and no CA2-A or
   CA2-B file, so it can go first or last without interacting; third keeps the review order matched to
   the acceptance order. It needs no flag and reverts by revert.

Do not squash #94 or #98: CA2-A is an independently accepted artifact and should stay reviewable as
its own history. No merge is performed here.

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
| `connector.search`, ẞ/ß, İ/i̇, ǅ/ǆ beyond 200 | invisible | returned | CA2-C, this round |

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

## D. The user-visible warning, verified rather than assumed

The independent review was right to insist on this: **the frontend did not read the field at all.**
`App.tsx` contained zero references to coverage, and the `Message` and `ChatResponse` types in
`api.ts` did not even declare it, so the server's warning reached the JSON and stopped there.

Closed in this round: `CoverageNotice` is typed in `api.ts` and carried on `ChatResponse` and
`Message`; the chat handler stores it per assistant message; `App.tsx` renders it beside the answer
it qualifies as a `role="status"` element with `data-coverage-notice`; `styles.css` styles it as an
advisory, deliberately distinct from an error banner.

It is carried **per message**, not as view state, so a later complete answer cannot clear or inherit
an earlier notice, and reopening a conversation shows what that answer actually said.

Frontend evidence: `tsc --noEmit` and `vite build` clean, and **99 tests passed across 14 files**,
including three that assert the warning is rendered verbatim from the server's sentence, that a
complete answer renders none, and that **reopening a partial conversation still shows it**.

---

## D2. History replay keeps the qualification

The independent review found a real user-facing regression that the previous round's claim did not
support: the notice was attached only to the **live response**. `chat()` set
`ChatResponse.coverage_notice` after the assistant turn had already been committed, `MessageOut` had
no such field, and the persisted `Message` carried only content, execution class, requested mode and
evidence. `openConversation()` replaces the message array from `GET /api/conversations/{id}`, so a
warning shown after the POST vanished the moment the same conversation was reopened. An incomplete
answer read later, unqualified, is precisely the silent omission CA2 exists to remove.

**No migration.** `Message` has no spare column, and the review required the evidence shape to be
preserved while avoiding a migration unless truly required. So the qualification is written into the
persisted assistant **text**, which is where a historical turn has its only channel:

* the fixed, server-written sentence (never model text, never source data) is appended to the
  persisted content as `"<answer>\n\nCoverage note: <fixed sentence>"`;
* the live response keeps its **clean** `answer` and reports the notice separately, so a reader of the
  live response sees the warning **exactly once** and not twice;
* `MessageOut.coverage_notice` is populated on replay by recognising that exact prose
  (`persisted_coverage_notice()`), so an API client gets the structured notice without parsing text,
  and a client that ignores the field still cannot read an incomplete historical answer as complete;
* only the exact sentences this module writes are ever recognised, so user content can never be
  mistaken for a qualification;
* legacy mode writes nothing, so a complete legacy answer is persisted byte-for-byte as returned.

**One implementation detail worth recording**, because the first attempt looked correct and did
nothing: `assistant_message.content` is finalised *after* the artifact path may rewrite it, so an
annotation computed at message construction is discarded a few lines later. The write had to move to
the point where the persisted text is actually final. The test caught it; the review could not have.

Covered by HTTP tests: POST partial Knowledge, reload the conversation, assert the qualification is
still there and structured; send a complete request in the same conversation and assert the earlier
turn stays qualified, the new turn has no qualification, and its content equals the returned answer
byte-for-byte; and a failing model call (502) leaves no assistant turn and therefore no phantom
qualification.

---

## E. Acceptance scorecard

| Requirement | Verdict | Evidence |
| --- | --- | --- |
| No silent omission of authorized resources | **PASS** | 150: 150 vs 100; live 120 of 120 |
| Real indexed retrieval beyond the legacy window | **PASS** | live check, index 115 cited |
| Beyond position 256 reachable by continuation | **PASS** | 300 resource walk |
| Truthful partial coverage, no leaked detail | **PASS** | fixed prose per reason; live notice check |
| Nonempty partial result does not look exhaustive | **PASS** | catalog answer appends the notice |
| Empty partial result is not proof of absence | **PASS** | answer text plus `coverage_notice` |
| Coverage on the user-visible response | **PASS** | `ChatResponse.coverage_notice`, `ExecutionPlan.coverage_notice` |
| **Coverage visible to the user in the UI** | **PASS (was FAIL)** | rendered per message; 3 frontend tests |
| **History replay keeps the qualification** | **PASS** | persisted with the turn, replayed via `MessageOut`; HTTP tests |
| Notice cannot leak across requests | **PASS, falsified** | sequential HTTP test plus a falsified stale-context test |
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
| p95 within 1.25x on comparable work | **PASS** | 1.028x / 1.047x locally and in CI |
| Zero accepted functional regressions | **PASS after two repairs** | see H |
| `connector.search` match beyond 200 | **PASS (CA2-C)** | rank 281 returned |
| `connector.search` truthful incompleteness | **PASS (CA2-C)** | capped and truncated cases; the note never asserts unseen matches |
| **`connector.search` Unicode correctness** | **PASS (CA2-C)** | ẞ/ß, İ/i̇, ǅ/ǆ, ASCII 'i' vs 'İ', all beyond 200 |
| Live DB-GPT/Chroma verification | **PASS** | 13 checks, 0 failures |
| Merge | **NOT DONE** | owner hold binding |

---

## F. Security assessment

All passing, on fixture evidence and, for the retrieval path, on live evidence: tenant and principal
isolation, current provider authorization, denial-versus-unavailable semantics, revocation at
evidence consumption, classification and group access, cursor confidentiality and tamper resistance
(AEAD, bound to tenant, principal, query and scope), strict exact-source authorization, no partial
authoritative reports, and CA1 non-mutation.

Live-specific: an indexed document withheld from the authorized refs is **not returned by real
retrieval**. No information leakage was found through notices, errors or metadata; the notice is
fixed application prose per cause, and a test asserts it contains no identifier, count or title.

CA2-C security note: removing the SQL prefilter means the scan is bounded rather than narrowed, so the
tool reads up to `MATCH_SCAN_LIMIT` active resources of the tenant per search. Tenant scoping is
unchanged and every candidate still passes current-authorization before being returned.

---

## G. Performance and economics

### Fixture, comparable work, 100 resources, 15 samples, warmup excluded, paths interleaved

| Stage | legacy p50 / p95 | candidate p50 / p95 | ratio p50 / p95 | SQL |
| --- | --- | --- | --- | --- |
| before optimization | 526.39 / 798.84 | 675.90 / 1141.96 | 1.284 / **1.430 FAIL** | 303 vs 410 |
| after, run 1 | 421.96 / 543.80 | 420.61 / 528.81 | 1.003 / **1.028 PASS** | 303 vs 314 |
| after, run 2 | 653.74 / 823.50 | 629.59 / 862.59 | 0.963 / **1.047 PASS** | 303 vs 314 |

### Live, real Chroma and embeddings, 120 documents

| Metric | Value |
| --- | --- |
| ingest | 32.2 s total, 268.6 ms per document |
| Chroma vector directory | 20.7 MB for 120 per-document collections |
| legacy query (100 refs) | 2546.7 ms |
| candidate query (120 refs) | 2898.8 ms |
| live retrieval latency ratio | 1.138x |
| dependencies | dbgpt 0.8.2, dbgpt-ext 0.8.2, chromadb 1.5.9, sentence-transformers 6.1.0, torch 2.14.1 |

### Latency by collection count, and what it means

There is one Chroma collection per document, so a Knowledge query pays per authorized collection
examined. From the two measured points (2546.7 ms / 100 = 25.5 ms; 2898.8 ms / 120 = 24.2 ms):

| Authorized collections | Expected retrieval step | Basis |
| --- | --- | --- |
| 100 | **2.5 s** | measured |
| 120 | **2.9 s** | measured |
| 256 (the default cap) | **6.1-6.4 s** | extrapolated from the measured per-collection cost, not measured |

The first two are measurements; the third is an extrapolation and is labelled as one. This is the
cost of completeness, not a regression against legacy work of the same size, and it is within the
agreed relative threshold. It is an absolute-latency and SLA question with no agreed cutoff in this
evidence, so it needs a conscious product rollout decision, and the default cap must not be raised
silently to hide it.

**The live 1.138x is not p95 evidence.** It is one retrieval-only timing per mode over different work
(120 versus 100 collections), excluding the authorization traversal, so it must not be presented as
proof that a deployed p95 met 1.25x. The p95 claim rests on the fixture table above and on CI.

**Provider calls.** Equal at 100 resources; 150 vs 100 at 150 resources, buying the 50 resources
legacy never examines. Unit pricing is not available to the harness, so **no currency estimate is
fabricated**.

---

## H. Test and CI evidence

Local, at the heads above:

| Suite | Result |
| --- | --- |
| CA2-A contract | 78 passed |
| CA2-B integration plus measurement harness | 39 passed |
| CA2-A + CA2-B + harness together | **117 passed** (was 115) |
| CA2-C search | **20 passed** (was 15 before this round's Unicode work, 19 after it) |
| Frontend (vitest) | **99 passed across 14 files**, plus a clean `tsc`/`vite` build |
| Live retrieval harness | 13 checks, 0 failures |
| `pytest tests/ --collect-only` | clean, no collection errors |
| ruff `E9,F63,F7,F82` | clean |

### The CA2-C Unicode correction, and why the predicate was removed

The independent review reproduced three cases where the shipped three-arm SQL predicate was not a
superset of the Python matcher. Verified independently here, in SQLite, against that exact predicate:

| title | query | Python | 3-arm predicate |
| --- | --- | --- | --- |
| ẞ | ß | True | **False** |
| İ | i̇ (i + U+0307) | True | **False** |
| ǅ | ǆ | True | **False** |
| İ | i (pure ASCII query) | True | **False** |
| ÄNDERUNGSBERICHT | änderungsbericht | True | **False** |

The last row is the case a previous commit claimed to have fixed. It was still a silent omission.
Every one of these would have removed an authorized resource before the LIMIT and before the matcher.

Because SQLite does not Unicode-case-map and PostgreSQL's folding is locale dependent, no portable
predicate can be proven to include every row Python accepts, and an ASCII-only form is unsafe too
(Python lowercases 'İ' to 'i̇', which contains an ASCII 'i'). The predicate is therefore gone, not
widened, and matching runs in Python over a bounded deterministically ordered scan with
`matches_complete` false whenever the bound or the result cap was reached.

Backend parity is now true by construction: **which rows match** is identical on SQLite and
PostgreSQL because the matcher is Python. The only backend-dependent aspect is which rows fall inside
the bound when the scan truncates, and that condition is reported.

### The notice-lifecycle regressions, and one honest negative result

Two HTTP regressions were added, both through the real router on one ASGI client:

1. `test_a_partial_knowledge_notice_cannot_leak_into_the_next_http_request` — request 1 is a real
   Knowledge request whose connector authorization cannot be proven (provider down), so the response
   carries `authorization_unavailable`; request 2 is a real general Chat request and carries none.
2. `test_a_stale_notice_in_the_ambient_context_is_cleared_before_the_request_runs` — a notice already
   sitting in the ambient context must be cleared at request entry.

**Falsification, and what it showed.** `reset_coverage_notice()` was disabled in the handler and both
tests were re-run:

* test 2 **fails**, exposing the leaked notice dict, so the reset is genuinely load-bearing and that
  test is not vacuous;
* test 1 **still passes**, because two independent HTTP requests are also isolated from each other by
  the async runtime: each runs in a task that copies the context, so state set in one request does not
  reach the next.

That second result is recorded rather than hidden. The sequential test proves the notice reaches the
user-visible response and that the next request does not carry it, but it does **not** by itself prove
that the reset is what prevents that, and its docstring says so.

### CI, `OpenJM CI` workflow

| Head | Run | Result |
| --- | --- | --- |
| CA2-A `3ce6ccc` (current) | `38230734152` | **success** |
| CA2-B `6d30b6b` (current) | `38232317447` | **success** |
| CA2-C `5d8f7fa` (current) | `38231163795` | **success** |
| CA2-A `8557487` | `38220473733` | success |
| CA2-B `707c50c` | `38227735920` | success |
| CA2-C `1125427` | `38225959902` | success |

All three current heads passed the full workflow, including the Backend/Python 3.11 job and the
Frontend/Node 22 job that covers the new coverage-notice rendering.

The commit carrying this dossier is itself docs-only and is the CA2-B branch tip; its CI repeats the
same passing jobs over an identical code tree plus markdown, because the heads above are validated
with the code they actually contain.

Earlier, `978a2b1` failed and `63f5f1e` repaired it (regression 2 below).

### Two regressions found and repaired, both mine

1. Changing `_ready_documents` from `list` to `tuple` broke two `test_vs5_tenant_isolation.py` tests
   and failed CI. Repaired by moving the CA2-B work to `_ready_documents_with_context` and leaving the
   original contract intact.
2. Adding `tests/live/conftest.py` shadowed `tests/conftest.py` for any module doing a bare
   `import conftest`, breaking seven report-run modules. Repair: no conftest in that directory; the
   live module is gated by a module-level `skipif` instead.

---

## I. Rollback

Tested, fixture and live: setting `retrieval_candidate_mode` to `legacy` restores the 100-resource
window, `coverage = None`, no notice, zero candidate provider calls, and with the real index in place
it returns the same 100 documents. No migration, reindexing or irreversible state change.

CA2-C needs no flag: it is a correctness fix inside the existing tool, and reverting the commit
restores the previous behaviour. The frontend change is additive and inert when the server sends no
notice.

---

## J. Outstanding issues and limitations

1. **Live scope, stated precisely.** The live test validates **actual retrieval and citations** on a
   real index with real embeddings. It is **not** a full deployed PostgreSQL/Keycloak HTTP stack, not
   multi-user traffic, and not a model-generated end-user answer. It is a retrieval/evidence-boundary
   smoke test, single-process and SQLite-backed.
2. **Absent absolute latency evidence.** 100 and 120 collections are measured at 2.5 s and 2.9 s; 256
   is extrapolated to 6.1-6.4 s. No agreed absolute cutoff exists in this evidence.
3. **`connector.search` scans rather than seeks.** Removing the unsound predicate means `LIKE` no
   longer narrows, so the tool examines up to `MATCH_SCAN_LIMIT` (500) active resources per search.
   Bounded in rows examined and reported via `matches_complete`, and not measured at production scale.
4. **`connector.search` has no cursor continuation.** Incompleteness is reported instead, which the
   mandate permits for this slice.
5. **CA2-B's diff touches the CA2-A module** for the batched-read optimization. No contract element
   changed and CA2-A's 78 tests pass, but CA2-A is the accepted artifact, so this is flagged.
6. **The live harness is excluded from CI** by design and runs only on request (`CA2_LIVE=1`), so
   regressions in it would not be caught by CI.
7. **Frontend tests run against a stubbed fetch**, so they assert rendering, not a live server round
   trip; the live server round trip is covered by the backend HTTP regressions.
8. **The negative case is deliberate.** If a client ignores `MessageOut.coverage_notice`, it still
   sees the qualification in the persisted text; that is why the text carries it rather than the
   field alone. The trade-off is that history shows the qualification as prose rather than as a
   styled notice. This is the smallest implementation that survives a reload without a migration, and
   the review's own guidance permitted it.

---

## K. Final recommendation

**ACCEPT FOR OWNER MERGE REVIEW.**

Every engineering gate is met with evidence: both silent-omission defects fixed and measured, the
live gate closed with real retrieval and citations, the CA2-C Unicode defect repaired by removing the
unsound predicate rather than widening it, coverage truthful and now **visible in the UI**, the notice
lifecycle covered by HTTP regressions one of which is falsified, the p95 gate passing on comparable
work, rollback tested, and the stack reconciled onto current main so the reviewed diff is the CA2
delta rather than unrelated deployment work.

Three things a reviewer should weigh, all stated plainly rather than buried:

1. **Absolute latency.** Completeness costs roughly 20-25 ms per authorized collection: 2.5 s at 100,
   2.9 s at 120, extrapolated 6.1-6.4 s at the default 256. That is a product rollout and SLA
   decision, not a threshold breach.
2. **Live scope.** Retrieval and citations are validated live; a deployed PostgreSQL/Keycloak stack
   and a model-composed answer are not.
3. **`connector.search` now scans** up to its bound instead of narrowing in SQL, which is the honest
   cost of not shipping a predicate that silently drops matches.

**A recommendation is not permission to merge.** Nothing here is merged or activated.
