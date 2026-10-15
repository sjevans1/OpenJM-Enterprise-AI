# OpenJM Development Framework v1.8.3 — Enterprise AI adoption

**Status (2026-10-15): reference pinned; controller activation NOT YET AUTHORIZED.**  
**Repository:** `sjevans1/OpenJM-Enterprise-AI`  
**Approved framework:** [v1.8.3 merged on main](https://github.com/sjevans1/OpenJM-Development-Framework/commit/b9cc14d708311417d6945e074799f4cb3d46cf8b); immutable `framework.lock` at this repository root.  
**Adoption branch baseline:** `main@8e09ab586113648f606b8927f22a8b444d3cb581`.

This is a controlled **documentation/reference adoption**, not a cutover of current product execution. It does not install a different Hermes plugin, start or reset the controller, change CI, modify live model configuration, alter application/runtime behavior, merge an existing product PR, or reinterpret historical acceptance.

## Authority, model and quality contract

- Human + Astra (manual) authorize the plan; GPT-6 Luna frames bounded packages; DeepSeek V4.1 Flash implements and performs bounded CI corrections; Claude Haiku 5.5 independently tests; a **separate, ledger-verified fresh Luna session** reviews; a verified human alone authorizes merge.
- **MAX reasoning effort** is the selected Hermes setting across the three models. Named Haiku Anthropic-wire route and **no fallback providers** are preserved; no GLM or Sol in governed development dispatch.
- The controller allows **two meaningful correction cycles total per package**, shared across FAST, STANDARD, Haiku acceptance and Luna review. Never reinterpret a carried-over per-role budget as additional cycles; infrastructure retry is separate and bounded.
- The governing product plan remains [PLAN](PLAN.md) and [enterprise productization](plan/ENTERPRISE_PRODUCTIZATION.md). Premium end-to-end business workflows, policy/tenant authorization and [UI quality](design/UI_QUALITY_STANDARD.md) must meet [enterprise journey acceptance](qa/ENTERPRISE_PRODUCT_ACCEPTANCE.md), not merely look polished. Historical `ACCEPTANCE_MATRIX.md` evidence stays tied to its original candidate.
- Existing [CI tiers](CI_TIERS.md), [Hermes workflow](CI_HERMES_WORKFLOW.md), release/security gates and approved feature scopes remain in force until a separately reviewed activation. No production secrets, tenant data or third-party model credentials enter the framework lock.

## Freeze on in-flight work

- **EP-01A / PR #113** must complete or reach an explicitly approved safe checkpoint under its existing recorded workflow. Hermes most recently reported this work package in `phase=repairing`, with **repair attempt 1 of 2 already reserved**. This is a reported snapshot, not an assertion of current live state.
- Before any later activation, reconcile the current controller/project state and GitHub PR head and retain its package ID, reservation history, normalized root causes, evidence, approvals, session provenance and lease. **Do not reset counters, re-admit the active package, rebase it into this documentation branch or switch models mid-repair.**
- This adoption branch intentionally does not modify `docs/PLAN.md`, EP plans, the EP-01A feature branch, product infrastructure, UAT or product QA. The separate product QA `glm-5.3-flash` pin and its historical QA evidence are unchanged pending their own controlled migration.

## Compatibility gap before controller activation

The current `.github/workflows/ci.yml` runs FAST/STANDARD/FULL through **one** GitHub Actions workflow named `OpenJM CI` with a manual `suite` input and tier-dependent jobs. Framework v1.8.3's `ci_workflows` admission maps tiers to declared *workflow identities*; identical identities alone do not prove which suite ran.

**Do not invent distinct identities or relabel a STANDARD run FULL.** Before activating the new controller on a product package, implement and independently test a trustworthy tier discriminator: e.g., authorized distinct workflow identities with unchanged job coverage or an explicitly reviewed controller verification of GitHub dispatch-suite evidence. Preserve the existing FAST/ STANDARD/FULL cost boundaries, required jobs, exact SHA, real model/live gates, and FULL-after-independent-review chronology. Treat the gap as **BLOCKED FOR CONTROLLER ACTIVATION**, not permission to bypass or weaken a gate.

## Safe activation sequence (separate approved change)

1. Close or safely checkpoint PR #113; record the live repair budget and authoritative CI/acceptance facts without mutating them.
2. Resolve CI tier identity/provenance in a **separate scoped PR**; demonstrate negative tests for STANDARD-as-FULL, wrong suite/workflow, head drift, missing jobs, early FULL and superseded runs.
3. Choose an approved **new** package boundary, reconcile its plan revision, role routes, exact CI job/workflow mapping, account/channel approvals and appropriate acceptance scope. Admit that package only after human authorization; no retroactive acceptance.
4. Validate installation/route/ledger identity in both Hermes profiles and test the controller end-to-end on an isolated sandbox before applying it to an actual product PR. Require complete separation of tenant data and product QA from development-agent records.
5. Record tested commit/run IDs and human merge decision; leave unrelated ongoing work and deployment unchanged. Any conflict requires an explicit hold, not automated migration.

## Evidence and ownership

- This PR's only intended changes are `framework.lock`, this document and a README pointer. It is **documentation-only**; a clean FAST gate plus link/YAML consistency checks are suitable for the reference change itself. It does **not** attest to product release FULL or controller activation.
- Human owner must separately approve merging this documentation PR and, later, any CI compatibility/activation PR. Onboarding other products remains independent.
