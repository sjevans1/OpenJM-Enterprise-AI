# OpenJM Enterprise AI — Product Roadmap

Roadmap baseline: `main@8e09ab586113648f606b8927f22a8b444d3cb581`, checked 2026-10-26. Accepted EP-00 administration safety correction and roadmap documentation are on main; EP-01A prototypes, EP-01B/C and later enterprise packages are not accepted. See [Developer Handoff](DEVELOPER_HANDOFF.md) for current open PR dispositions.

## Current phase: enterprise workflows and consistent experience

The accepted foundation is substantial, but the owner's manual review identified practical workflow gaps and inconsistent UI quality. The next programme completes those journeys and actual enterprise integrations. It is **PLANNED**, with acceptance **NOT RUN** for the new scope.

Accepted foundation includes VS1-VS8, BV1-BV6, M1-M3, INF1-A/B/C, E1-E3, delegated support-content access and the REL1 release/handoff work. See the [acceptance matrix](ACCEPTANCE_MATRIX.md) for exact candidate and environment evidence; this plan does not restamp old live passes onto new code.

## Next: enterprise productization

The [active plan](PLAN.md) links the [bounded package backlog](plan/ENTERPRISE_PRODUCTIZATION.md), [whole-application UI standard](design/UI_QUALITY_STANDARD.md) and [journey acceptance contract](qa/ENTERPRISE_PRODUCT_ACCEPTANCE.md).

| Milestone | Outcome |
| --- | --- |
| M0 | Completed: PR #109 reviewed-action and administration workflow corrections merged, with main CI passed |
| M1 | Shared premium experience across every existing UI surface, including mobile and failure states |
| M2 | Versioned governance, tenant-wide resource lifecycle, policy-first Knowledge, real identity onboarding and resumable company setup |
| M3 | Real SQL Server, MinIO/S3 and individually selected ERP/warehouse adapters with source-specific live qualification |
| M4 | Complete provider tenant lifecycle, entitlements/service management and bounded support workflows |
| M5 | Integrated product, live negative cases, affected deployment/recovery and independent handoff accepted |

Exact ERP/warehouse vendors and supported source/auth versions are decisions at package entry. They are not currently supported merely because they appear on the roadmap. Row/column governance, billing/payment processing and unselected Microsoft services are not implicit additions.

## Accepted REL1 lineage to preserve

### REL1-A — reproducible deployment
Canonical release identity; deterministic inputs; supported Linux/Windows delivery; production bootstrap; health/readiness verification.

### REL1-B — installer/appliance packaging
Guided install experience; offline/air-gapped bundle where required; release manifest + checksums; bundled dependencies/model artifacts as required by profile.

### REL1-C — upgrade, rollback and operations lifecycle
Upgrade path; rollback boundaries; backup/restore integration; doctor/support-bundle tooling; failure diagnosis and recovery.

### REL1-D — repository normalization and handoff
Current architecture/status/README; threat model; API/integration index; troubleshooting/incident/release runbooks; final golden journeys; zero-tribal-knowledge handoff rehearsal.

## Deferred: Rahkia specialist integration

Keep the application-to-Rahkia cutover with the later specialist phase. This programme uses the supported model-gateway contract and retains real-model acceptance; it does not silently adopt a new runtime. The specialist phase later qualifies runtime call paths, M1/M2/M3 attribution, hardware, security, performance and supported deployment topologies.

## Product decisions retained for v1

- `public`/`internal` with `tenant_visible=true` are tenant-wide within the tenant boundary.
- source-level classification is the v1 governance unit.
- explicit General/Knowledge/Data/Hybrid modes remain until implicit routing is proven safe.
- Workspace remains a standalone product and repository.
