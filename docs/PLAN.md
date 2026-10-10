# OpenJM Enterprise AI — Active Plan

Observed at accepted `main@c5ed073a332da7a8dd120dc3d85bf8e2328a0511` on 2026-10-10.

## Active objective

Move from accepted feature functionality to a commercially deployable, maintainable and handoff-ready product without reopening accepted feature lanes.

Issue #45 remains the historical/functionality umbrella. `docs/ACCEPTANCE_MATRIX.md` is the current evidence map. The active focus is REL1.

## Execution order

1. **REL1-A — reproducible deployment:** normalize version identity; reconcile release inputs; prove Linux/Windows delivery; deterministic bootstrap and health verification.
2. **REL1-B — packaging:** installer/package experience; release manifest + SHA-256 checksums; offline bundle where required; blocked-registry install proof.
3. **REL1-C — lifecycle:** upgrade/rollback; backup/restore; doctor/support-bundle; repeatable diagnosis.
4. **REL1-D — handoff:** documentation normalization; threat model; API/integration index; troubleshooting/incident/release runbooks; final persona/browser journeys; zero-tribal-knowledge rehearsal.
5. **Rahkia specialist integration — after REL1:** preserve the stable inference contract; verify M1/M2/M3 attribution, runtime routing, hardware/security/performance and production topology gates.

## Required discipline

- Do not reopen accepted BV/INF/M/E functionality without a reproduced defect or explicit product decision.
- Use focused verification during iteration, one fast CI on the final integrated head, and one full exact-head CI at a true acceptance boundary.
- Do not run duplicate CI merely for another green result.
- Keep Workspace isolated.
- Preserve fail-closed authorization and evidence-before-model invariants.
- Mark live/manual gates NOT RUN until actually executed.
- Product complete means feature complete, installable, upgradable, recoverable, documented, maintainable and handoff-ready.
