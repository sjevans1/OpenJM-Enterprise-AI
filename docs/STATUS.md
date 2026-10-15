# Current Repository Status

Planning snapshot: `main@b45ca8dad5fa79d12eba20fefd4d7d6c76df24b9` on 2026-10-15. Read live GitHub state before resuming implementation; this snapshot is not a moving branch assertion.

## Current phase

The historical Issue #45 feature programme and REL1 release/handoff work form the accepted foundation. The owner's subsequent manual review opened an **enterprise workflow and UI consistency programme**, now documented and **PLANNED**.

Use [PLAN](PLAN.md) for execution order, the [package backlog](plan/ENTERPRISE_PRODUCTIZATION.md) for bounded implementation and the [UI standard](design/UI_QUALITY_STANDARD.md) for the whole-application quality contract. No new adapters or management journeys are implemented by this planning change.

## Current acceptance evidence

- [ACCEPTANCE_MATRIX](ACCEPTANCE_MATRIX.md), [REL1-B4](REL1_B4_ACCEPTANCE.md) and [ZTK1](ZTK1_ACCEPTANCE.md) retain the original accepted heads/artifacts and live evidence. Later code does not automatically inherit those passes.
- [PR #109](https://github.com/sjevans1/OpenJM-Enterprise-AI/pull/109), candidate `e3854d1e84230afb639cef4e3ed53a2ad515a203`, contains the initial reviewed-action, policy-editor, source-setup, operator-pagination and mobile/readability corrections. It is **draft / unmerged** at this snapshot.
- That candidate's [FAST CI run 38678716580](https://github.com/sjevans1/OpenJM-Enterprise-AI/actions/runs/38678716580) passed backend/frontend checks and three synthetic Chromium journeys. Live SSO/source/model acceptance was **NOT RUN for that change**.
- New programme journeys in [ENTERPRISE_PRODUCT_ACCEPTANCE](qa/ENTERPRISE_PRODUCT_ACCEPTANCE.md) are **NOT RUN**. Planning documents are not implementation evidence.

## Next work

1. EP-00: obtain owner disposition of #109 and record the accepted baseline.
2. EP-01: shared primitives/tokens and three reference journeys; then EP-02/03 migrate every existing UI surface.
3. Follow the explicit dependencies for governance, Knowledge intake, company setup, adapters and provider management. Resolve vendor/identity/publication decisions at the relevant package entry.
4. Qualify the integrated candidate and handoff under EP-14. Keep implementation status and evidence truthful; merge/deploy only with explicit authority.

## Explicitly deferred

- Rahkia application cutover and production GPU/runtime qualification.
- Per-object/table classification labels unless a concrete unsplittable mixed-sensitivity source requires them.
- Implicit mode routing until it meets the established safety/quality bar.
- Workspace implementation in this repository.

Historical VS4 pilot checkpoints remain historical evidence and should not be interpreted as the live project state.
