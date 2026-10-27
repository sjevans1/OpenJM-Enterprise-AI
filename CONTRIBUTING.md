# Contributing to OpenJM Enterprise AI

This is the receiving team's **human-readable engineering contract**. Start with [Developer Handoff](docs/DEVELOPER_HANDOFF.md), [Architecture](docs/ARCHITECTURE.md), [Security](docs/SECURITY_POSTURE.md) and the [current execution plan](docs/PLAN.md).

## Working agreements

1. **Use the real current base.** Refresh remote `main`, inspect open PRs and avoid duplicate work. Keep feature work on a short-lived branch; one bounded objective per PR. Do not push directly to `main`, force-push another contributor's branch or merge without an authorized maintainer decision.
2. **Describe a business/user outcome.** Every issue/package identifies the actual persona and tenant/scope, existing behavior, acceptance journey, negative cases, release/integration impact and rollback. Follow `docs/plan/ENTERPRISE_PRODUCTIZATION.md` and the UI standard rather than inventing a parallel roadmap.
3. **Make the smallest maintainable change.** Separate UI, API, policy, migration and infrastructure concerns; add focused regression tests. Document nontrivial interfaces and update status/acceptance mappings in the same PR. Never rewrite historical PASS records as if they were retested.
4. **Preserve security and state semantics.** Tenant and content authorization belongs to the server, not a hidden button. State-changing actions require preview/review/confirm and server reconciliation. Be careful with identity, support delegation, stale revisions, immutable reports, read-only SQL, evidence scoping and audit. Never log secrets or sensitive source content.
5. **Use reusable, accessible UI only after validation.** Assess maintained components and licenses, keyboard/focus behavior, mobile reflow, load/error/denied/conflict states, install/bundle/offline effects and design-token fit. Treat upstream showcases as references, not completion proof.
6. **Test at the appropriate tier.** `docs/CI_TIERS.md` describes FAST, STANDARD and FULL; consult the actual checked-out `.github/workflows/ci.yml` before dispatch. Focused local tests + PR FAST support iteration; STANDARD qualifies a completed package; FULL is for frozen release/affected lifecycle validation under the actual project policy. Never claim a missing/skipped/failed job passed. For material security, identity, migration, offline-release or deployment changes, record all relevant additional checks.
7. **Keep delivery explicit.** No automatic release, protected-branch bypass, environment credential change, tenant bootstrap or live deployment without designated approval. All production settings are fail-closed.

## PR description template

Use the following headings in each implementation PR:

- **Outcome and scope:** user problem, behavior change and explicit exclusions.
- **Baseline:** issue, main/base SHA, PR head SHA and dependencies.
- **Authority and data:** affected tenant/identity/policy boundaries and test-owned data.
- **Tests and evidence:** exact commands, CI run links, changed-head status, screenshots/browser traces if UI, API negative tests, environment-dependent tests marked PASS/FAIL/NOT RUN and limitations.
- **Operational impact:** secrets, migrations, feature flags, install/upgrade/offline-bundle implications, rollback and support notes.
- **Review and decision:** open defects, required human visual/product/security disposition, merge approval status and next work.

## Local verification examples

Follow the canonical [Installation](docs/INSTALLATION.md) guide (Python 3.11, Node 22). With dependencies installed:

```bash
cd backend && python -m pytest -q tests/<affected_test>.py
python -m ruff check . --select E9,F63,F7,F82
cd ../frontend && npm test -- --run && npx tsc --noEmit && npm run build
```

Browser changes also require the relevant `npx playwright test` suites; full live integrations require a bounded, isolated environment and genuine source/IdP/model evidence. Prefer semantic behavior assertions over screenshots alone. Do not make CI green by deleting or skipping a failing relevant test.

## Where to document

- Current status and next actions: [STATUS](docs/STATUS.md), [PLAN](docs/PLAN.md), [BLOCKED](docs/BLOCKED.md).
- Feature design/acceptance: [enterprise plan](docs/plan/ENTERPRISE_PRODUCTIZATION.md), [UI quality](docs/design/UI_QUALITY_STANDARD.md), [journey acceptance](docs/qa/ENTERPRISE_PRODUCT_ACCEPTANCE.md).
- Operations and release: [CONFIGURATION](docs/CONFIGURATION.md), [OPERATIONS](docs/OPERATIONS.md), [RELEASE](docs/RELEASE.md), [BACKUP_RESTORE](docs/BACKUP_RESTORE.md), [UPGRADE_ROLLBACK](docs/UPGRADE_ROLLBACK.md).
- Exact historical evidence: [ACCEPTANCE_MATRIX](docs/ACCEPTANCE_MATRIX.md), [REL1-B4](docs/REL1_B4_ACCEPTANCE.md), [ZTK1](docs/ZTK1_ACCEPTANCE.md).

Historic engineering investigations can support context, but stale batch/pilot notes are not instructions to run today. Internal tool/vendor-specific development prompts are intentionally excluded from the current handover.
