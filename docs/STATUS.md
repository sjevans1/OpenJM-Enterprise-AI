# Current Repository Status

**Remote snapshot checked 2026-10-26, not a live feed.** Accepted `main` at `8e09ab586113648f606b8927f22a8b444d3cb581`; post-merge [CI 38689459332](https://github.com/sjevans1/OpenJM-Enterprise-AI/actions/runs/38689459332) succeeded. Refresh GitHub before beginning work.

## Accepted foundation

Historical Issue #45, VS/BV/M/INF/E capability foundations and REL1/ZTK1 handoff are accepted **at their documented candidates**. PR [#109](https://github.com/sjevans1/OpenJM-Enterprise-AI/pull/109) merged the reviewed administration/workflow corrections; PR [#110](https://github.com/sjevans1/OpenJM-Enterprise-AI/pull/110) merged the enterprise productization plan. Previous REL1 production/browser proof is specific to frozen artifact `709ce47bf3ae1b7135446c376bcf312b25606b5c`, not every later tree.

## Active work — not accepted

- [Issue #111](https://github.com/sjevans1/OpenJM-Enterprise-AI/issues/111): EP-01 premium shared experience, component reuse and three product-plane journeys.
- [PR #113](https://github.com/sjevans1/OpenJM-Enterprise-AI/pull/113): EP-01A prototypes at `ce7825950d97dcead97670aa38165bb3e6048b60`; **unmerged**. Latest exact-head FAST [38695599875](https://github.com/sjevans1/OpenJM-Enterprise-AI/actions/runs/38695599875) and STANDARD [38695596235](https://github.com/sjevans1/OpenJM-Enterprise-AI/actions/runs/38695596235) **FAILED** in Chromium component-dialog probes. Unit tests/build and backend jobs passed. The source comparison proposes Radix for its tested dialog pattern; no general component adoption has been accepted.
- [#112](https://github.com/sjevans1/OpenJM-Enterprise-AI/pull/112) is a draft preliminary component survey; reconcile against #113's evidence, not a separate independent programme.
- [#114](https://github.com/sjevans1/OpenJM-Enterprise-AI/pull/114) framework-version documentation, [#115](https://github.com/sjevans1/OpenJM-Enterprise-AI/pull/115) docs CI optimization and stacked [#116](https://github.com/sjevans1/OpenJM-Enterprise-AI/pull/116) CI workflow identities are **draft/unmerged**. Do not assume that their behavior is deployed.

## Next work

1. Resolve the concrete #113 dialog probe failures at the actual head; prove keyboard, focus and accessible name/description behavior without loosening expected semantics. Qualify render and mock API behavior separately from production identity/data/model integration.
2. Obtain explicit component decision and owner visual acceptance before EP-01B. Do not treat an ADR recommendation as adoption approval.
3. Evaluate #112/#114–116 for duplication, stack order and maintenance cost; merge only approved, exact-head qualified work.
4. Execute EP-01B shared tokens/primitives and EP-01C integrated journeys in bounded packages, then follow EP-02–14 dependency order.

Refer to [handoff](DEVELOPER_HANDOFF.md), [plan](PLAN.md), [blockers](BLOCKED.md), [enterprise scope](plan/ENTERPRISE_PRODUCTIZATION.md), [UI quality](design/UI_QUALITY_STANDARD.md) and [journey acceptance](qa/ENTERPRISE_PRODUCT_ACCEPTANCE.md).

## Explicitly deferred / not claimed

- Rahkia application cutover and production GPU/model runtime qualification.
- New real SQL Server, MinIO/S3 or individually selected ERP/warehouse connector support until qualified.
- Implicit mode routing and expanded object/table classification without approved product decisions.
- Workspace Platform implementation in this repository.
- Customer-site live deployment, new release-artifact qualification and any new EP-01 journey not actually tested.

Past pilot/checkpoint files are historical evidence and are not active team execution instructions.
