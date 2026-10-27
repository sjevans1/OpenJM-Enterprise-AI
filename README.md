# OpenJM Enterprise AI

OpenJM Enterprise AI is a multi-tenant enterprise assistant combining governed documents/Knowledge (RAG), registered structured Data, Chat/Hybrid interactions and saved Reports. Server-side policy, evidence, audit and model routing are core product boundaries.

**Incoming developers:** start with the [Developer Handoff](docs/DEVELOPER_HANDOFF.md) and [Contributing Guide](CONTRIBUTING.md). **Operators:** start with [Installation](docs/INSTALLATION.md), [Configuration](docs/CONFIGURATION.md) and [Operations](docs/OPERATIONS.md).

## What the product does

- **Employee workspace**: persistent conversations, explicit General/Knowledge/Data/Hybrid modes, citations/evidence, governed saved reports and exports.
- **Customer administration**: users, groups, document classifications, data sources and reviewed permission/policy changes.
- **OpenJM control plane**: tenant operations, metadata-level oversight, usage/entitlements and bounded support without implicit customer-content access.

OpenJM's backend integrates with an OpenAI-compatible, **server-side** model runtime. The provider remains replaceable; no particular coding tool or assistant is part of the product's operational dependency. The Knowledge layer uses a replaceable engine behind OpenJM-owned retrieval/authorization. Production requires PostgreSQL, OIDC/SSO, explicit origins/hosts and safe secrets. Workspace Platform is a standalone product/repository.

## Start here

| Need | Document |
| --- | --- |
| Incoming team: exact accepted baseline, pending PRs, next work | [Developer Handoff](docs/DEVELOPER_HANDOFF.md) |
| Branches, code review, security, tests, PR evidence | [CONTRIBUTING](CONTRIBUTING.md) |
| Current work / blockers / priorities | [STATUS](docs/STATUS.md), [BLOCKED](docs/BLOCKED.md), [PLAN](docs/PLAN.md) |
| Technical architecture and boundaries | [ARCHITECTURE](docs/ARCHITECTURE.md), [SECURITY_POSTURE](docs/SECURITY_POSTURE.md) |
| Setup and runtime configuration | [INSTALLATION](docs/INSTALLATION.md), [DEPLOYMENT_PROFILES](docs/DEPLOYMENT_PROFILES.md), [CONFIGURATION](docs/CONFIGURATION.md) |
| Operations, backup, recovery and release | [OPERATIONS](docs/OPERATIONS.md), [BACKUP_RESTORE](docs/BACKUP_RESTORE.md), [UPGRADE_ROLLBACK](docs/UPGRADE_ROLLBACK.md), [RELEASE](docs/RELEASE.md) |
| CI and acceptance | [CI_TIERS](docs/CI_TIERS.md), [CI_WORKFLOW](docs/CI_WORKFLOW.md), [ACCEPTANCE_MATRIX](docs/ACCEPTANCE_MATRIX.md) |
| Active enterprise roadmap and UI design | [Enterprise Plan](docs/plan/ENTERPRISE_PRODUCTIZATION.md), [UI Standard](docs/design/UI_QUALITY_STANDARD.md), [Journey Acceptance](docs/qa/ENTERPRISE_PRODUCT_ACCEPTANCE.md) |

## Development quick start

Supported contributor toolchain: **Python 3.11 and Node 22**. Linux and Windows/WSL2 use the supported installation guide; a production install is built from a validated **release bundle**, not a bare clone.

```bash
git clone https://github.com/sjevans1/OpenJM-Enterprise-AI.git
cd OpenJM-Enterprise-AI
git switch main
scripts/install-linux.sh --profile development
```

The installer and application preflight validate the selected profile. Configure the local, non-production `.env` from the sample; never commit secrets. Start services according to [Installation](docs/INSTALLATION.md) and [Deployment Profiles](docs/DEPLOYMENT_PROFILES.md). **Do not run live acceptance or schema migrations against a shared customer environment.**

Representative verification in a correctly provisioned checkout:

```bash
cd backend
python -m pytest -q tests/<affected_test>.py
python -m ruff check . --select E9,F63,F7,F82
cd ../frontend
npm ci --no-audit --no-fund
npm test -- --run
npx tsc --noEmit
npm run build
```

The actual [GitHub Actions CI](.github/workflows/ci.yml) and [CI tiers](docs/CI_TIERS.md) define hosted gates. Use Chromium/Playwright for browser-impacting changes and separate live IdP/database/model qualification when relevant.

## Current project position

The accepted foundation includes the VS, BV, inference, governance, release and zero-tribal-knowledge handoff programmes at their **recorded tested candidates**. The clean-production/browser acceptance belongs to the frozen REL1 artifact `709ce47bf3ae1b7135446c376bcf312b25606b5c`; do not infer that every later branch was retested.

**As checked on 2026-10-26:** accepted `main` at `8e09ab586113648f606b8927f22a8b444d3cb581` (merged enterprise development plan #110). Enterprise experience work **EP-01A is unmerged**: [PR #113](https://github.com/sjevans1/OpenJM-Enterprise-AI/pull/113) still has failed Chromium component probes at its exact head. The component/adoption decision is pending. Other open draft candidates #112 and #114–116 also require owner review. See the [handoff](docs/DEVELOPER_HANDOFF.md) for evidence and dispositions.

The new enterprise programme (EP-00–14) is **planned/partially under review**, not wholly delivered: premium UI consistency, user/company onboarding, source lifecycle and real SQL Server, MinIO/S3 and selected ERP/warehouse adapter qualifications remain explicit work. **Rahkia application cutover is deferred** to a separately authorized specialist phase.

## Engineering invariants

- Fail closed on identity, tenant, role, classification, evidence and connector authorization.
- Mutations of user roles, permissions or managed resources require visible draft → review → confirm → server reconciliation; field selection alone never changes access.
- Models and databases are reached by the backend, never directly by the browser; no public fallback model in production.
- Structured access remains governed read-only; snapshots and completed histories obey their immutable contracts.
- No production claim without matching artifact-specific, environment-specific evidence. Mark unavailable gates **NOT RUN** rather than assuming success.
- No merge, promotion, tenant bootstrap or secrets access without the appropriate human approval.

Historical planning, dated review and test reports support traceability, but they are not instructions to resume an obsolete pilot. A new contributor should follow the current documents linked above.
