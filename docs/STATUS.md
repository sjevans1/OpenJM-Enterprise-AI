# Current Repository Status

Observed at accepted `main@c5ed073a332da7a8dd120dc3d85bf8e2328a0511` on 2026-10-10.

## Current phase

The broad Issue #45 business-value/functionality programme is substantially complete. The project is now at the **REL1 productization and final integrated qualification boundary**.

Accepted product state includes BV1-BV6, M1-M3, INF1-A/B/C, delegated support-content access, and the accepted VS1-VS8/E1-E3 foundation.

## Current acceptance evidence

- Final BV6-B exact-head full CI: run `38026945641`, Backend / Python 3.11 and Frontend / Node 22 success.
- #75 merged as `a047a991cc8882f3f8c0f444387ea190acb689d4`.
- Governance decision cleanup #74, refreshed acceptance matrix #73 and REL1 preparation #63 subsequently merged.
- Current main: `c5ed073a332da7a8dd120dc3d85bf8e2328a0511`.

## Next work

1. Execute REL1 productization: reproducible package/install, supported Windows/Linux delivery, offline/air-gapped path where applicable, release manifest/checksums, version normalization, doctor/support-bundle, upgrade/rollback and recovery acceptance.
2. Run final integrated browser journeys on the packaged candidate.
3. Run the scoped live environment qualifications recorded in `docs/ACCEPTANCE_MATRIX.md`.
4. Only after product completion, hand the application-to-Rahkia integration to the specialist team.

## Explicitly deferred

- Rahkia application cutover and production GPU/runtime qualification.
- Per-object/table classification labels unless a concrete unsplittable mixed-sensitivity source requires them.
- Implicit mode routing until it meets the established safety/quality bar.
- Workspace implementation in this repository.

Historical VS4 pilot checkpoints remain historical evidence and should not be interpreted as the live project state.
