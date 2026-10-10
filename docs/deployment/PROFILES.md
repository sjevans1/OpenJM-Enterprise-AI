# Supported deployment profiles

OpenJM Enterprise AI supports exactly three profiles. A profile is a supported,
tested way to run the product, not a set of developer defaults. The profile
selector is `OPENJM_DEPLOYMENT_PROFILE` (`development` or `production`). The
accepted profile contract is [../DEPLOYMENT_PROFILES.md](../DEPLOYMENT_PROFILES.md);
this document focuses on the install/topology choice for each profile and on the
model-serving sub-profiles.

## Profile summary

| Profile | `deployment_profile` | Identity | Database | Model | Typical install |
| --- | --- | --- | --- | --- | --- |
| Local developer | `development` | dev identity (`auth_mode=dev`) | SQLite under `./data/` | any OpenAI-compatible endpoint, plain HTTP allowed | manual `uvicorn` + Vite dev server; `scripts/install-linux.sh --profile development` |
| Self-hosted production | `production` | OIDC/SSO (`auth_mode=oidc`) | PostgreSQL | `local` or `private_remote` | systemd unit or Docker Compose; `scripts/install-linux.sh --profile production` |
| Private model-serving | `production` | as production | as production | `local` or `private_remote` explicitly | same as production |

A `production` profile whose configuration is unsafe refuses to serve: the same
validator runs as a CLI (`python -m app.core.preflight`) and in the application
lifespan. Development is validated structurally and is clearly labelled as
non-production.

## Production topology 1: system service (systemd)

Preferred when the operator manages a single Linux host without a container
runtime.

- Install target: `/opt/openjm` (see `deploy/compose/Dockerfile` for the layout
  the image uses; the unit references `/opt/openjm/backend`).
- Unit: `deploy/systemd/openjm.service`.
- Config: `EnvironmentFile=/etc/openjm/openjm.env` (mode `0600`, owned by the
  `openjm` service user).
- Start: `ExecStartPre` runs the preflight, `ExecStart` runs uvicorn on
  `127.0.0.1:8000`.
- Restart: `Restart=on-failure`, `RestartSec=5`, bounded by
  `StartLimitIntervalSec=120` / `StartLimitBurst=5`.
- Limits and hardening: `MemoryMax=4G`, `CPUQuota=200%`, `TasksMax=512`,
  `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=full`, `ProtectHome`,
  `ReadWritePaths=/var/lib/openjm /var/backups/openjm`.
- Ingress/TLS: an operator-provided reverse proxy (the Caddy example in
  `deploy/compose/Caddyfile` is a usable reference).

## Production topology 2: containers (Docker Compose)

Preferred when a container runtime is available. A strong Compose deployment is
chosen over Kubernetes; Kubernetes is not required by the product.

- File: `deploy/compose/docker-compose.prod.yml`.
- Services: `db` (PostgreSQL 16), `backend` (built from
  `deploy/compose/Dockerfile`), `proxy` (Caddy with the SPA baked in from
  `deploy/compose/Dockerfile.proxy`).
- Secrets: the `openjm_db_password` Compose secret; the database password has a
  single source, shared by `db` and `backend`.
- Storage: named volumes `openjm-db`, `openjm-data`, `openjm-keys`,
  `openjm-backups`, `caddy-data` (mount points in
  [PORTS_STORAGE_SECRETS.md](PORTS_STORAGE_SECRETS.md)).
- Healthchecks: `db` uses `pg_isready`; `backend` uses `/api/health`.
- Bring-up: `docker compose -f deploy/compose/docker-compose.prod.yml up -d --build`.
  Once images exist, use `up -d` without `--build`.

## Production on Windows: WSL2

A Windows-native backend is not a supported profile. The supported Windows path
is Windows + WSL2, and `scripts/install-wsl2.ps1` delegates to the same
`scripts/install-linux.sh` used on a native Linux host, so the two cannot drift.
See [WINDOWS_INSTALLER.md](WINDOWS_INSTALLER.md).

## Private model-serving sub-profiles

Model provider routing is deployment configuration, never a client BYO-key
feature; the credential never reaches the browser.

### Local / offline model (`model_provider_mode=local`)

An OpenAI-compatible endpoint on the local/on-prem network. Plain HTTP is
allowed for a local endpoint (`model_allow_insecure_http`, development-only
classification). The default development target is `http://127.0.0.1:18080/v1`.
Once the model artifacts are present, the product operates without reaching a
public model provider.

### Operator-managed private remote API (`model_provider_mode=private_remote`)

An operator-managed private OpenAI-compatible service. Production requires
HTTPS/TLS for the endpoint and a non-placeholder server-side credential. The
provider mode and model id are surfaced by readiness, but the base URL host and
the credential never are.

Both sub-profiles fail closed: `model_provider_fallback=none` is enforced in
production, so a provider failure surfaces as an error rather than a silent
route to an unapproved public provider.

## What is not a supported profile

- Kubernetes or any orchestrator beyond Compose/systemd.
- A Windows-native backend (use WSL2).
- A public frontier-model API reached with an end-user-supplied key.
- Running a `production` profile with development identity or a SQLite metadata
  database: the preflight refuses it.