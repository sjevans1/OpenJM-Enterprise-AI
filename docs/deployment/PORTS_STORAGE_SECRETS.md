# Ports, storage, secrets and configuration inventory

The network ports, storage paths, secrets and configuration keys a deployment
must provide. Grounded in `backend/app/core/config.py`, `backend/app/core/preflight.py`,
`.env.example`, `.env.production.example`, `deploy/compose/docker-compose.prod.yml`,
`deploy/compose/Caddyfile`, `deploy/compose/entrypoint.sh`,
`deploy/systemd/openjm.service` and `frontend/vite.config.ts`.

## Required ports

| Port | Default | Fixed or configurable | Owner | Binding | Source |
| --- | --- | --- | --- | --- | --- |
| 8000 | 8000 | fixed in the unit/Dockerfile (`--port 8000`) | backend API | systemd binds `127.0.0.1:8000`; Compose publishes `127.0.0.1:8000:8000` | `deploy/systemd/openjm.service`, `deploy/compose/Dockerfile` |
| 443 | 443 | `OPENJM_HTTPS_PORT` (Compose) | reverse proxy HTTPS | `"${OPENJM_HTTPS_PORT:-443}:443"` | `deploy/compose/docker-compose.prod.yml` |
| 80 | 80 | `OPENJM_HTTP_PORT` (Compose) | reverse proxy HTTP | `"${OPENJM_HTTP_PORT:-80}:80"` | `deploy/compose/docker-compose.prod.yml` |
| 5432 | 5432 | `OPENJM_DB_PORT` (Compose, internal) | PostgreSQL | internal Compose network only | `docker-compose.prod.yml`, `entrypoint.sh` |
| 18080 | 18080 | `OPENJM_MODEL_BASE_URL` | local model endpoint | loopback by default | `config.py`, `.env.example` |
| 5173 | 5173 | `OPENJM_OIDC_REDIRECT_URI`, CORS defaults | Vite dev server (development only) | loopback | `vite.config.ts`, `config.py` |
| 5174 | 5174 | fixed | `scripts/serve_frontend.py` built-frontend helper | loopback | `scripts/serve_frontend.py` |

Notes:

- The backend and the reverse proxy are the only components with externally
  meaningful ports. In the Compose topology the backend is published on loopback
  (`127.0.0.1:8000`) and reached over the internal network by the proxy; the
  proxy is the public boundary.
- Port 443/80 on the proxy is followed by TLS termination. Caddy obtains a
  certificate for `OPENJM_SITE_ADDRESS` (default `openjm.example.com`); a
  local/HTTP run overrides it, for example `OPENJM_SITE_ADDRESS=":80"`.
- The OIDC provider is external; its port is whatever the operator's issuer
  URL specifies and is not a port OpenJM opens.
- The frontend development server port (5173) is a development concern only.
  The production SPA is served by the reverse proxy on 443.
- There is no fixed outbound port. The backend reaches the model provider and
  the OIDC provider on their configured URLs.

## Storage

### Development (`deployment_profile=development`)

Paths resolve under the repository root (`REPO_ROOT / "data"`), so a checkout is
self-contained. `Settings.ensure_directories()` creates them at load.

| Purpose | Default path | Setting |
| --- | --- | --- |
| Metadata database | `./data/openjm.db` | `OPENJM_DATABASE_URL` |
| Uploaded/local documents | `./data/uploads` | `OPENJM_UPLOAD_DIR` |
| Vector / Knowledge index | `./data/vector` | `OPENJM_VECTOR_PATH` |
| Credential-vault key | `./data/credentials.key` | `OPENJM_CREDENTIAL_KEY_FILE` |
| Backups | `./data/backups` | `OPENJM_BACKUP_DIR` |

### Production (`deployment_profile=production`)

Paths are absolute and outside the repository, so an upgrade replaces code
without touching data.

| Purpose | Default path | Setting |
| --- | --- | --- |
| Uploaded/local documents | `/var/lib/openjm/uploads` | `OPENJM_UPLOAD_DIR` |
| Vector / Knowledge index | `/var/lib/openjm/vector` | `OPENJM_VECTOR_PATH` |
| Backups | `/var/backups/openjm` | `OPENJM_BACKUP_DIR` |
| Credential-vault key | `/etc/openjm/credentials.key` | `OPENJM_CREDENTIAL_KEY_FILE` |

### Container volumes (Compose)

| Volume | Mount | Holds |
| --- | --- | --- |
| `openjm-db` | `/var/lib/postgresql/data` | PostgreSQL data directory |
| `openjm-data` | `/var/lib/openjm` | uploads and vector index |
| `openjm-keys` | `/etc/openjm` | credential-vault key |
| `openjm-backups` | `/var/backups/openjm` | backups |
| `caddy-data` | `/data` | Caddy certificates and state |

The systemd unit grants write access only to `/var/lib/openjm` and
`/var/backups/openjm` (`ReadWritePaths`), with `ProtectSystem=full` and
`ProtectHome=true`.

## Secrets

No production secret is committed. `.env.production.example` holds placeholders
only, and `.env` is gitignored. In production each secret is supplied through an
environment variable or a `0600` file (systemd `EnvironmentFile`, Compose
`secrets:`, or a mounted file).

| Secret | Setting | Provisioned as | Classification |
| --- | --- | --- | --- |
| OIDC client secret | `OPENJM_OIDC_CLIENT_SECRET` | env / secret file | secret |
| Credential-vault key material | `OPENJM_CREDENTIAL_ENCRYPTION_KEY` or `credential_key_file` | inline env or `0600` file | secret |
| Model provider credential | `OPENJM_MODEL_API_KEY` | env / secret file | secret |
| Operational-surface bearer | `OPENJM_OPS_TOKEN` | env / secret file | secret |
| Database password | `OPENJM_DB_PASSWORD` / `openjm_db_password` Compose secret | Compose secret file `deploy/compose/secrets/openjm_db_password.txt` | secret |

The Compose stack has a single database-password source: the
`openjm_db_password` secret is mounted into both `db` and `backend`, and
`entrypoint.sh` assembles `OPENJM_DATABASE_URL` from it so the two can never
disagree.

Preflight refuses a production start when a secret-classified setting is missing
or is a stock placeholder (`auth_mode`, OIDC secret, vault key, provider
credential). The report names the setting, never a secret value.

## Configuration inventory

`backend/app/core/preflight.py` declares the classified surface
(`CONFIGURATION_SURFACE`). Categories: **required**, **optional**,
**development-only**, **secret**, **restart-required**, **safe-default**.

| Setting | Class | Production requirement |
| --- | --- | --- |
| `deployment_profile` | required | `production` |
| `auth_mode` | required | `oidc` (dev identity refused) |
| `oidc_issuer` | required | present |
| `oidc_client_id` | required | present |
| `oidc_client_secret` | secret | present, not a placeholder |
| `oidc_redirect_uri` | required | present |
| `database_url` | required | PostgreSQL |
| `credential_encryption_key` | secret | inline key or key file, not a placeholder |
| `credential_key_file` | restart-required | fallback key path |
| `upload_dir` | restart-required | persistent path |
| `vector_path` | restart-required | persistent path |
| `backup_dir` | restart-required | backup destination |
| `session_ttl_seconds` | safe-default | (default 28800) |
| `cors_origins` | required | explicit list, no `*` |
| `trusted_hosts` | required | explicit list, no `*` |
| `trust_proxy_headers` | required | honour `X-Forwarded-*` behind the proxy |
| `model_provider_mode` | required | `local` or `private_remote` |
| `model_base_url` | required | backend-only endpoint |
| `model_name` | required | model identifier |
| `model_api_key` | secret | required and non-placeholder for `private_remote` |
| `model_allow_insecure_http` | development-only | plain HTTP only for a local endpoint |
| `model_provider_fallback` | required | `none` |
| `model_timeout_seconds` | safe-default | bounded provider timeout |
| `max_request_body_bytes` | safe-default | request body bound (default 8,000,000) |
| `max_upload_bytes` | safe-default | upload bound (default 25,000,000) |
| `rate_limit_enabled` | safe-default | must be `true` in production |
| `retention_enabled` | optional | opt-in lifecycle |
| `metrics_enabled` | safe-default | expose Prometheus-style metrics |
| `ops_token` | secret | bearer for the detailed operational surface; hidden (404) in production without it |
| `product_name` | optional | white-label display text |
| `organization_name` | optional | white-label display text |
| `release_id` | optional | build/release identifier |

Settings beyond the classified surface that a deployment still depends on
(`vector_collection`, `embedding_model`, `rag_*`, `scheduler_*`,
`retention_*_days`, `document_lease_seconds`, `action_*`, `oidc_*` cache/skew,
`trust_proxy_headers`) are defined in `backend/app/core/config.py` with safe
defaults. See [../CONFIGURATION.md](../CONFIGURATION.md) for the failure
conditions and secret rotation, and
[../DEPLOYMENT_PROFILES.md](../DEPLOYMENT_PROFILES.md) for the profile contract.