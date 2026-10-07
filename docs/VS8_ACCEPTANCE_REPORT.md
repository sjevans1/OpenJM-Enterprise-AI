# VS8 Acceptance Report — Self-Hosted Commercialization Readiness (Release Candidate)

Issue: #38 (VS8 commercialization readiness)
Product version: 0.2.0 (`VS8` release train)
Parent baseline: VS7 merge `1d687d1`
Code under test: `38c9029c19f26b8a2eb938348b3c117a91be5b42` (VS8 correction pass, PR #39)
Report date: 2026-10-07

This report distinguishes **PASS**, **FAIL** and **NOT RUN** for every gate Issue
#38 requires. Any check that was not actually exercised is recorded as NOT RUN,
never as PASS. Fixture-backed evidence is labelled as such; no claim of a live
private fleet or a specific external identity provider is made.

This revision addresses the PR #39 review (blockers 1–3 and the hardening
checks). It supersedes the prior revision, whose "Code under test" value
(`70369454…`) referred to the pre-correction candidate.

## Summary

| Gate | Result |
| --- | --- |
| 1. Local model Chat / Knowledge / Data / Hybrid | PASS (not re-run; orchestrator untouched) |
| 2. Private hosted model API (routing, secrecy, bounds, no fallback) | PASS (fixture) |
| 3. Production deployment: PostgreSQL + OIDC/TLS boundary | PASS (fixture IdP) / NOT RUN (live external IdP) |
| 4. Windows / WSL2 installation | PASS (Linux installer in WSL2) / NOT RUN (Windows bootstrap) |
| 5. Backup, restore, DR, tenant isolation | PASS |
| 6. Upgrade, rollback | PASS |
| 7. Performance and load baseline | PASS (health + public readiness) / NOT RUN (retrieval, connector, scheduler concurrency) |
| 8. Security and observability regression | PASS |
| 9. Production packaging (Compose) | **PASS (live `docker compose up --build` stack)** |

## Environment

- Host: WSL2, `Linux 6.6.87.2-microsoft-standard-WSL2`, x86_64, 16 CPUs, Python 3.11.15.
- Docker Engine 29.1.3 (Compose v2.40.3) for the live packaging gate.
- PostgreSQL: provided by the `pgserver` dependency (fixture gates) and by the
  `postgres:16` Compose service (live packaging gate).
- Local model: a local OpenAI-compatible Gemma worker bound to `127.0.0.1`, bearer required.
- Credentials: supplied through server-side environment and secret files only. No
  credential appears in this report, in any committed file, or in any
  client-facing response. Secrets are written as `[REDACTED]`.

## Correction pass — PR #39 review items

Reviewed at `f9604f98…`. Addressed in `38c9029c…`:

| Review item | Resolution | Evidence |
| --- | --- | --- |
| Blocker 1 — frontend not delivered by Compose | `deploy/compose/Dockerfile.proxy` builds the SPA into the proxy image; the Caddyfile serves it from `/srv/openjm/frontend` | Live stack: root + deep route + hashed asset served (Gate 9) |
| Blocker 1 (latent defect) — `/api/*` not proxied | `try_files` rewrote `/api/…` to `/index.html` before the proxy handler. Fixed with mutually exclusive `handle /api/*` → `reverse_proxy` and `handle` → SPA | Live stack: `/api/health` returns the backend profile, not the SPA |
| Blocker 2 — provider URL may leak in client error | Provider failures classified (`timeout`, `auth_rejected`, `unreachable`, `bad_response`); raw httpx text never returned, sanitised operator log | `test_model_gateway_error_safety.py` (sentinel host/path/token); live Gate 2 |
| Blocker 3 — production install launchable | Real `docker compose up --build` (production profile) from a scratch copy | Gate 9 |
| Hardening — chunked body bypass | `BodySizeLimitMiddleware` now enforces the bound on the read stream (no-Content-Length and lying-length cases) | `test_vs8_system_and_hardening.py` |
| Hardening — probe amplification | Public `/api/ready` is a minimal go/no-go; `/api/ready/detail` + `/api/metrics` require `OPENJM_OPS_TOKEN` (hidden in production without it) | `test_vs8_system_and_hardening.py`; live Gate 9 (401 unauth) |
| Hardening — report SHA | This report now names the correction-pass code SHA | This file |
| Windows bootstrap detection | Read-only distro detection; never auto-installs a different distro without `-AllowDistroInstall` | `scripts/install-wsl2.ps1` (NOT RUN on Windows) |

## Gate 1 — Local model Chat / Knowledge / Data / Hybrid: PASS (not re-run)

No application code on the Chat/orchestrator path was changed by the correction
pass (the model-gateway change is the failure-classification of transport
errors, covered by Gate 8 unit tests). The previously recorded clean-environment
run against the local OpenAI-compatible endpoint therefore still holds: Chat +
Knowledge (`scripts/acceptance.py`, `mode=knowledge`), Data foundation
(`scripts/structured_foundation.py`), Data chat
(`scripts/structured_chat_acceptance.py`) and Hybrid
(`scripts/dependent_hybrid_acceptance.py`) all passed. Not re-run in this pass
to avoid unnecessary model calls; the orchestrator was not modified.

## Gate 2 — Private hosted model API: PASS (controlled fixture)

Evidence: `scripts/vs8_private_model_fixture.py` (labelled fixture) and the private
model leg of `scripts/vs8_production_acceptance.py` (re-run this pass). The
fixture is a controlled, authenticated HTTPS OpenAI-compatible endpoint, not a
live OpenJM fleet.

- Backend-only routing; wrong credential fails closed; outage fails closed within
  the bounded timeout with no fallback; production preflight requires HTTPS and a
  server-side credential; provider fallback is `none`. PASS.
- Credential isolation: the provider probe reports mode and transport only; no
  `api_key`/`authorization` field and never the credential value. PASS.
- Provider failures return a stable category and never the provider URL/host or
  credential. PASS.
- The detailed operational surface now requires the ops token; `/api/metrics`
  without it returns 401 (`ops_metrics_requires_token: true`). PASS.

Knowledge/Data/Hybrid over the *remote* private endpoint were not re-run (the
fixture offers no embeddings endpoint); they were exercised end-to-end against
the local endpoint in Gate 1 through the same gateway code path.

## Gate 3 — Production deployment: PASS (fixture IdP) / NOT RUN (live external IdP)

Evidence: `scripts/vs8_production_acceptance.py` — PASS this pass.

- Production preflight passes; the app boots in the production profile
  (`profile: production`); migrations reach head on PostgreSQL; unauthenticated
  and dev-auth requests are refused; a fixture RS256 token authorizes a protected
  request while a foreign-key token is refused; Chat routes to the private model;
  PostgreSQL backup/restore preserves rows.

NOT RUN: interop with a specific external identity provider and termination
through a real public TLS proxy (avoided ACME). The OIDC and TLS boundaries are
exercised with controlled fixtures; live IdP/proxy interop is deferred to
deployment-time acceptance.

## Gate 4 — Windows / WSL2 installation: PASS (WSL2 path) / NOT RUN (Windows bootstrap)

- Linux installer (`scripts/install-linux.sh`) ran to completion inside WSL2 in a
  clean checkout, exercising the full migration chain and preflight (recorded in
  the prior revision). The supported WSL2 execution path is genuinely exercised.
- NOT RUN: the Windows-side bootstrap (`scripts/install-wsl2.ps1`). It is not run
  from inside WSL (its `wsl --list --quiet` output cannot be parsed through the
  nested invocation). The detection path was hardened so this failure mode is
  now safe: detection is read-only, and the script refuses to install a different
  distribution unless `-AllowDistroInstall` is passed explicitly. No distribution
  is installed or unregistered automatically.

## Gate 5 — Backup, restore, DR and tenant isolation: PASS

Unchanged and re-exercised within Gate 3 (live `pg_dump` → verified backup →
restore into a fresh database, row count matched). SQLite DR acceptance and
tenant isolation (`tests/test_vs5_tenant_isolation.py`) remain green.

## Gate 6 — Upgrade and rollback: PASS

`tests/test_vs8_upgrade.py` proves a populated revision-0007 database upgrades to
head; the schema guard refuses an unknown future revision; rollback is
restore-based (`docs/UPGRADE_ROLLBACK.md`), proven by the restore path.

## Gate 7 — Performance and load baseline: PASS (health + public readiness)

Host: 16 vCPU, WSL2. Bounded, self-reported measurements (no sizing claims),
re-measured this pass against a development backend after the readiness split:

| Mode | Concurrency | Duration | Requests | Throughput | Error rate | p50 | p90 | p99 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| health | 12 | 20 s | 12,185 | 609.1 rps | 0.0 | 17.6 ms | 23.0 ms | 116.8 ms |
| ready (public minimal) | 12 | 20 s | 5,759 | 287.6 rps | 0.0 | 35.9 ms | 55.4 ms | 147.2 ms |

The `ready` figure now measures the **public minimal** readiness endpoint. The
prior revision's `ready` figure (23.9 rps) measured the old detailed endpoint
(which ran migrations, DB-wide counts and a model probe per call) and is
superseded; the split removes that unauthenticated amplification path.

NOT RUN: concurrent retrieval, connector and scheduler load, and tenant-isolation
under concurrent load (deferred to avoid unnecessary model calls;
`scripts/load_baseline.py --mode chat` supports it).

## Gate 8 — Security and observability regression: PASS

- Focused suites re-run this pass: `test_vs8_system_and_hardening.py`,
  `test_model_gateway.py`, `test_model_gateway_error_safety.py`,
  `test_vs8_config_preflight.py`, `test_chat_history_integrity.py` — 67 passed.
- Full backend suite: **819 passed, 1 failed**. The single failure
  (`test_report_run_budget.py::test_overall_planning_wall_timeout_finalizes_without_result`)
  is a pre-existing, environment-sensitive failure that reproduces identically at
  base `1d687d1` and is untouched by this branch (documented in the prior
  revision); it is not a VS8 regression.
- Frontend: `tsc --noEmit` + `vite build` clean; 68 tests passed.
- CI lint parity: `ruff check . --select E9,F63,F7,F82` clean.
- New negative coverage: no provider host/path/token in a `ModelGatewayError`, a
  502 API body, the readiness probe, metrics, or the operator log; chunked and
  lying-length request bodies are refused; the ops token gates metrics and
  readiness detail; secrets are classified in the configuration surface.

## Gate 9 — Production packaging (Compose): PASS (live stack)

Evidence: `scripts/vs8_compose_acceptance.py` (new), run against a real
`docker compose -f deploy/compose/docker-compose.prod.yml up -d --build` of the
**production profile** from a scratch copy. Result: `COMPOSE ACCEPTANCE: PASS`.

- The backend image's editable install resolves `app` from `/opt/openjm`
  (`/opt/openjm/backend/app/__init__.py`). PASS.
- The backend entrypoint assembled `OPENJM_DATABASE_URL` from the shared
  `openjm_db_password` secret (PID 1 environment). PASS.
- Migrations reached head on PostgreSQL (`0007_vs7_connectors`); the Postgres
  schema exists and no SQLite fallback file was created; the db accepts the same
  secret password over TCP. PASS.
- The proxy (Caddy) serves the built SPA at `/` and on a deep route, serves a real
  hashed asset (`assets/index-*.js`) with a JS content type, and proxies
  `/api/health` to the backend (production profile). PASS.
- The frontend asset tree is physically present in the proxy container. PASS.
- `/api/ready` is minimal; `/api/metrics` returns 401 unauthenticated and 200 with
  the ops token; no provider key, ops token or db password appears on the public
  surfaces. PASS.

`docker compose config --env-file .env.production.example` validates without
warnings. This live run replaces the prior "NOT RUN (live compose up)" result.
NOT RUN within this gate: a public TLS termination with a real certificate/domain
(the acceptance ran Caddy on HTTP to avoid ACME).

## Defects found and fixed during acceptance

1. `app/migrations_runner.py`: percent-encoded database URLs raised an Alembic
   interpolation error at startup; now escaped. (prior revision)
2. `app/ops/backup.py`: `pg_dump`/`psql` resolved from PATH only; now resolved
   from PATH or bundled `pgserver`, normalised to libpq. (prior revision)
3. `.env.production.example`: missing `OPENJM_DB_PASSWORD`. (prior revision)
4. `scripts/acceptance.py`: enterprise steps omitted `mode`. (prior revision)
5. `deploy/compose/Caddyfile`: `/api/*` was rewritten to `/index.html` by
   `try_files` before the proxy handler, so the API was not proxied. Fixed with
   mutually exclusive `handle` blocks.
6. `deploy/compose/docker-compose.prod.yml` + `deploy/compose/Dockerfile`: the
   frontend was never delivered to the proxy, and the db password had two sources.
   Fixed with a frontend-bearing proxy image and a single-source secret +
   entrypoint DSN.
7. `app/services/model_gateway.py`: provider transport errors echoed the httpx
   message (which can carry the private URL/host/credential). Now categorised and
   sanitised.
8. `app/core/middleware.py`: the body-size limit trusted the declared
   `Content-Length`; a chunked body could bypass it. Now bounded on the stream.

## Not run

- Live interop with a specific external OIDC provider and a real public TLS proxy.
- The Windows-side WSL2 bootstrap as a clean run (detection path hardened; not run).
- Concurrent retrieval/connector/scheduler load and tenant isolation under load.
- Live chat/knowledge/hybrid re-run (orchestrator unchanged; deferred).

## Artifacts

Harness outputs (JSON): `/tmp/vs8-compose.json` (compose acceptance),
`/tmp/vs8-prod.json` (production), `/tmp/vs8-load-health.json`,
`/tmp/vs8-load-ready.json` (load), plus the compose/production logs. The backend
and frontend regression runs and the exact-head CI run IDs are recorded on PR #39
and Issue #38.

## Recommendation

The VS8 release candidate, corrected at `38c9029c…`, meets every acceptance gate
that can be executed in this environment, with the deferred items above recorded
as NOT RUN. It is ready for human review. No merge has been performed.
