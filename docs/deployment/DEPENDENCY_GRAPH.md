# Production service dependency graph

The runtime dependencies between the production components, and the order in
which they must be available. Grounded in `deploy/compose/docker-compose.prod.yml`,
`deploy/compose/Caddyfile`, `deploy/systemd/openjm.service` and
`backend/app/main.py`.

## Graph

```
        +---------------------------+
        |        Browser / SPA      |  (public, no secrets)
        +-------------+-------------+
                      | HTTPS (443) ; HTTP (80) redirect/entry
                      v
        +---------------------------+
        |   Reverse proxy (Caddy)   |  serves /srv/openjm/frontend
        |   TLS termination         |  handle /api/* -> backend:8000
        +-------------+-------------+
                      | HTTP, private network only
                      v
        +---------------------------+
        |   Backend API (uvicorn)   |  app.main:app  (port 8000)
        +--+------+------+--------+-+
           |      |      |        |
           |      |      |        +----------------------------+
           |      |      |                                     |
           v      v      v                                     v
   +--------+ +------+ +---------+                    +-------------------+
   | Postgres| |Uploads| |Vector  |                    | Model provider    |
   | :5432   | |/var/  | |/var/   |                    | local or          |
   | metadata| |lib/   | |lib/    |                    | private_remote    |
   +--------+ |openjm | |openjm  |                    +-------------------+
              +------+ +---------+
           |                                       +-------------------+
           v                                       | OIDC / SSO IdP    |
   +-----------------+                             | (external issuer) |
   | Credential key  |                             +-------------------+
   | /etc/openjm/    |
   | credentials.key |
   +-----------------+
```

Internal, backend-owned loops (no external network on the critical path):

- **scheduler** tick -> connectors / notifications / report runs, each
  re-authorized at execution time;
- **connectors** -> customer source systems through governed, read-only paths.

## Start order (Compose)

1. `db` (PostgreSQL 16) starts and must pass its healthcheck
   (`pg_isready -U openjm -d openjm`).
2. `backend` starts after `db` is healthy (`depends_on: condition:
   service_healthy`). Its `entrypoint.sh` assembles `OPENJM_DATABASE_URL` from
   the shared `openjm_db_password` secret, then runs
   `python -m app.core.preflight` and refuses to serve an unsafe production
   configuration. The lifespan then runs migrations (`app.db.init_db`).
3. `proxy` starts after `backend` (`depends_on: backend`).

## Start order (systemd)

`openjm.service` is a single unit: `ExecStartPre` runs the preflight,
`ExecStart` runs uvicorn. `After=network-online.target` and
`Wants=network-online.target` order it after the network. The database, model
provider and OIDC IdP are external and are not ordered by the unit; readiness
(not liveness) reports each one once the process is serving.

## Shutdown order

Shutdown is the reverse of start-up and is bounded everywhere.

- **Compose.** `docker compose down` stops `proxy`, then `backend`, then `db`
  (reverse of `depends_on`). `backend` and `db` carry `restart: unless-stopped`,
  so a stop that is not an explicit `down` restarts them. The PostgreSQL data
  volume persists across a stop.
- **systemd / direct uvicorn.** The process handles `SIGTERM` (the unit sets
  `KillSignal=SIGTERM` and `TimeoutStopSec=30`). The application lifespan's
  `finally` block signals the in-process scheduler to stop and waits up to five
  seconds for its tick task to finish before the process exits, so a tick is not
  killed mid-write.
- **Inside the backend.** There is no external broker or message queue to drain;
  the only background loop is the opt-in scheduler. In-flight report runs are not
  drained on shutdown: they persist as `running` and are recovered on the next
  start by normal startup recovery (report runs become `interrupted`, document
  leases are reclaimable; see [BACKUP_RESTORE_CONTRACT.md](BACKUP_RESTORE_CONTRACT.md)).
- **No state is written on shutdown** that the next start depends on. Every
  component reads its durable state at start-up, so an unclean stop is
  recoverable by the same startup recovery path that a restore uses.

## Which dependencies gate readiness

`/api/ready/detail` distinguishes the components required to serve core requests
from those that are merely reported. This is the deployment-relevant split:

| Component | Gates readiness? | Why |
| --- | --- | --- |
| database | yes | no metadata database, no service |
| migrations | yes | schema must equal this build's head |
| storage | yes | upload/vector volumes must be writable |
| model_provider | no | an outage is visible, not fatal; the process stays in rotation |
| knowledge | no | reported (DB-GPT / vector path) |
| scheduler | no | reported |
| connectors | no | reported |
| notifications | no | reported |

Gating components are exactly `("database", "migrations", "storage")` in
`backend/app/api/system.py`.

## External dependencies that are not a deployment responsibility

- **OIDC/SSO IdP** is operator-provided. OpenJM owns the OIDC client and
  authorization-code flow; it never shares state with Workspace.
- **Customer source systems** reachable through connectors are the customer's.
- **The model provider** is either an on-prem endpoint (`local`) or an
  operator-managed private endpoint (`private_remote`). OpenJM never silently
  falls back to a public provider (`model_provider_fallback=none` is enforced in
  production).