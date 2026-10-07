# Supported deployment profiles (VS8 Workstream A)

OpenJM Enterprise AI supports exactly three deployment profiles. A profile is a
supported, tested way to run the product — not a set of developer defaults. The
backend refuses to serve a **production** profile whose configuration is unsafe
(see `docs/CONFIGURATION.md`).

The target is an independently deployable product. Workspace remains optional
and API-only; nothing in a profile requires Workspace or a public frontier-model
API.

---

## 1. Local developer profile

Fast, clearly non-production.

| Aspect | Value |
| --- | --- |
| `OPENJM_DEPLOYMENT_PROFILE` | `development` |
| Identity | dev identity (`auth_mode=dev`) resolving a single provisioned local principal |
| Database | repo-local SQLite under `./data/` |
| Model | any OpenAI-compatible endpoint; plain HTTP allowed |
| Secrets | repo-local key file under `./data/` (gitignored) |

Properties:

- fast local start (`uvicorn app.main:app --reload`);
- development-only identity is explicit and is provisioned as a real tenant
  membership, so it exercises the same authorization path as production;
- non-production defaults are labelled as such in logs and in the preflight
  report;
- **no accidental production claim**: the profile name is surfaced in
  `/api/health`, `/api/version` and the preflight report.

## 2. Self-hosted production profile

Supported on a Linux host and on Windows via WSL2. This is the customer
deployment profile.

| Aspect | Value |
| --- | --- |
| `OPENJM_DEPLOYMENT_PROFILE` | `production` |
| Identity | production OIDC/SSO (`auth_mode=oidc`); dev identity is refused |
| Database | PostgreSQL application-metadata database |
| Storage | persistent upload and vector directories outside the repo |
| Model | local/offline **or** OpenJM-hosted private remote API (see profile 3) |
| Ingress | reverse proxy / TLS boundary (Caddy example in `deploy/compose`) |
| Secrets | explicit provisioning via secret files (`0600`), never committed |
| Health | `/api/health` (liveness), `/api/ready` (readiness) |
| Restart | systemd `Restart=on-failure` with a start-limit, or Compose `restart: unless-stopped` |
| Limits | `MemoryMax`, `CPUQuota`, request/upload bounds |

Deployment options, in preference order:

1. systemd unit (`deploy/systemd/openjm.service`) with a venv install;
2. Docker Compose (`deploy/compose/docker-compose.prod.yml`) with PostgreSQL and
   a Caddy TLS boundary.

Kubernetes is **not** required and is not the default. The existing product does
not demonstrate a need for it, so a strong Compose/system-service deployment is
preferred over platform complexity.

## 3. Private model-serving profile

OpenJM supports **two first-class private inference patterns**. This is
deployment/provider routing, not a client BYO-API-key feature.

### 3a. Local / offline model

An OpenAI-compatible model running locally or on-prem (`model_provider_mode=local`).
No public model API is required; once the model artifacts are present the product
operates without Internet access to a model provider. Knowledge/Data/Hybrid and
the accepted product functions operate against the configured model subject to
its capability.

### 3b. OpenJM-hosted private model API

The OpenJM backend is configured with a private OpenAI-compatible endpoint and a
server-side credential for an OpenJM-operated/private LLM service
(`model_provider_mode=private_remote`).

Requirements enforced by code and preflight:

- the model base URL, model identifier and credential are **deployment/operator
  configuration only**;
- the credential is **never** exposed to the browser/client, returned by a public
  configuration endpoint, persisted in frontend storage, or supplied by an end
  user;
- all client inference requests terminate at the OpenJM backend, which performs
  model-provider routing server-side;
- provider selection is explicit and auditable enough to identify which model
  handled a request (`/api/ready` reports provider mode and model id) **without
  logging secrets**;
- production requires HTTPS/TLS for a remote private endpoint; plain HTTP is
  development/local-only;
- **provider failure fails visibly and never silently routes to an unapproved
  public provider** (`model_provider_fallback=none` is enforced in production).

The frontend may show safe operator-selected display metadata (product name,
version). It never receives provider base URLs, API keys, bearer tokens or
routing configuration.
