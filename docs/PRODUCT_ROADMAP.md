# OpenJM Enterprise AI — Product Roadmap

Observed at accepted `main@c5ed073a332da7a8dd120dc3d85bf8e2328a0511` on 2026-10-10.

## Current phase: productization and final qualification

The feature-heavy post-VS8 phase is substantially complete. The roadmap is now deliberately narrow.

Accepted foundation includes VS1-VS8; BV1-BV6; M1-M3; INF1-A/B/C; E1-E3; and delegated support-content access. Current detail is in `docs/ACCEPTANCE_MATRIX.md`.

## Next: REL1

### REL1-A — reproducible deployment
Canonical release identity; deterministic inputs; supported Linux/Windows delivery; production bootstrap; health/readiness verification.

### REL1-B — installer/appliance packaging
Guided install experience; offline/air-gapped bundle where required; release manifest + checksums; bundled dependencies/model artifacts as required by profile.

### REL1-C — upgrade, rollback and operations lifecycle
Upgrade path; rollback boundaries; backup/restore integration; doctor/support-bundle tooling; failure diagnosis and recovery.

### REL1-D — repository normalization and handoff
Current architecture/status/README; threat model; API/integration index; troubleshooting/incident/release runbooks; final golden journeys; zero-tribal-knowledge handoff rehearsal.

## After REL1: Rahkia specialist integration

Do not connect the current product to Rahkia during REL1. After product completion, wire the stable inference contract to Rahkia and qualify model/runtime call paths, M1/M2/M3 attribution, hardware, security, performance and supported deployment topologies.

## Product decisions retained for v1

- `public`/`internal` with `tenant_visible=true` are tenant-wide within the tenant boundary.
- source-level classification is the v1 governance unit.
- explicit Chat/Knowledge/Data/Hybrid modes remain until implicit routing is proven safe.
- Workspace remains a standalone product and repository.
