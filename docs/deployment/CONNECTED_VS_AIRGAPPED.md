# Connected versus air-gapped installation

The network requirements of an OpenJM installation differ depending on whether
the host can reach public package registries and model hubs. This document
states what the current installer requires (a connected install) and what an
air-gapped install must supply instead.

The connected install is implemented today (`scripts/install-linux.sh`). The
air-gapped install is a **design** for REL1: the repository does not yet ship an
offline bundle, and the installer does not yet accept one. That gap is the
subject of [OFFLINE_BUNDLE.md](OFFLINE_BUNDLE.md).

## Connected installation

A connected install has outbound access to public registries during install and,
for some configurations, at runtime.

### Install-time network use

| Step | Reaches | Source |
| --- | --- | --- |
| Clone the repository | GitHub (`git clone`) | `docs/INSTALLATION.md` |
| Backend dependencies | PyPI (`pip install -e ".[dev]"`) | `scripts/install-linux.sh` |
| Frontend dependencies | npm registry (`npm ci`) | `scripts/install-linux.sh` |
| Container base images (Compose) | Docker registry (`python:3.11-slim`, `node:20-bookworm-slim`, `caddy:2`, `postgres:16`) | `deploy/compose/Dockerfile`, `Dockerfile.proxy`, `docker-compose.prod.yml` |

### Runtime network use

| Component | Reaches | Notes |
| --- | --- | --- |
| Model provider (`private_remote`) | the operator's private endpoint | internal by design; HTTPS in production |
| Model provider (`local`) | an on-prem endpoint | may be plain HTTP; no public egress required |
| Embedding model | Hugging Face or a local cache | default `sentence-transformers/all-MiniLM-L6-v2`; the first retrieval downloads it unless the model is already cached |
| OIDC/SSO | the IdP issuer / discovery / JWKS URLs | `OPENJM_OIDC_ISSUER`, `OPENJM_OIDC_DISCOVERY_URL`, `OPENJM_OIDC_JWKS_URL` |
| Connectors | customer source systems | governed, read-only paths |

The embedding model download is the one runtime dependency that can surprise an
otherwise-internal deployment: a host with no model cache and no Hugging Face
access cannot index documents on first use. A connected install can pre-populate
the cache by building it once with network access; an air-gapped install must
ship the model files.

## Air-gapped installation

An air-gapped host has no public egress. Every artefact the install needs, and
every artefact a runtime feature needs, must be present locally before the
install starts.

### Requirements

| Requirement | Why | Supplied by the proposed bundle |
| --- | --- | --- |
| The repository source | the installer builds from source | repository tarball in the bundle |
| Python wheels | `pip install` cannot reach PyPI | a wheelhouse and `pip --no-index --find-links` |
| Frontend dependency cache | `npm ci` cannot reach the registry | a pre-populated `node_modules` or an offline npm cache |
| Container base images | Compose cannot pull | `docker save` image tarballs, loaded with `docker load` |
| Embedding model files | retrieval cannot download on first use | the model directory copied into the vector/embedding cache path |
| Local model artifacts | the `local` provider must run on-prem | operator-provided model files, not part of the OpenJM bundle |
| An internal OIDC/SSO IdP | `auth_mode=oidc` requires a reachable issuer | operator-provided; no public IdP |
| Internal model endpoint | `private_remote` or `local` | operator-provided; no public model API |
| Integrity checksums | verify what crossed the air gap | the bundle checksum file (see [RELEASE_MANIFEST.md](RELEASE_MANIFEST.md)) |

### What is deliberately not required

- A public frontier-model API. The `private_remote` and `local` provider modes
  are the air-gapped intent; `model_provider_fallback=none` guarantees no silent
  public route.
- A public OIDC IdP. Any reachable OIDC issuer works; the IdP is separate from
  the product.
- Workspace or any external SaaS. Workspace is optional and API-only.

## Transition path

The cleanest path to an air-gapped install is: build the offline bundle on a
connected host of the same architecture, verify its checksums, transfer it, then
run the same installer against the unpacked bundle with registry access disabled.
For this to hold, `scripts/install-linux.sh` must accept an offline source for
pip, npm and the images, and must not attempt any network fetch when told the
host is disconnected. That installer change is part of the REL1 packaging work,
not part of this documentation lane.