# CA2 — frozen baseline, traceability matrix and benchmark methodology

Companion to `docs/plan/CA2_RETRIEVAL_COMPLETENESS.md` (revision `ca2-retrieval-2`).
Phase 1 artefact. Records existing accepted behaviour **before** any change, so that
non-regression is provable rather than asserted.

Nothing here authorizes implementation. PR #94 remains draft under the owner integration hold.

## 1. Frozen baseline

| Item | Value |
| --- | --- |
| Repository | `sjevans1/OpenJM-Enterprise-AI` |
| Baseline commit | `main` at `8d6b7d8` |
| CA1 prerequisite | merged as `a4e1e4f`, verified still in `main` history |
| Retrieval stack | DB-GPT behind the OpenJM-owned interface, one Chroma collection per document, `sentence-transformers/all-MiniLM-L6-v2` |
| Identity | own OIDC issuer/JWKS, Keycloak as reference; server-derived `Principal` |
| Model layer | OpenAI-compatible gateway (Hermes / Ollama / vLLM) |
| Test environment | fixture-only, in-process, no network, no model calls, per the existing suite convention |
| Fixture provider | the existing deterministic in-process connector provider pattern used by `tests/test_vs7_connector_security.py` |

The baseline is frozen at `8d6b7d8`. If `main` advances before Phase 4, the baseline is re-taken
and the delta recorded, rather than silently comparing against a moving target.

## 2. Frozen dataset

Two fixture sets, generated deterministically and committed as fixtures, not data.

1. **Scale set.** 150+ connector-origin resources across two connector instances, with
   interleaved insertion order so physical row order cannot accidentally be the intended order.
   Permission shapes: all-authorized; denied-first window; selective grants; missing user
   mapping; provider timeout then recovery; a resource whose match sits beyond position 100 and
   another beyond position 200.
2. **Native set.** Tenant documents across `public`, `internal`, `confidential`,
   `highly_restricted`, with department and group policies, so `document_is_visible` is exercised
   independently of the connector gate.

Frozen means: fixed content, fixed titles, fixed external ids, fixed insertion order, fixed
permissions. Any change to the fixture is a new baseline revision and must be declared.

## 3. Functional traceability matrix

Each row is accepted behaviour that must not regress. "Touched by" names the CA2 slice that could
affect it, or `none`.

| # | Accepted capability | Concrete surface on `8d6b7d8` | Baseline status | Touched by |
| --- | --- | --- | --- | --- |
| 1 | Chat (GENERAL) | `services/orchestrator.py` | accepted | none |
| 2 | Knowledge over native documents | `governed_tenant_documents` + `knowledge_engine.retrieve` | accepted | CA2-B |
| 3 | Knowledge over connector-origin documents | same + `authorized_connector_document_ids_for_context` | accepted, **defect: 100-cap omission** | CA2-A, CA2-B |
| 4 | Structured Data (governed read-only SQL) | `structured_planner`, `structured_executor`, `sql_policy` | accepted | none |
| 5 | Hybrid (policy-dependent) | `services/dependent_hybrid.py` | accepted | CA2-B |
| 6 | Catalog / document listing | `api/knowledge.py` `GET /documents` | accepted, **defect: same omission** | CA2-B |
| 7 | `connector.search` | `services/actions/connector_tools.py:115-137` | accepted, **defect: 200-cap, filter after limit** | CA2-C |
| 8 | `connector.fetch` (named resource) | `connector_tools.py` + `require_current_authorization` | accepted | CA2-A (contract only) |
| 9 | Report definitions, preflight, pinned source scope | `report_scope.py`, `api/reports.py` | accepted | CA2-B |
| 10 | Report runs, reruns, immutable snapshots, exports | `report_runs.py`, `export_render.py` | accepted | CA2-B |
| 11 | Agent tools / read-only actions | `services/tools.py`, `services/actions/` | accepted | CA2-B |
| 12 | Neighbour context, dedup, citation provenance | `context_expansion.py`, `evidence_quality.py` | accepted | CA2-B |
| 13 | Tenant isolation | `test_vs7_connector_security.py` isolation suite + CA1 additions | accepted | CA2-A, CA2-B |
| 14 | Revocation, outage fail-closed, CA1 cross-user availability | CA1 suite (order independence, co-tenant non-interference, recovery) | accepted | CA2-A, CA2-B |
| 15 | Audit | `record_audit` on mapping/credential/lifecycle events | accepted | CA2-A, CA2-B |
| 16 | Client-hosted and OpenJM-managed deployments | `docs/DEPLOYMENT_PROFILES.md` | accepted | none |

Rows 3, 6, 7 are the defects CA2 exists to fix. Every other row must be byte-equivalent in
behaviour after CA2, and the matrix is the evidence.

## 4. Benchmark methodology

### 4.1 Tolerances, declared before measurement

| Dimension | Metric | Threshold | Rationale |
| --- | --- | --- | --- |
| Retrieval completeness | reachable authorized resources across the fixture set | 150/150 reachable via relevant queries or truthful pagination | the defect is omission; partial reachability would not be a fix |
| Unauthorized exposure | unauthorized evidence reaching model/user/tool/report/metadata | 0, and 0 tenant-isolation regressions | non-negotiable |
| Functional parity | frozen matrix rows passing | 16/16, no exceptions | non-negotiable |
| Latency, interactive | p95 versus baseline | <= baseline p95 x 1.25 | bounded budget should not materially worsen interactive latency |
| Latency, report | p95 versus baseline | <= baseline p95 x 1.25 | authoritative paths may spend more checks but not unboundedly |
| Provider usage | checks per request | <= declared budget in every observed request; 0 unbounded | boundedness is part of the fix |
| Determinism | identical result set and coverage across 3 repeats, two insertion orders | 100% identical | the current defect is order-dependent |
| Cost | new external paid dependency | 0 | cost model rules |
| Reversibility | rollback restores baseline behaviour | 1 revert, suite green, behaviour identical on the matrix | required by the hold |

These are fixed before measurement. They are not revised to make a candidate pass; a revision
requires a recorded reason and the owner's agreement.

### 4.2 Comparison protocol

1. Run the frozen suite and the scale set against the baseline commit; record results as JSON/CSV.
2. Run the same, same fixtures, same environment, against the candidate head.
3. Record per dimension: reachable-authority count, omissions, coverage status, answer
   correctness, security results, matrix pass/fail, p50/p95 interactive and report, provider
   check counts and retries and timeouts and concurrency, CPU/memory (GPU where applicable),
   incremental compute and provider cost, outage and recovery behaviour, determinism across
   repeats, and rollback result.
4. Publish both raw numbers and the derived scorecard. No threshold may be applied only after
   seeing the candidate numbers.

### 4.3 What passing CI does and does not mean

Existing CI must be green on the exact head. It is necessary and not sufficient: it proves the
suite still passes, not that retrieval is more complete, that coverage is reported, or that
nothing was omitted. Only the Phase 4 comparison addresses those.

## 5. Scenario coverage checklist (Phase 1 list, mapped)

| Scenario | Where it is exercised |
| --- | --- |
| Chat, Knowledge, Data, Hybrid | orchestrator and dependent_hybrid suites; matrix rows 1-5 |
| Native and connector-origin Knowledge | fixture sets 1 and 2; rows 2-3 |
| Tenant isolation, group/classification policy | isolation suite; `document_is_visible`; rows 6, 13 |
| Current source permission and revoked access | CA1 suite; row 14 |
| Connector authorization outage and recovery | CA1 suite plus scale-set timeout-then-recovery fixture; row 14 |
| Catalog listing and document discovery | `GET /documents`; row 6 |
| Connector search and direct fetch | `connector_tools`; rows 7-8 |
| Agent tools and read-only actions | tools/actions suites; row 11 |
| Saved Reports, reruns, exports, immutable snapshots | report suites; rows 9-10 |
| Neighbour context, dedup, citation provenance | context_expansion and evidence_quality suites; row 12 |
| Auditing, error handling, operational behaviour | audit assertions; `authorization_incomplete` outcome tests |
| Client-hosted and OpenJM-managed deployments | deployment profile checks; row 16 |

## 6. Status

Phase 1 seam map: **complete** (see plan section 2).
Frozen baseline and dataset definition: **recorded here**.
Traceability matrix: **recorded here**.
Benchmark methodology and thresholds: **recorded here**.

Next, and only with the owner's go-ahead: record candidate acceptance criteria per package, then
build CA2-A in its own worktree as a draft, keeping the current path intact for comparison.
