# REL1-A — Reproducible Deployment

Status: implementation package.

Baseline: `main@afbefff38ac60b459a88d4aeba8b254d9788d2e3`.

## Objective

Establish one release identity and one deterministic supported bootstrap contract
for Linux and Windows/WSL2 before REL1-B builds distributable/offline bundles.

REL1-A does **not** claim that Python dependencies are fully locked for offline
reproduction. The backend currently has no lock file. Dependency resolution,
wheelhouse/image capture and the disconnected bundle belong to REL1-B.

## Contract

1. `backend/app/version.py::PRODUCT_VERSION` is the canonical product version.
   Backend package metadata, frontend package metadata and npm lock metadata must
   match it.
2. The release train is `REL1`.
3. Supported Python is exactly 3.11.
4. Frontend builds require Node.js major 22, matching CI.
5. Linux install accepts only `development` or `production`.
6. Production install does not install backend development/test extras.
7. Windows/WSL2 passes the selected profile and frontend choice to the same Linux
   installer rather than silently defaulting to development.
8. Every install finishes with `scripts/verify-rel1a-install.py`.
9. Existing production preflight remains fail-closed and runs before migration.

## Acceptance

Automated:
- `backend/tests/test_rel1a_reproducible_deployment.py`;
- existing VS8 preflight/system tests;
- frontend tests/typecheck/build;
- fast CI on the integrated PR head;
- one full exact-head CI at the merge boundary.

Manual/runtime qualification for REL1-A:
- Linux development install from a clean checkout;
- Linux production preflight with deliberately incomplete sample secrets must
  fail closed until provisioned;
- Windows/WSL2 detection and profile propagation;
- successful post-install verifier on the supported path.

## Explicit NOT RUN / deferred

- offline registry-blocked install: REL1-B;
- Python dependency lock/wheelhouse: REL1-B;
- release manifest/checksum generation: REL1-B;
- final end-user/browser persona journey: REL1-D;
- Rahkia application cutover: after REL1.
