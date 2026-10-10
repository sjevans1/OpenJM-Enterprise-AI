# QA1 acceptance environment (OpenJM Enterprise AI)

One controlled, production-like acceptance environment for OpenJM, reused by QA2
(persona/security journeys) and QA3 (vector/RAG plus final closure).

It runs the accepted REL1 production package unchanged. There are no product-code
changes in this directory: everything here is environment scaffolding.

## What it contains

| Component | Implementation | Port (loopback) |
| --- | --- | --- |
| Application-metadata database | `postgres:16` (PostgreSQL 16) | 15432 |
| OIDC identity provider | Keycloak 26.7.4 | 18090 |
| OpenJM backend | REL1 packaged wheel + committed lock, production profile | 18010 |
| Frontend | production SPA build served by `caddy:2` | 15173 |
| Model gateway | local OpenAI-compatible endpoint (operator-provided) | 18080 |

The backend is intentionally NOT a Compose service. The accepted REL1 deployment
semantics install the backend from the packaged application wheel and the
committed production lock (no editable install, no `PYTHONPATH`), which the
shipped `deploy/compose/Dockerfile` does not do. The backend therefore runs from
the REL1 production install on the host, and the frontend container proxies
`/api` to it.

## Repository artifacts

| File | Purpose |
| --- | --- |
| `docker-compose.qa.yml` | PostgreSQL, Keycloak and the frontend host |
| `keycloak/realm-openjm.json` | Non-secret realm template: client, PKCE, tenant/audience mappers, persona user shells |
| `Caddyfile` | Frontend host: API proxy before SPA fallback |
| `.env.qa.example` | Documented production-profile configuration (no secret values) |
| `qa_env.sh` | Renders the QA1 environment from the operator secret store |
| `install-backend.sh` | REL1 production install (offline, hash-pinned, packaged wheel) |
| `build-frontend.sh` | Production frontend build and staging |
| `sql/qa_demo.sql` | Synthetic structured-data fixture |
| `../../scripts/qa/seed_acceptance_env.py` | Idempotent seeder (Keycloak + platform trust root + HTTP fixtures) |
| `../../scripts/qa/smoke_matrix.py` | Identity/governance smoke matrix and retrieval proofs |
| `../../scripts/qa/start_backend.sh` | Preflight + uvicorn |

## Secrets the maintainer provides

No secret is committed. Create `$OPENJM_QA_ENV_ROOT/secrets` (default
`~/openjm-qa1-env/secrets`, mode 0700) and place:

| File | Content |
| --- | --- |
| `db_password.txt` | PostgreSQL password for the QA database |
| `kc_admin_password.txt` | Keycloak bootstrap admin password |
| `keycloak.env` | `KC_BOOTSTRAP_ADMIN_USERNAME` and `KC_BOOTSTRAP_ADMIN_PASSWORD` |
| `model_api_key.txt` | Credential for the local OpenAI-compatible endpoint |
| `credential_key.txt` | Fernet key for the OpenJM credential vault |
| `ops_token.txt` | Shared bearer for the operational endpoints |

Generated automatically by the seeder (do not create by hand):
`openjm_client_secret.txt` (rotated OIDC client secret) and `qa_credentials.json`
(persona passwords).

## Stand-up

```bash
export OPENJM_QA_ENV_ROOT="$HOME/openjm-qa1-env"
export OPENJM_QA_BUNDLE="$HOME/openjm-replay/b4-bundle/openjm-rel1-0.2.0"

# 0. dependency services
export OPENJM_QA_SECRETS="$OPENJM_QA_ENV_ROOT/secrets"
export OPENJM_QA_FRONTEND_DIST="$OPENJM_QA_ENV_ROOT/frontend-dist"
docker compose -f deploy/qa/docker-compose.qa.yml up -d db keycloak

# 1. grouped structured fixture database
docker exec openjm-qa1-db psql -U openjm -d openjm -c "CREATE DATABASE qa_demo"
docker exec -i openjm-qa1-db psql -U openjm -d qa_demo < deploy/qa/sql/qa_demo.sql

# 2. backend production install
deploy/qa/install-backend.sh

# 3. frontend production build, then serve it
deploy/qa/build-frontend.sh
docker compose -f deploy/qa/docker-compose.qa.yml up -d frontend

# 4. Keycloak realm secrets and persona passwords (no backend needed)
"$OPENJM_QA_ENV_ROOT/venv/bin/python" scripts/qa/seed_acceptance_env.py --phase keycloak

# 5. serve the backend (preflight is fail-closed)
scripts/qa/start_backend.sh            # run in its own shell / background

# 6. seed tenants, personas, governance and fixtures over real OIDC
source deploy/qa/qa_env.sh
"$OPENJM_QA_ENV_ROOT/venv/bin/python" scripts/qa/seed_acceptance_env.py --phase openjm

# 7. identity/governance smoke matrix
"$OPENJM_QA_ENV_ROOT/venv/bin/python" scripts/qa/smoke_matrix.py
```

The seeder is idempotent: re-running `--phase openjm` reuses existing
departments, groups, members, documents and the structured source.

## Teardown

```bash
pkill -f 'uvicorn app.main:app'
docker compose -f deploy/qa/docker-compose.qa.yml down      # keep volumes
docker compose -f deploy/qa/docker-compose.qa.yml down -v   # also drop data
```

## Notes and pitfalls

* Keycloak marks its login cookies `Secure`, so a standards-compliant HTTP client
  will not replay them over plain HTTP and the login POST fails with
  `Restart login cookie not found`. This loopback acceptance environment replays
  the session cookies explicitly in `scripts/qa/qa_env_lib.py`.
* Visibility is classification/group scoped, not role scoped. A fixture can be
  invisible even to the tenant admin (for example an HR-Leadership-only document
  is invisible to a Finance-titled admin). The seeder's idempotent lookup uses a
  viewer that can actually see each fixture to avoid uploading duplicates.
* The first platform operator has no HTTP bootstrap path. The seeder performs
  that one step through the application service layer; everything else is driven
  over the real HTTP API.
