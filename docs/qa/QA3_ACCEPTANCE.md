# QA3 Acceptance Record: Authoritative/Vector-RAG Qualification

- **Tested Git SHA:** `4479d82ba773affab926d81bffbc1a9d272fdbef` (`main`, QA1 and QA2 accepted and merged).
- **Date:** 2026-10-11.
- **Package:** QA3 (qualification only; no feature wave, no OPS1 implementation).
- **Outcome:** **QA3 ACCEPTED. 53 checks: 50 PASS, 0 FAIL, 2 NOT RUN, 1 DEFERRED.**
- **Rahkia production cutover:** DEFERRED by design. No Rahkia runtime integration was enabled.

QA3 proves live that ordinary governed RAG retrieval works, that authorization
happens before evidence selection and model context, and that an authoritative
Saved Report contributes bounded, additive candidates that follow current source
authorization at every request.

## Environment

| Item | Value |
| --- | --- |
| OS | Ubuntu 24.04.4 LTS on WSL2 (kernel 6.6.87.2) |
| Python | 3.11 (production venv, packaged wheel, no editable install, no PYTHONPATH) |
| Node/npm | 22 |
| PostgreSQL | 16.15 (production profile, metadata DB) |
| Keycloak | 26.7.4 (OIDC, authorization-code + PKCE) |
| OpenJM product version | 0.2.0 (REL1) |
| Migration head | `0021_support_content_scope` |
| Vector/RAG | DB-GPT knowledge engine with Chroma persistent store (all-MiniLM-L6-v2) |
| Model path | OpenCode Go `glm-5.3-flash` via the QA shim |
| Backend | `127.0.0.1:18010` |
| Frontend | `127.0.0.1:15173` |
| Keycloak | `127.0.0.1:18090` |
| PostgreSQL | `127.0.0.1:15432` |
| Vector path | `runtime/vector` (Chroma) |

The environment was rebuilt from the accepted `main` tree: the packaged backend
wheel was built from `4479d82`, and the served frontend build corresponds to a
tree identical to accepted `main` (the QA2 branch tree and `main` are byte-identical
at `4479d82`). No dev-auth is active; the production profile and preflight reject it.

## Inference model

`/api/health` reports `model: glm-5.3-flash` and `profile: production`. A
representative QA3 call ran on the GLM path, metering recorded only
`glm-5.3-flash`, and the OpenCode Go credential never appeared in any response
body. No silent fallback was observed. The authoritative eligibility decision
remains deterministic server-side and is not delegated to the model.

## BV6-B authoritative candidates are an accepted opt-in

The authoritative Saved Report candidate path is off by default
(`OPENJM_REPORT_AUTHORITATIVE_CANDIDATES_ENABLED=false`). QA3 enables it
explicitly in `deploy/qa/qa_env.sh` (`ENABLED=true`, `MAX=5`, `MIN_OVERLAP=1`)
because the package must exercise the path. This is an accepted, additive
opt-in; enabling it does not weaken authorization.

## Fixtures

Tenant A (`acme`) governed Knowledge:

| Document | Classification | Visibility |
| --- | --- | --- |
| Employee Handbook | internal | tenant-wide |
| Employee Onboarding Guide | internal | tenant-wide |
| HR Compensation Policy | highly_restricted | HR Leadership only |

Tenant B (`globex`): Globex Operations Manual (internal, tenant-wide) so
cross-tenant RAG can be tested explicitly. Structured Data fixtures (customers,
orders, inventory and an operations source) were seeded during QA1 and reused.
All data is synthetic.

## Phase results

| Phase | Area | Result |
| --- | --- | --- |
| 2 | Ordinary governed vector/RAG baseline | 7/7 PASS |
| 3 | Evidence-backed Saved Report creation | 3/3 PASS |
| 4 | Full BV6-A curation state machine | 11/11 PASS |
| 5 | Authoritative candidate live retrieval | 6/6 PASS |
| 6 | Only `authoritative` may contribute a candidate | 3/3 PASS |
| 7 | Relevance gate | 2/2 PASS |
| 8 | Source revocation removes candidate influence | 4/4 PASS |
| 9 | Regrant restores eligibility | 2/2 PASS |
| 10 | Unauthorized principal negative | 2/2 PASS |
| 11 | Cross-tenant candidate negative | 2/2 PASS |
| 12 | Live structured-source authoritative candidate | **NOT RUN** (see below) |
| 13 | Authorization before evidence/model context | 3/3 PASS |
| 14 | GLM path and metering | 2/2 PASS |
| 15 | Withdrawal/demotion | 3/3 PASS |

### Ordinary baseline (Phase 2)

The ordinary employee asked a deterministic handbook question. Real vector
retrieval returned two authorized tenant-wide candidates (Employee Handbook and
Employee Onboarding Guide) with `[DOC n]` citations in the evidence panel. The
restricted HR source and the Tenant B source were absent, no authoritative
candidate existed yet, and the answer was grounded in the retrieved evidence.

### Curation lifecycle (Phases 3-4)

Three evidence-backed Saved Reports were created from real answers by the report
owner (`qa.clientadmin`). The full state path was exercised:

- `none` -> `under_review` by the owner (request review).
- `under_review` -> `approved` by a distinct authorized admin (`qa.admin1`).
- `approved` -> `authoritative` by a third distinct principal (`qa.admin2`).

Two-person rule: owner self-approval was denied (403), the approving principal
could not also confirm authoritative (rejected), an unauthorized ordinary member
could not approve, and the platform operator alone could not curate customer
content. `featured` set on an approved report left `curation_state` at `approved`,
proving it is presentation-only. Curation transitions are audited.

### Authoritative candidate (Phase 5)

On a relevant query the evidence order was `['document', 'document', 'report']`:
ordinary documents first, then one appended candidate with
`source_type="report"`, `provenance.candidate_kind="authoritative_report"`, and a
bounded reference passage (title, authorized source count, snapshot time). No
pinned source content was copied into the candidate, and ordinary evidence
ordering was not rewritten.

### Non-authoritative states (Phase 6)

An approved and featured report, and an under_review report, never contributed a
candidate. On a relevant query the only candidate ever observed was the
authoritative report.

### Relevance gate (Phase 7)

A relevant query yielded the candidate; an unrelated query yielded none. The gate
uses configured significant-term overlap (`MIN_OVERLAP=1`) and was not modified to
pass the test.

### Revocation, regrant and unauthorized principals (Phases 8-10)

Revoking one pinned source (restricting the Employee Handbook to HR Leadership)
removed the candidate immediately while ordinary evidence remained usable where
still authorized. No report id, title or pinned source content leaked. A distinct
Tenant A principal without the grant received no candidate and learned nothing
about the report. Regranting the source restored eligibility without any change to
the report, which remained `authoritative`. Eligibility follows current source
authorization, not a stale cached authority.

### Cross-tenant (Phase 11)

A Tenant B member asking a query relevant to the Tenant A report received zero
candidates and no report id, title or source information. Non-enumerating
behaviour held.

### Authorization before model context (Phase 13)

The live evidence set contained only currently authorized sources. The restricted
HR document never entered the evidence set or the response body (which is what
feeds the model), and the authoritative eligibility decision was deterministic
across repeated identical requests.

### GLM path (Phase 14)

`glm-5.3-flash` served the QA3 retrieval calls; metering recorded only
`glm-5.3-flash`; the credential did not appear in any response.

### Withdrawal (Phase 15)

Withdrawing the authoritative report returned it to `none`; the candidate
disappeared immediately and no report reference leaked. The historical curation
audit survived.

## NOT RUN / DEFERRED

| ID | Item | Class | Reason |
| --- | --- | --- | --- |
| QA3-P12-01 | Live structured-source authoritative candidate | NOT RUN | The document-backed live revalidation (Phases 8-9) exercises the same shared source-revalidation predicate. Automated structured-pin coverage `tests/test_bv6b_candidates.py` and `tests/test_bv6b_candidate_report_integration.py` passed 29/29. A second live structured authoritative fixture was not prepared economically. |
| QA3-HOST-01 | Clean, separate Linux development host install | NOT RUN | No separate clean host. Compatibility coverage, not an Issue #45 deployment gate. |
| QA3-HOST-02 | Real Windows/WSL2 runtime install | DEFERRED | No real Windows host. Non-blocking compatibility qualification. Full Windows support is not claimed. |

## Product defects found

None. QA3 exposed no product defect in authorization, tenant isolation, curation
rules, two-person authority, table authorization, retrieval filtering or audit
behaviour. All negative assertions passed without weakening any boundary.

## Repository changes

QA3 is evidence-and-configuration only, plus the accepted opt-in flag in the QA
environment file. No product code changed. The changes are:

- `deploy/qa/qa_env.sh`: enable the accepted BV6-B authoritative-candidate opt-in.
- `docs/qa/QA3_ACCEPTANCE.md`: this record.
- `docs/ACCEPTANCE_MATRIX.md`: restamp and live-gate reconciliation.

## Reproducibility

QA tooling lives under `~/openjm-qa3/tools` (`qa3_fixtures.py`,
`qa3_phase2.py`, `qa3_phase34.py`, `qa3_phase5.py`, `qa3_phase6.py`,
`qa3_phase78910.py`, `qa3_phase11_14_15.py`) and reuses the QA1/QA2 helper
library. Credentials live only under the operator secrets store
(`~/openjm-qa1-env/secrets`) and are never committed.
