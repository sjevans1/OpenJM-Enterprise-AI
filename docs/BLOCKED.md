# Current blockers and pending qualification

**Checked against GitHub 2026-10-26.** Earlier REL1 blocker notes are historical; REL1/ZTK1 foundation was accepted at its frozen qualified artifacts. This document tracks **new** enterprise-productization work and must be refreshed before each handover milestone.

## Blocking EP-01A acceptance

1. **PR #113 frontend browser checks failing at exact head** `ce7825950d97dcead97670aa38165bb3e6048b60`: [PR FAST 38695599875](https://github.com/sjevans1/OpenJM-Enterprise-AI/actions/runs/38695599875) has 24/25 Chromium tests passing; the Radix `aria-describedby` resolution probe failed. [STANDARD 38695596235](https://github.com/sjevans1/OpenJM-Enterprise-AI/actions/runs/38695596235) has 22/25 passing; Radix described-by, React Aria opener-focus restoration and Base UI tab-containment probes failed. Tests/build/backend jobs passed. Root-cause and re-run the affected probes—do not present early green runs as qualification of this head.
2. **Component choice is not signed off.** Radix is the proposed first dialog primitive, but the ADR/visual decision is open; EP-01B shared component adoption must wait for acceptance. Resolve/qualify incomplete axe observations and confirm mobile identity visibility and dev-only prototype exclusion on the reviewed head.
3. **Open draft PR sequence:** #112 preliminary research, #114 framework adoption, #115 docs CI and #116 stacked workflow identities. Validate integration, avoid duplicating #112/#113, and prevent documentation from presenting unmerged workflow features as active.

## Environment-dependent gates not implied by merged foundation

- New UI prototype tests use synthetic fixtures and do **not** prove live OIDC, tenant data sources, model answers or production delivery.
- The frozen REL1 artifact has its own accepted host/browser evidence; changed release inputs and future production candidate require fresh scoped qualification.
- New source engines and Rahkia deployment need separate real runtime/security/performance evidence and explicit authorization.

## Not blockers for source development

Rahkia application cutover is **deferred**; Workspace integration is separately scoped; unsupported vendor adapters are planned work, not ready-to-ship defects.

See [Developer Handoff](DEVELOPER_HANDOFF.md), [STATUS](STATUS.md), [CI_TIERS](CI_TIERS.md) and the authoritative [issue #111](https://github.com/sjevans1/OpenJM-Enterprise-AI/issues/111).
