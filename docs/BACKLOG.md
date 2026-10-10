# Current Backlog

Observed at accepted `main@c5ed073a332da7a8dd120dc3d85bf8e2328a0511` on 2026-10-10.

## Must complete before product handoff

| Work | Reason |
| --- | --- |
| REL1 reproducible deployment/package | Product must be installable without reconstructing the environment manually. |
| Version normalization | Runtime/package/frontend release identity must agree. |
| Release manifest + checksums | Required for deterministic release provenance and transfer integrity. |
| Offline/air-gapped installation path | Required for supported disconnected deployments. |
| Doctor/support-bundle tooling | Reduce operator tribal knowledge. |
| Upgrade/rollback/recovery qualification | Handoff requires a supported lifecycle. |
| Final browser golden journeys | Close persona acceptance on the packaged candidate. |

## Should complete before or with handoff

Threat model; API/integration index; troubleshooting/incident runbook; final release runbook; targeted maintainability refactors only where they reduce handoff risk.

## Deferred

Rahkia application integration; per-object/table classification; automatic implicit mode routing; Workspace development.
