# Deployment and operations foundation (REL1)

Observed against accepted product baseline `a047a991cc8882f3f8c0f444387ea190acb689d4` on 2026-10-10.

This directory is the REL1 engineering and design foundation for deploying,
operating, upgrading and releasing OpenJM Enterprise AI. It collects the
deployment contract into a single navigable set of documents so an operator or
a packaging engineer does not have to reconstruct it from code and scattered
notes.

Scope and honesty note: this is a documentation and design lane. It adds no
product code. Where a document describes something that already exists in the
repository, it names the exact file or setting it is grounded in. Where it
describes a design that is proposed for REL1 and not yet implemented (the
offline bundle, the release manifest and checksum files, a consolidated
`doctor` command), that is stated explicitly in the document. Nothing here
claims an installer, bundle or command that the repository does not contain.

## Supported topologies

OpenJM runs as exactly one product with two supported production topologies and
a non-production developer topology:

| Topology | Where it runs | Compose / service artefacts |
| --- | --- | --- |
| Development | a developer host or CI | none (uvicorn + Vite dev server) |
| Production, system service | a Linux host | `deploy/systemd/openjm.service` |
| Production, containers | a Linux host, Docker Compose | `deploy/compose/docker-compose.prod.yml` |
| Production, Windows | Windows host via WSL2 | `scripts/install-wsl2.ps1` delegates to the Linux installer |

Kubernetes is not required and is not a supported default.

## Document index

| Document | Covers |
| --- | --- |
| [COMPONENT_INVENTORY.md](COMPONENT_INVENTORY.md) | every runtime component, its process/artefact and its source of truth |
| [DEPENDENCY_GRAPH.md](DEPENDENCY_GRAPH.md) | the production service dependency graph and start order |
| [PORTS_STORAGE_SECRETS.md](PORTS_STORAGE_SECRETS.md) | required ports, storage paths, secrets and the configuration inventory |
| [PROFILES.md](PROFILES.md) | the supported deployment profiles and their install options |
| [CONNECTED_VS_AIRGAPPED.md](CONNECTED_VS_AIRGAPPED.md) | what a connected install needs versus an air-gapped install |
| [BACKUP_RESTORE_CONTRACT.md](BACKUP_RESTORE_CONTRACT.md) | the backup/restore contract and the manifest schema |
| [UPDATE_ROLLBACK_CONTRACT.md](UPDATE_ROLLBACK_CONTRACT.md) | the update/rollback contract and its limits |
| [LINUX_INSTALLER.md](LINUX_INSTALLER.md) | the Linux installer architecture |
| [WINDOWS_INSTALLER.md](WINDOWS_INSTALLER.md) | the Windows + WSL2 installer architecture |
| [OFFLINE_BUNDLE.md](OFFLINE_BUNDLE.md) | proposed offline bundle structure |
| [RELEASE_MANIFEST.md](RELEASE_MANIFEST.md) | proposed release manifest and checksum design |
| [../operations/HEALTH_DOCTOR_CHECKS.md](../operations/HEALTH_DOCTOR_CHECKS.md) | health, readiness and doctor checks |
| [../operations/DIAGNOSTICS_AND_SUPPORT_BUNDLE.md](../operations/DIAGNOSTICS_AND_SUPPORT_BUNDLE.md) | structured logging, correlation ids, diagnostics and the proposed support bundle |
| [../handoff/REL1_FOUNDATION.md](../handoff/REL1_FOUNDATION.md) | handoff summary for the REL1 packaging work |

## Existing operational documents

These documents already describe the accepted VS8 operational surface. This
foundation references them rather than restating them; read them for the
runtime behaviour and use the documents above for the packaging and deployment
contract.

| Document | Covers |
| --- | --- |
| [../CONFIGURATION.md](../CONFIGURATION.md) | configuration surface, classifications, production failure conditions, secret rotation |
| [../DEPLOYMENT_PROFILES.md](../DEPLOYMENT_PROFILES.md) | the accepted profile contract (development, production, private model serving) |
| [../INSTALLATION.md](../INSTALLATION.md) | the accepted install and bootstrap steps |
| [../OPERATIONS.md](../OPERATIONS.md) | health/readiness endpoints, structured logging, metrics, scheduler |
| [../BACKUP_RESTORE.md](../BACKUP_RESTORE.md) | the accepted backup/restore and disaster-recovery behaviour |
| [../UPGRADE_ROLLBACK.md](../UPGRADE_ROLLBACK.md) | the accepted upgrade and rollback behaviour |
| [../RELEASE.md](../RELEASE.md) | version identity, packaging model, white-label configuration |
| [../ACCEPTANCE.md](../ACCEPTANCE.md) | the release acceptance gate |

## Grounding sources

Every factual claim in this foundation traces to one of these repository files:

- `backend/app/core/config.py` (the settings surface and defaults).
- `backend/app/core/preflight.py` (the fail-closed configuration contract and
  the classified configuration surface, `CONFIGURATION_SURFACE`).
- `backend/app/main.py` (middleware, router registration, lifespan preflight).
- `backend/app/api/system.py` (`/api/health`, `/api/ready`, `/api/ready/detail`,
  `/api/version`, `/api/config/public`, `/api/metrics`).
- `backend/app/ops/backup.py` and `backend/app/ops/upgrade.py` (the backup and
  upgrade libraries).
- `backend/app/migrations_runner.py` (the versioned migration runner and the
  unknown-revision refusal).
- `backend/app/version.py` (the single source of release identity).
- `.env.example` and `.env.production.example` (the development and production
  configuration templates).
- `deploy/compose/` (Dockerfile, Dockerfile.proxy, docker-compose.prod.yml,
  Caddyfile, entrypoint.sh).
- `deploy/systemd/openjm.service` (the production systemd unit).
- `scripts/install-linux.sh`, `scripts/install-wsl2.ps1`,
  `scripts/openjm_ops.py` (the installer and operator entry points).