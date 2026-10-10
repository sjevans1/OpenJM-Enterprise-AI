# REL1-B — Offline Package and Release Integrity

Baseline: `main@ad6fc9abdf2f27d2892e688e1cf10938a595c14b`.

## Objective

Turn the REL1-A install contract into a target-platform release artifact that can
cross an air gap with integrity metadata and install without public registries.

## Core bundle contract

A connected build host produces a bundle containing:

- exact tracked source archive from the release commit;
- target-platform backend runtime wheelhouse;
- generated `requirements.lock` pinning every wheel with SHA-256;
- prebuilt frontend `dist` archive so the target does not require Node/npm;
- self-contained bundle verifier and offline installer;
- `BUNDLE-MANIFEST.json` with release/version/commit/schema/platform identity;
- `SHA256SUMS` covering the manifest and every payload file.

The air-gapped installer:

- verifies manifest and SHA-256 before extraction;
- installs Python only with `--no-index --find-links --require-hashes`;
- performs no npm install;
- stages production without pretending sample secrets are valid;
- can complete the development install/migration path from local artifacts only.

## Container packaging gate

The production Compose topology also requires frozen container images. REL1-B is
not complete until the backend, proxy and PostgreSQL images can be built/pulled
on the connected build host, exported with `docker save`, recorded in the
manifest/checksum set, loaded on the target, and started without registry access.

This is a real acceptance gate, not inferred from the core bundle tests.

## Acceptance

Automated:
- release bundle verifier accepts intact payload and rejects corruption;
- offline installer contract contains no PyPI/npm fallback;
- proxy image build uses Node 22;
- full existing regression remains green.

Runtime:
1. Build a real bundle from the frozen candidate on Linux x86_64.
2. Verify the generated bundle.
3. Install the development profile into a clean target with public registry
   access blocked; prove no PyPI/npm/Hugging Face access occurs during install.
4. Build/export/load the production Compose images and prove startup without
   Docker Hub access.
5. Confirm `/api/health`, `/api/ready`, release identity and schema head.

## Explicit boundaries

- Bundle is target-platform specific; no claim of universal wheels.
- OIDC, private model runtime and secrets remain operator-provided.
- OpenJM does not ship the customer's local model weights.
- Rahkia application cutover remains after REL1.
- Signing is not silently implied by SHA-256; provenance signing remains a
  separate key-management decision.
