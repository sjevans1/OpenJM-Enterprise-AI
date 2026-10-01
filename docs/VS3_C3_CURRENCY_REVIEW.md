## Verified CI checkpoint — 2026-10-01

GitHub Actions run **36876494651** at branch commit `56c38af77ee6e52f4c6bebb69abf445df5a9f0e2` completed successfully in **both** jobs:

- `deterministic-policy-binding`: safety tests for policy threshold binding, currency guards, disabled/ambiguous sources, legacy database migration, real SQLite result checks, and mocked-tool *real orchestrator* C3 tests.
- `frontend-citation-and-build`: Node citation-label regression and production TypeScript/Vite build with locked dependencies.

Evidence URL: https://github.com/sjevans1/OpenJM-Enterprise-AI/actions/runs/36876494651

This is meaningful **CI** acceptance, but *not* full backend/model/API/physical-system acceptance. The CI orchestration wrapper stubs the heavyweight Knowledge adapter and mocks tool execution. Live Gemma+Chroma+real API acceptance remains pending. The broken `frontend/src/EvidencePanel.test.tsx` placeholder has been replaced by `frontend/tests/evidenceLabels.test.mjs` exercising the same label helper now called by `App.tsx`.

The GitHub connector was unable to create a draft pull request in this session (action blocked by the host). Work is committed to the verified branch and may be reviewed using GitHub compare; no merge has been attempted.

# VS3-C3 parallel hardening — currency and provenance

**Status: DRAFT / NOT MERGED / NOT PRODUCTION-ACCEPTED**

This work accompanies the existing dependent Hybrid implementation on `feature/hybrid-vertical-slice-3` and is intentionally isolated in `hardening/vs3-currency-provenance`. A separate Hermes local branch called `feature/hybrid-vertical-slice-3-c3` was reported but was **not present remotely at the last verification**. Rebase/reconcile both changesets after it is pushed; never force-push or overwrite either implementation.

## Risk addressed

An annual-revenue policy stating `USD 300` cannot be compared safely with transaction amounts in JMD, an unknown denomination, or an unspecified dollar sign. Retrieval evidence by itself does **not** establish the accounting basis or denomination of an external data source.

## Implementation

- DataSource gains nullable `revenue_currency`; only `USD`, `JMD`, or null (unknown) accepted in create/update API.
- Existing SQLite application databases upgrade with a non-destructive, idempotent migration. Existing data sources remain `NULL` until deliberately attested.
- Data UI supports reviewing/changing the declared currency. This is **operator attestation**, not automatic detection or forex conversion; client onboarding must verify accounting basis before setting it.
- Policy threshold Evidence extracts explicit USD/JMD without assuming generic `$` means USD. Conflicting denominations are an error. Ambiguous denominations cannot drive C3 data execution.
- The C3 orchestrator checks the Structured planner's selected source against the authorized, connected source catalog and requires its declared currency to match policy currency **before** `structured.query`. Evidence provenance includes both currency and policy document/source IDs.
- A deterministic, tightly scoped completed-orders single-year SQL compiler is included as experimental code. **It is not the active C3 path**. It needs separate business-semantic acceptance (returns, cancellations, taxes, fiscal years, recognized vs booked sales, aggregate rounding, timezone and multi-currency orders) before promotion.
- The pre-existing independent HYBRID path remains as designed; no autonomous agent logic or document-to-SQL string interpolation was added.

## Acceptance / review blockers

1. Run the C3 GitHub Actions suite (tests `test_dependent_hybrid.py`, `test_dependent_currency.py`) and the orchestration tests after merging the parallel branches.
2. Run full backend pytest, frontend TypeScript+Vite production build, EvidencePanel tests, and RAG/Structured API gates A–J on a fresh environment.
3. Validate the application migration on a pre-existing SQLite data source with preserved credentials and catalog.
4. Validate a real locally hosted model against real authorized Knowledge and Structured sources, testing both an explicit USD policy and an ambiguous `$` policy.
5. Regression cases: disabled source, source ownership failure, stale schemas, multiple currencies, missing/contradictory policy, SQL policy rejection, deleted policy document, repeated queries and hostile Evidence.
6. Review source-currency change auditing and operator permissions before production multi-tenant rollout.
7. Resolve implementation overlap with Hermes local C3 work and consolidate to one `_plan_dependent_hybrid` definition. Avoid a second dispatch or competing AST-binding/SQL-compiler policy.
8. Do not merge without user review/authorization.

## Integration note for Hermes

Hermes reported one local failing unit test about `applicants with scores exceeding 700`. This branch does not contain the Hermes local `GroundedParameter` implementation; no assertion that this specific failure was fixed is possible. Once the local C3 branch is pushed, inspect its exact regex and policy-context validation, add a focused regression for the phrase, and run all C3 gates. Missing thresholds for a truly dependent question must fail closed rather than silently answer as an independent comparison.

## Evidence

The baseline B3 branch previously reported 157 passing backend tests and a passing frontend build. Those are historical checkpoint reports, **not** evidence that the combined C3 or this currency branch has passed. CI status and live acceptance must be checked after the PR is created.
