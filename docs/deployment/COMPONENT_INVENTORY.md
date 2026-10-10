# Deployment component inventory

The components that make up a running OpenJM Enterprise AI deployment, what each
one is at runtime, and where its definition lives in the repository. Every entry
is grounded in a file that exists in this repository at the REL1 baseline.

## Runtime components

| Component | Runtime form | Definition file | Notes |
| --- | --- | --- | --- |
| Backend API | `uvicorn app.main:app` on Python 3.11 | `backend/app/main.py`, `backend/pyproject.toml` | The only process that reaches the model endpoint, the metadata database, the credential vault and the storage volumes. Requires Python `>=3.11,<3.12`. |
| Application-metadata database | PostgreSQL 16 (production) or SQLite (development) | `backend/app/core/config.py` (`database_url`) | Holds OpenJM's own metadata only; never a customer's connected structured sources. Production preflight requires PostgreSQL. |
| Database schema owner | Alembic migration runner | `backend/app/migrations_runner.py`, `backend/migrations/versions/` | Applied at startup by `app.db.init_db` and by `scripts/openjm_ops.py upgrade`. Revisions `0001` through `0021`. |
| Frontend SPA | Static build, `frontend/dist` | `frontend/`, built by `npm run build` | React/Vite/TypeScript. Served as static files by the reverse proxy, never by the backend. |
| Reverse proxy / TLS boundary | Caddy 2 | `deploy/compose/Caddyfile`, `deploy/compose/Dockerfile.proxy` | Terminates TLS, serves the SPA, proxies `/api/*` to the backend. |
| Model provider | OpenAI-compatible endpoint | `backend/app/services/model_gateway.py`, `backend/app/core/config.py` (`model_provider_mode`) | Two forms: `local` (on-prem, plain HTTP allowed) and `private_remote` (operator-managed, HTTPS required in production). Backend-only; the credential never reaches the browser. |
| Knowledge engine | DB-GPT with a Chroma vector store | `backend/app/core/config.py` (`vector_path`, `vector_collection`, `embedding_model`), `backend/app/api/knowledge.py` | Embedding model default `sentence-transformers/all-MiniLM-L6-v2`. |
| Credential vault | Fernet key material | `backend/app/core/config.py` (`credential_encryption_key`, `credential_key_file`) | Decrypts connector/infra secrets. Provided inline or as a `0600` key file (production default `/etc/openjm/credentials.key`). |
| Scheduler | In-process bounded tick | `backend/app/main.py` (`_scheduler_loop`), `backend/app/services/scheduler.py` | Opt-in via `OPENJM_SCHEDULER_ENABLED`. Default off. |
| Connectors | Registered connector types | `backend/app/services/connectors/`, `backend/app/api/connectors.py` | The Workspace connector is registered at startup. |
| Notifications | Delivery + retry | `backend/app/services/notifications.py`, `backend/app/api/operations.py` | Delivery re-proves recipient access at send time. |
| Operator CLI | `python scripts/openjm_ops.py <cmd>` | `scripts/openjm_ops.py` | Subcommands: `backup`, `verify`, `restore`, `upgrade`, `retention`, `config`. |
| Configuration preflight | `python -m app.core.preflight` | `backend/app/core/preflight.py` | Runs as a CLI and inside the app lifespan. Fail-closed in production. |
| Backend container image | `python:3.11-slim`, unprivileged user | `deploy/compose/Dockerfile`, `deploy/compose/entrypoint.sh` | Builds a backend venv, exposes 8000, runs preflight then uvicorn. |
| Proxy container image | `caddy:2` with the built SPA | `deploy/compose/Dockerfile.proxy` | Multi-stage: Node 20 builds `frontend/dist`, copied to `/srv/openjm/frontend`. |
| Compose stack | db + backend + proxy | `deploy/compose/docker-compose.prod.yml` | Named volumes, a db-password secret, healthchecks. |
| systemd unit | `openjm.service` | `deploy/systemd/openjm.service` | Runs the backend venv directly, with an `EnvironmentFile` and restart limits. |

## Installer and packaging artefacts

| Artefact | File | Purpose |
| --- | --- | --- |
| Linux installer | `scripts/install-linux.sh` | Version-checked, idempotent install. `--profile development\|production`, `--skip-frontend`. |
| Windows/WSL2 bootstrap | `scripts/install-wsl2.ps1` | Verifies WSL2, ensures the distro, maps the path, delegates to `install-linux.sh`. |
| Production config template | `.env.production.example` | Placeholders only; one key per classified setting. |
| Development config template | `.env.example` | Local defaults. |
| Windows-native helpers | `scripts/setup-windows.ps1`, `scripts/start-windows.ps1`, `scripts/verify-windows.ps1` | Legacy developer conveniences for a Windows-native venv. They are not the supported production path; the supported Windows path is WSL2 (see [WINDOWS_INSTALLER.md](WINDOWS_INSTALLER.md)). |
| Built-frontend server | `scripts/serve_frontend.py` | Serves `frontend/dist` and proxies `/api` to `127.0.0.1:8000` on port 5174. A developer/acceptance helper. |

## Component boundaries worth stating

- The backend is the only component that talks to the model provider, the
  metadata database and the storage volumes. The browser talks only to the
  reverse proxy.
- The SPA is public static content plus API calls; it never receives provider
  base URLs, model credentials, the OIDC secret or routing configuration.
- The metadata database is OpenJM's own; structured source data stays in the
  customer's system and is reached through the governed, read-only structured
  query path.
- Kubernetes is not a component. No manifest, chart or operator is present in
  the repository, and none is required.