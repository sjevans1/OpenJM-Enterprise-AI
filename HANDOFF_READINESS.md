# Handoff readiness — scope and evidence

**Checked 2026-10-26.** This page distinguishes the qualified historical baseline from current **unmerged** development. Begin with [Developer Handoff](docs/DEVELOPER_HANDOFF.md).

## Foundation delivered (candidate-specific)

- Versioned platform/tenant permissions, knowledge and data governance, model gateway, reports, administration and OpenJM operator surfaces are described in [Acceptance Matrix](docs/ACCEPTANCE_MATRIX.md).
- REL1 production installer/bundle, recovery and isolated production-host/browser qualification are documented in [REL1-B4 Acceptance](docs/REL1_B4_ACCEPTANCE.md), specifically frozen release artifact `709ce47bf3ae1b7135446c376bcf312b25606b5c`.
- Independent operator handover evidence exists at [ZTK1](docs/ZTK1_ACCEPTANCE.md).
- These completed qualifications do **not** automatically accept later `main` changes, an unmerged PR, a new deployment profile or an untested source/model.

## Current handover scope

- Current accepted `main` observed: `8e09ab586113648f606b8927f22a8b444d3cb581`.
- [#109](https://github.com/sjevans1/OpenJM-Enterprise-AI/pull/109) workflow/administration safeguards and [#110](https://github.com/sjevans1/OpenJM-Enterprise-AI/pull/110) enterprise plan are **merged**.
- [#113](https://github.com/sjevans1/OpenJM-Enterprise-AI/pull/113) EP-01A accessible UI prototypes are **unmerged** and latest exact-head CI failed. Pending component adoption/visual acceptance. See [Current Blockers](docs/BLOCKED.md).
- Draft [#112](https://github.com/sjevans1/OpenJM-Enterprise-AI/pull/112), [#114](https://github.com/sjevans1/OpenJM-Enterprise-AI/pull/114), [#115](https://github.com/sjevans1/OpenJM-Enterprise-AI/pull/115), and stacked [#116](https://github.com/sjevans1/OpenJM-Enterprise-AI/pull/116) are pending review and are not part of `main`.
- EP-01B/EP-01C and subsequent enterprise integrations remain planned. Rahkia application cutover is deferred.

## Receiving-team readiness checks

- Can install a source development checkout without customer data or ambient developer-machine state.
- Knows the three product authority planes and can describe tenant/read-only/evidence restrictions.
- Can explain Python 3.11/Node 22, PostgreSQL/OIDC production prerequisite, runtime preflight and model-gateway isolation.
- Can identify exact test runs, current PR failures, offline-release scope and which live tests are **NOT RUN** for a candidate.
- Can propose an evidence-backed correction for the first pending EP package, with a recovery/rollback plan and no automatic merge.

**Current status: documentation handover prepared; receiving-team live rehearsal NOT RUN.** Final acceptance belongs to the team/product owner after they verify the checkout/environment. Do not treat this page as a new live release test.
