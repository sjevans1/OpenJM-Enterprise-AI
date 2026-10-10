# Offline bundle structure (design)

**Status: proposed for REL1. Not implemented.** The repository does not yet ship
an offline bundle and `scripts/install-linux.sh` does not yet accept one. This
document defines the structure the REL1 packaging work should produce. It is
grounded in what a connected install fetches today
([CONNECTED_VS_AIRGAPPED.md](CONNECTED_VS_AIRGAPPED.md)); every requirement here
exists because a connected step would otherwise need the network.

## Purpose

Carry everything a connected install downloads, plus integrity metadata, so the
same `install-linux.sh` can install on a host with no public egress. The bundle
is built on a connected host of the same OS and CPU architecture as the target.

## Layout

```
openjm-rel1-bundle-<version>-<arch>/
  BUNDLE-MANIFEST.json          # see RELEASE_MANIFEST.md
  SHA256SUMS                    # checksums for every file in the bundle
  source/
    openjm-<version>.tar.gz     # the repository at the release commit
  wheels/
    *.whl                       # backend runtime + build dependencies
    requirements.lock           # the exact set install-linux.sh installs
  npm/
    node_modules.tar.gz         # or an offline npm cache for `npm ci`
    frontend-dist.tar.gz        # optional: a prebuilt frontend, to skip Node
  images/
    openjm-backend-<version>.tar
    openjm-proxy-<version>.tar
    postgres-16.tar             # docker save output, loaded with docker load
  models/
    embedding/                  # sentence-transformers/all-MiniLM-L6-v2 files
  install/
    install-offline.sh          # a thin wrapper that calls install-linux.sh
                                # with offline sources (pip --no-index, no npm
                                # registry, docker load)
```

## Contents and why each is present

| Item | Required because | Source of the requirement |
| --- | --- | --- |
| Repository source tarball | the installer builds from a checkout | `docs/INSTALLATION.md` |
| Python wheels + a lock | `pip install -e ".[dev]"` would reach PyPI | `scripts/install-linux.sh` |
| Frontend dependencies or a prebuilt `dist` | `npm ci` would reach the registry | `scripts/install-linux.sh` |
| Container image tarballs | Compose would pull `python:3.11-slim`, `node:20-bookworm-slim`, `caddy:2`, `postgres:16` | `deploy/compose/Dockerfile`, `Dockerfile.proxy`, `docker-compose.prod.yml` |
| Embedding model files | first retrieval would download `sentence-transformers/all-MiniLM-L6-v2` | `backend/app/core/config.py` |
| Manifest and checksums | verify what crossed the air gap | [RELEASE_MANIFEST.md](RELEASE_MANIFEST.md) |

## What the bundle does not contain

- **Local model artifacts.** The `local` provider runs whatever OpenAI-compatible
  model the operator supplies on-prem; those files are not OpenJM's to ship.
- **The OIDC/SSO IdP.** The IdP is separate infrastructure; the target network
  must reach it.
- **The private remote model service.** Operator-provided.
- **Any secret.** The bundle carries no `.env`, no key material and no
  credentials. Secrets are provisioned on the target, exactly as for a connected
  install.

## Build procedure (proposed)

1. Check out the release commit on a connected build host.
2. Resolve Python wheels for the target platform (`pip download`), pin them in
   `requirements.lock`.
3. Build or capture the frontend dependency set (`npm ci` then archive
   `node_modules`, or build `frontend/dist` and archive it).
4. `docker save` the backend, proxy and PostgreSQL 16 images. The backend and
   proxy images are built from `deploy/compose/Dockerfile` and
   `Dockerfile.proxy`; PostgreSQL 16 is pulled from the registry.
5. Copy the embedding model directory from the build host's model cache.
6. Emit `BUNDLE-MANIFEST.json` (release identity, target arch, schema head) and
   `SHA256SUMS` over every file.
7. Archive the bundle directory and record the archive's own checksum.

## Install procedure (proposed)

1. Verify `SHA256SUMS` and `BUNDLE-MANIFEST.json` against the archive checksum.
2. Unpack the repository tarball.
3. Run the offline wrapper, which calls `install-linux.sh` with pip in
   `--no-index --find-links wheels/` mode, frontend install pointed at the local
   archive or using the prebuilt `dist`, and `docker load` for the images.
4. Provision secrets and the environment file as for a connected install.
5. Migrate and start.

## Verification the REL1 work must add

- A bundle installed on a host with the registries blocked reaches head and
  serves `/api/health` and `/api/ready`.
- `SHA256SUMS` detects a corrupted bundle file before install.
- The install makes no outbound call to PyPI, the npm registry, Docker Hub or
  Hugging Face. This is the property that defines "offline", so it must be
  tested by observing the install, not assumed from the flags.