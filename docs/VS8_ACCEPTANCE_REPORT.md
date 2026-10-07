# VS8 Acceptance Report — Self-Hosted Commercialization Readiness (Release Candidate)

Issue: #38 (VS8 commercialization readiness)
Product version: 0.2.0 (`VS8` release train)
Parent baseline: VS7 merge `1d687d1`
Code under test: `70369454d2e40ffaefe7a9d79c7d246425c1fd6f`
Report date: 2026-10-07

This report distinguishes **PASS**, **FAIL** and **NOT RUN** for every gate Issue
#38 requires. Any check that was not actually exercised is recorded as NOT RUN,
never as PASS. Fixture-backed evidence is labelled as such; no claim of a live
private fleet or a specific external identity provider is made.

## Summary

| Gate | Result |
| --- | --- |
| 1. Local model Chat / Knowledge / Data / Hybrid | PASS |
| 2. Private hosted model API (routing, secrecy, bounds, no fallback) | PASS (fixture) |
| 3. Production deployment: PostgreSQL + OIDC/TLS boundary | PASS (fixture IdP) / NOT RUN (live external IdP) |
| 4. Windows / WSL2 installation | PASS (Linux installer in WSL2) / NOT RUN (Windows bootstrap) |
| 5. Backup, restore, DR, tenant isolation | PASS |
| 6. Upgrade, rollback | PASS |
| 7. Performance and load baseline | PASS (health/ready) / NOT RUN (retrieval, connector, scheduler concurrency) |
| 8. Security and observability regression | PASS |
| 9. Production packaging (Compose) | PASS (config) / NOT RUN (live compose up) |

## Environment

- Host: WSL2, `Linux 6.6.87.2-microsoft-standard-WSL2`, x86_64, 16 CPUs, Python 3.11.15.
- PostgreSQL: provided by the `pgserver` dependency (a real PostgreSQL server, no root needed).
- Local model: a local OpenAI-compatible Gemma worker bound to `127.0.0.1`, bearer required.
- Credentials: the model bearer and OIDC client secret were supplied through the
  server-side environment and secret files only. No credential appears in this
  report, in any committed file, or in any client-facing response. Secrets are
  written as `[REDACTED]`.

## Gate 1 — Local model Chat / Knowledge / Data / Hybrid: PASS

Clean-environment run against an isolated backend and the local OpenAI-compatible
endpoint.

- Chat + Knowledge (`scripts/acceptance.py`): health, isolated knowledge catalog,
  conversation creation, server-side conversation memory, persisted history,
  document indexing, document catalog listing, document-grounded answer
  (`ZX-4471`) with evidence, and deletion removing the document from both catalog
  and evidence. All checks passed.
- Data foundation (`scripts/structured_foundation.py`): source registration,
  connection test, schema discovery, credential-redaction contract, source
  disable, source deletion. All checks passed.
- Data chat (`scripts/structured_chat_acceptance.py`): registered source,
  connection, schema discovery, and structured answer path. Passed.
- Hybrid (`scripts/dependent_hybrid_acceptance.py`, `--isolated-instance`): policy
  indexing, HYBRID execution class and mode, structured result evidence, exact
  grounded predicate, threshold/operator/currency provenance, boundary-value
  exclusion, both citation types, missing-policy and conflicting-policy fallbacks
  with zero structured executions, policy-value mutation shifting the boundary,
  currency-mismatch fallback, and catalog cleanup. `VS3-C3 LIVE HARDENING
  ACCEPTANCE PASSED`.

Note: the enterprise assertions in `scripts/acceptance.py` originally omitted the
`mode` field; the API default is `chat`, so those steps were not exercising
retrieval. The harness now passes `mode=knowledge` explicitly. This is a harness
fix, not a product change (the orchestrator was untouched by VS8).

## Gate 2 — Private hosted model API: PASS (controlled fixture)

Evidence: `scripts/vs8_private_model_fixture.py` (labelled fixture) and the private
model leg of `scripts/vs8_production_acceptance.py`. The fixture is a controlled,
authenticated HTTPS OpenAI-compatible endpoint, not a live OpenJM fleet.

- Backend-only routing: the backend reached the private HTTPS endpoint and
  returned the model answer. PASS.
- Credential isolation: the provider probe that feeds `/api/ready` reports mode
  and transport only; it returns no `api_key`/`authorization` field and never the
  credential value. PASS.
- Wrong credential: request fails closed with an explicit gateway error. PASS.
- Provider outage: request fails closed within the bounded timeout; no fallback to
  any other provider. PASS.
- No unapproved public fallback: provider fallback is `none`; the config preflight
  refuses to start otherwise. PASS.
- Production HTTPS policy: production preflight rejects a plain-HTTP private
  endpoint and accepts the HTTPS one. PASS.

Knowledge/Data/Hybrid over the *remote* private endpoint were not re-run (the
fixture offers no embeddings endpoint, so vector indexing cannot complete
against it). Those modes were exercised end-to-end against the local
OpenAI-compatible endpoint in Gate 1 through the same gateway code path, with
`model_provider_mode=local`. Recorded explicitly rather than claimed.

## Gate 3 — Production deployment: PASS (fixture IdP) / NOT RUN (live external IdP)

Evidence: `scripts/vs8_production_acceptance.py`.

- Production configuration preflight passes for a fully provisioned deployment.
- The real application boots in the production profile (health reports
  `profile: production`).
- Migrations reach the Alembic head on PostgreSQL.
- No production fallback to dev auth: unauthenticated requests are refused, and
  the dev-auth header path is refused in production.
- Live OIDC path: a fixture-issued RS256 token validates against the fixture JWKS
  and authorizes a protected request (`200`); a token signed by a foreign key is
  refused (`401`).
- Chat routes through the backend to the private model (`200`, model answer).
- Client surfaces (`/api/health`, `/api/version`, `/api/ready`, `/api/config/public`,
  `/api/metrics`) expose neither the model credential nor the provider URL.
- Readiness reports provider `mode=private_remote`, `transport=https`.

NOT RUN: interop with a specific external identity provider (keycloak.microsoft or
similar) and termination through a real TLS reverse proxy. The OIDC and TLS
boundaries are exercised with controlled fixtures; live IdP/proxy interop is
deferred to deployment-time acceptance.

TLS boundary configuration (Caddyfile, trusted-host/secure-header middleware,
`trust_proxy_headers`) is present and unit covered; the reverse proxy was not
started live.

## Gate 4 — Windows / WSL2 installation: PASS (WSL2 path) / NOT RUN (Windows bootstrap)

- Linux installer (`scripts/install-linux.sh --profile development --skip-frontend`)
  ran to completion, `EXIT=0`, in a clean checkout inside WSL2: it created the
  virtual environment, installed dependencies, created `.env` from the example,
  ran the preflight, and printed the start/verify instructions. This is the
  supported WSL2 execution path and it was genuinely exercised.
- The Windows-side bootstrap (`scripts/install-wsl2.ps1`) was invoked from the
  Windows host. Its `wsl --list --quiet` output could not be parsed from the
  nested invocation, so it took the "distribution missing" branch and launched
  `wsl --install -d Ubuntu`; the run was aborted. NOT RUN as a clean bootstrap.
  The host already has an Ubuntu-24.04 (v2) distribution, so the distro-install
  branch could not have been meaningfully exercised anyway. The nested invocation
  also caused a stray `Ubuntu` distribution registration; this is recorded as a
  side effect for the operator to review (`wsl --unregister Ubuntu` if unwanted).

## Gate 5 — Backup, restore, DR and tenant isolation: PASS

- Authoritative/reproducible/ephemeral data taxonomy is defined and enforced by
  `app/ops/backup.py`; the manifest carries per-file SHA-256 checksums and the
  effective configuration snapshot with secret **presence** flags only.
- SQLite DR acceptance destroys the primary store and restores to a fresh target
  for two seeded tenants; rows survive and stay tenant-scoped.
- PostgreSQL backup/restore was exercised live: `pg_dump` produced a verified
  backup, and it was restored into a fresh database with `psql`; the restored row
  count matches the source (1 conversation created during the production gate).
- Tenant isolation: `tests/test_vs5_tenant_isolation.py` passes; the DR fixtures
  seed two tenants and assert no cross-tenant leakage.

## Gate 6 — Upgrade and rollback: PASS

- `tests/test_vs8_upgrade.py` (including a new seeded VS7-baseline case) proves a
  populated revision-0007 database upgrades to head, postflight reports head, and
  the seeded row survives.
- The schema guard refuses to start on an unknown future revision
  (`UnknownSchemaRevisionError`), so there is no silent startup on a divergent
  schema.
- Rollback is restore-based and documented in `docs/UPGRADE_ROLLBACK.md`; the
  restore path itself is proven in Gate 5.

## Gate 7 — Performance and load baseline: PASS (partial)

Host: 16 vCPU, WSL2. Bounded, self-reported measurements (no sizing claims):

| Mode | Concurrency | Duration | Requests | Throughput | Error rate | p50 | p90 | p99 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| health | 12 | 20 s | 10,426 | 520.8 rps | 0.0 | 19.2 ms | 27.8 ms | 48.3 ms |
| ready | 12 | 20.6 s | 494 | 23.9 rps | 0.0 | 478.4 ms | 645.2 ms | 931.6 ms |

`/api/ready` is far slower because it probes the model/provider on each call; the
health figure reflects pure request throughput.

NOT RUN: concurrent retrieval, connector and scheduler load, and tenant-isolation
under concurrent load. These require many model-backed calls; they were deferred
per the instruction to avoid unnecessary model calls. The harness
(`scripts/load_baseline.py`) supports a `chat` mode for this.

## Gate 8 — Security and observability regression: PASS

`pytest` over the tenant-isolation, connector/scheduler security, and VS8 suites:
124 passed. Covers: no dev-auth production fallback, no client exposure of the
model credential or provider URL, unknown-schema startup refusal, secure headers,
body/upload bounds, rate limiting, credential redaction, and fail-closed
production preflight.

## Gate 9 — Production packaging: PASS (config) / NOT RUN (live stack)

- `docker compose -f deploy/compose/docker-compose.prod.yml config` validates with
  no warnings against `.env.production.example`, rendering services `backend`,
  `db` (`postgres:16`) and `proxy` (`caddy:2`) with named volumes.
- A packaging defect was found and fixed: `.env.production.example` did not
  declare `OPENJM_DB_PASSWORD`, which the Compose DSN requires, so the rendered
  database URL had an empty password. The variable is now declared.
- NOT RUN: an actual `docker compose up` of the production stack (image pull and
  long-running services), and live Caddy validation.

## Defects found and fixed during acceptance

1. `app/migrations_runner.py`: percent-encoded database URLs (unix-socket hosts,
   passwords containing `%`) raised an Alembic interpolation error at startup.
   The URL is now escaped before being written to the Alembic config.
2. `app/ops/backup.py`: `pg_dump`/`psql` were assumed to be on `PATH` and received
   a SQLAlchemy driver URL. Both are now resolved from `PATH` or the bundled
   `pgserver` binaries, URLs are normalised to libpq form, and PostgreSQL restore
   targets are supported.
3. `.env.production.example`: missing `OPENJM_DB_PASSWORD` required by the Compose
   DSN (see Gate 9).
4. `scripts/acceptance.py`: enterprise steps omitted `mode`, so they ran as chat
   and never exercised retrieval; now `mode=knowledge`.

## Not run

- Live interop with a specific external OIDC provider and a real TLS proxy.
- The Windows-side WSL2 bootstrap as a clean run.
- Concurrent retrieval/connector/scheduler load and tenant isolation under load.
- A live `docker compose up` of the production stack.

## Artifacts

Harness outputs are written under the run directory (JSON): `private_model.json`,
`production.json`, `load_health.json`, `load_ready.json`, plus the gate logs and
`compose_config.yaml`. The backend and frontend regression runs and the CI run IDs
are recorded on PR #39 and Issue #38.

## Recommendation

The VS8 release candidate meets the acceptance gates that could be executed in
this environment, with the deferred items above recorded as NOT RUN. It is ready
for human review. No merge has been performed.
