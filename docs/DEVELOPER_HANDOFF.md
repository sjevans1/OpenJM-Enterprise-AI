# OpenJM Enterprise AI — Developer Handoff

**Team entrypoint; snapshot checked 2026-10-26 against remote GitHub.** Before making a decision, refresh `main`, the open PRs, issue #111 and CI checks; this is a dated handover, not an automated status feed.

## 1. Product and boundaries

OpenJM Enterprise AI is a self-hostable, multi-tenant enterprise assistant that combines governed **Knowledge** (documents and RAG), **Data** (registered read-only structured sources), **Chat/Hybrid** and **Reports**. It uses OpenJM-owned authorization, tenant/group/classification policies, evidence-aware orchestration, audit trails, an OpenAI-compatible **server-side** model gateway, and three distinct experience planes:

1. **Employee workspace**: Chat, Knowledge, Data, Hybrid, evidence and saved reports.
2. **Company administration**: users/groups, classifications, source registration and governed changes.
3. **OpenJM operator control plane**: tenant/usage/capacity and service management, with no implicit access to customer content.

**Do not combine these permission planes.** Provider-operator authority is not customer-data authority; client-admin status is scoped to its tenant. Workspace Platform is a different repository/product. Rahkia application cutover is deferred and must not be implied by generic model-gateway support.

## 2. Versioned baseline and what is not yet delivered

At the handover audit the last accepted `main` is `8e09ab586113648f606b8927f22a8b444d3cb581` (PR #110 enterprise roadmap merged). PR #109 administration safety corrections were merged earlier as `bc8f02e5662378a6c954871c042233c635e43e63`. The post-merge main CI evidence for #110 is [run 38689459332](https://github.com/sjevans1/OpenJM-Enterprise-AI/actions/runs/38689459332) (success). This does **not** qualify any unmerged branch.

Historical acceptance: `docs/ACCEPTANCE_MATRIX.md`, `docs/REL1_B4_ACCEPTANCE.md` and `docs/ZTK1_ACCEPTANCE.md`. The REL1 clean-production-host/browser proof belongs to frozen artifact `709ce47bf3ae1b7135446c376bcf312b25606b5c`; do not restamp it as a pass for current or future application trees.

**Unmerged candidates at this snapshot (not part of the delivered product):**

| PR | Work | Handover disposition |
| --- | --- | --- |
| [#112](https://github.com/sjevans1/OpenJM-Enterprise-AI/pull/112) | Preliminary component-library screening | Draft, research only. Reconcile against the richer ADR in #113; avoid duplicate documents/contradictory adoption decisions. |
| [#113](https://github.com/sjevans1/OpenJM-Enterprise-AI/pull/113) | EP-01A rendered admin/resource prototypes and Radix comparison | **Not accepted.** Head `ce7825950d97dcead97670aa38165bb3e6048b60`. Both latest PR FAST [38695599875](https://github.com/sjevans1/OpenJM-Enterprise-AI/actions/runs/38695599875) and STANDARD [38695596235](https://github.com/sjevans1/OpenJM-Enterprise-AI/actions/runs/38695596235) failed frontend Chromium dialog probes. Do not merge on stale early greens. |
| [#114](https://github.com/sjevans1/OpenJM-Enterprise-AI/pull/114) | Proposed external framework-version adoption | Draft; not part of `main`. Review cross-repo policy compatibility before adopting; product development must not require any particular assistant/orchestration vendor. |
| [#115](https://github.com/sjevans1/OpenJM-Enterprise-AI/pull/115) | Documentation-only FAST routing | Draft, unmerged. |
| [#116](https://github.com/sjevans1/OpenJM-Enterprise-AI/pull/116) | Distinct STANDARD/FULL CI workflow identities | Draft and stacked on #115. Validate dependency/merge order, do not assume it is active on `main`. |

The [EP-01 tracker #111](https://github.com/sjevans1/OpenJM-Enterprise-AI/issues/111) owns continuation. The approved plan is `docs/plan/ENTERPRISE_PRODUCTIZATION.md` (EP-00–14), `docs/design/UI_QUALITY_STANDARD.md` and `docs/qa/ENTERPRISE_PRODUCT_ACCEPTANCE.md`. Planned SQL Server, MinIO/S3, ERP, warehouse and new onboarding capabilities are **not** supported just because they are in the plan. EP-01B shared components and EP-01C integrated reference journeys have not been accepted.

## 3. First-day reading and environment

1. Read this document, [CONTRIBUTING.md](../CONTRIBUTING.md) and [current status](STATUS.md).
2. Read [ARCHITECTURE.md](ARCHITECTURE.md), [SECURITY_POSTURE.md](SECURITY_POSTURE.md), [CONFIGURATION.md](CONFIGURATION.md).
3. Read [INSTALLATION.md](INSTALLATION.md) and [DEPLOYMENT_PROFILES.md](DEPLOYMENT_PROFILES.md) before setup; then [OPERATIONS.md](OPERATIONS.md), [BACKUP_RESTORE.md](BACKUP_RESTORE.md), [UPGRADE_ROLLBACK.md](UPGRADE_ROLLBACK.md).
4. Read [CI_TIERS.md](CI_TIERS.md), the actual [workflow](../.github/workflows/ci.yml) and the [acceptance contract](qa/ENTERPRISE_PRODUCT_ACCEPTANCE.md). Treat CI configuration on the checked-out commit as authoritative.

Baseline platform/toolchain: **Python 3.11, Node 22, React 18/TypeScript/Vite, PostgreSQL for production, Keycloak/OIDC in production, production-owned private model gateway**. The backend uses migration files under `backend/migrations/versions/`; source code is under `backend/app/`; UI is under `frontend/src/`. Installer/ops scripts are under `scripts/`; production Compose assets are under `deploy/compose/`. Do not work from obsolete pilot branches.

Developer start (Linux/WSL2; follow installation documentation for options and environment prerequisites):

```bash
git clone https://github.com/sjevans1/OpenJM-Enterprise-AI.git
cd OpenJM-Enterprise-AI
git switch main
scripts/install-linux.sh --profile development
```

Use disposable/synthetic local data. The installer preserves an existing `.env`; never point acceptance or migration tests at customer or shared production databases. A **production** installation needs an assembled, integrity-checked REL1 release payload, not a bare clone. No default platform admin/password is shipped; platform bootstrap is an explicit one-time authorized operation.

Run focused checks after changes and consult the actual CI workflow. Representative commands:

```bash
# Backend (with Python 3.11 development environment active)
cd backend
python -m pytest -q tests/<affected_test_module>.py
python -m ruff check . --select E9,F63,F7,F82

# Frontend
cd ../frontend
npm ci --no-audit --no-fund
npm test -- --run
npx tsc --noEmit
npm run build
```

For browser-impacting work, use the repository Playwright configuration and include keyboard, viewport, adverse-state and actual request-effect coverage. The current production application does not include the **unmerged** `?prototype=ep01a` development-only interface.

## 4. First eligible development work

**P0 — reconcile existing work before opening new scope.**

- Inspect #113 exact-head failing Playwright traces. Latest observed blockers include unresolved Radix dialog description linkage, React Aria opener-focus restoration and Base UI dialog focus containment. Determine actual implementation vs harness problems; preserve accessibility requirements. Do not disguise a failure as a flaky-run pass.
- Evaluate the proposed Radix dialog ADR against real behavior and visuals. Approval is **not recorded**; before adoption, inspect responsive identity labels, incomplete axe findings and developer-only prototype exclusion, then request product-owner decision.
- Keep #112 as research reference until it is reconciled/closed; do not merge duplicate overlapping plans.
- Independently evaluate the stacked #115/#116 CI proposals and draft #114. Do not modify mandatory release/identity qualification silently. Retain a bounded repair budget and avoid redundant FULL unless the release/security scope demands it.

**P1 — EP-01B**, only after EP-01A passes required review and its component decision is accepted: semantic tokens, accessible shared primitives and a component/state gallery, without wholesale React/Tailwind migration. **P2 — EP-01C**: three integrated employee, tenant-admin and operator journeys. Only then expand through EP-02–14 per dependencies in the plan.

For every package record its issue, frozen PR head/base, scope, screenshots/trace, test commands/outcomes, negative cases, reviewer disposition, and whether human approval/merge occurred. Never call local mock UI proof a live IdP/connector/model pass.

## 5. Security and operational non-negotiables

- Fail closed on tenant, group, classification, policy and connector authorization; deny cross-tenant requests, stale revisions, revoked resources and unbounded support access.
- User actions that change rights or resources require **draft → explicit review → confirm → persisted result**; changing a dropdown must never commit a permission change.
- Backend-only model credentials. No browser-direct database/model calls; no permissive CORS, anonymous production admin, implicit operator-to-content escalation or public-model fallback.
- SQL integrations remain governed **read-only**; saved report history/snapshots are immutable at the documented boundaries.
- Use PostgreSQL and OIDC for production; secrets via external secret files/managers, never Git/PRs or screenshots. Run preflight before service start.
- Restore, upgrade and offline-install evidence belong to their exact tested artifact. Keep a safe recovery plan before migrations or deployment.
- Do not claim customer UAT is production-ready. Any external test tunnel/host must be independently assessed, authenticated, and kept outside this source repo's secrets.

## 6. Handover acceptance checklist

A receiving team should demonstrate in a clean checkout (and document which gates actually ran): ability to bootstrap the supported development profile; explain the three authority planes; run backend/frontend checks and a browser adverse-state journey; understand migration/backup/rollback; identify the exact current open blockers; explain how to reproduce #113; and propose the smallest next PR without altering accepted tenant-security controls. If an environment-dependent/live gate is unavailable, record **NOT RUN** and its prerequisites.

**Ownership:** repository maintainers control product decisions, rights changes, merges, production activation, domain/DNS, secrets and releases. Contributors propose evidence-backed changes through reviewable PRs. Do not silently merge or deploy.

## 7. Provenance and cleanup limitations

Historical acceptance documents remain as exact-artifact evidence, not the team's execution instructions. This handover intentionally excludes internal prompt transcripts and development-agent operating procedures. **Git commits and closed/open PR discussion may still contain historical information** because deleting a file from the latest tree is not a Git-history rewrite. Public visibility and secret exposure require separate repository/history/access review; rotate any credential whose value was committed or posted in an exposed conversation.

**Before sending the GitHub URL as the formal handover:** the repository owner must check GitHub Security → Secret scanning / alerts (where available), investigate any findings, review past commits and PR conversations for accidentally exposed secrets, and verify that the named recipient’s code-use rights are documented in the signed consulting/licensing terms. Automated latest-tree checks are not a history-level credential clearance. A public repository does not, by itself, grant third parties commercial reuse rights; third-party package licenses still apply. No general open-source license has been issued in this tree.
