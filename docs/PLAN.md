# OpenJM Enterprise AI — Active Plan

Planning snapshot: `main@b45ca8dad5fa79d12eba20fefd4d7d6c76df24b9` on 2026-10-15. New programme status: **PLANNED**, not implemented or accepted by this document.

## Active objective

Complete practical enterprise workflows and apply one premium UI standard across the employee workspace, company administration and OpenJM provider Control Center. The owner's manual review identified accidental changes and incomplete workflows despite the substantial accepted backend foundation.

Issue #45 and REL1 retain their historical acceptance. This is an explicitly requested advancement programme, not a restart of those milestones. Start with:

- [Enterprise productization plan](plan/ENTERPRISE_PRODUCTIZATION.md): requirements, sequence, bounded EP-00–14 packages, decisions and handoff.
- [UI quality standard](design/UI_QUALITY_STANDARD.md): shared components, reviewed actions, visual/accessibility rules and every UI surface.
- [Enterprise journey acceptance](qa/ENTERPRISE_PRODUCT_ACCEPTANCE.md): positive/failure journeys, fixture/live distinction and evidence requirements.
- [Acceptance matrix](ACCEPTANCE_MATRIX.md): historical capability and deployment evidence at its recorded candidates.

## Execution order

1. **EP-00:** [PR #109](https://github.com/sjevans1/OpenJM-Enterprise-AI/pull/109) merged at `bc8f02e5662378a6c954871c042233c635e43e63`; record the exact post-merge STANDARD result and reconcile any residual behavior before declaring this baseline accepted.
2. **EP-01–03:** first compare Watermelon UI, Radix/React Aria/Base UI/shadcn and existing code; approve the reuse/adapt/build ADR, component strategy and shared reference journeys; then migrate every existing workspace, tenant-admin and provider UI with rendered-state review.
3. **EP-04–09:** versioned policy/effective access, tenant resource lifecycle, policy-first Knowledge intake, identity onboarding, common source contract and guided company setup. EP-09's common source contract precedes EP-08 setup acceptance; see the detailed dependencies.
4. **EP-10–12:** qualify real SQL Server, MinIO/S3 and separately selected ERP/warehouse integrations. Support claims are per verified source/version/auth combination.
5. **EP-13:** complete provider tenant, commercial/service and bounded-support journeys. It can follow EP-03/04 and the applicable identity package before all adapters finish.
6. **EP-14:** frozen integrated candidate, all-screen review, live governance/source/model journeys, affected REL1 lifecycle checks and independent handoff.
7. **Rahkia specialist integration remains deferred:** preserve the inference seam and later qualify routing, M1/M2/M3 attribution and production topology with the specialist team.

## Required discipline

- Preserve accepted BV/INF/M/E and REL1 behavior except for the explicit product changes and reproduced defects documented in this programme.
- Follow [CI_TIERS](CI_TIERS.md): focused/FAST iteration, STANDARD package qualification, FULL plus applicable live gates at a frozen acceptance boundary.
- Do not run duplicate CI merely for another green result.
- Keep Workspace isolated.
- Preserve fail-closed authorization and evidence-before-model invariants.
- Mark live/manual gates NOT RUN until actually executed.
- All new UI must use the shared experience contract; visual consistency and usable failure/recovery paths are required for completion.
- Product complete means usable business journeys, consistent UI, enforced governance, real integration proof, install/upgrade/recovery and maintainable handoff.
- Keep reviewable candidates separate from accepted main. Merge and deployment require explicit owner authority.
