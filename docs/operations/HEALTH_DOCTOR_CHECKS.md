# Health, readiness and doctor checks

The checks an operator uses to decide whether a deployment is up, ready and
correctly configured. Grounded in `backend/app/api/system.py`,
`backend/app/main.py`, `deploy/compose/docker-compose.prod.yml`,
`deploy/systemd/openjm.service` and `scripts/openjm_ops.py`. The endpoint
reference is [../OPERATIONS.md](../OPERATIONS.md); this document maps the checks
onto a deployment procedure.

## Liveness versus readiness

- **Liveness** answers "is this process serving?". `/api/health` has no
  dependencies. A failing dependency must not make liveness fail, or a restart
  loop would kill a working process during a provider outage.
- **Readiness** answers "can it do its work?" and names each component. Only the
  components required to serve core requests gate readiness.

## HTTP checks

| Check | Endpoint | Auth | Pass condition |
| --- | --- | --- | --- |
| Liveness | `GET /api/health` | none | HTTP 200 |
| Public readiness | `GET /api/ready` | none | `{"ready": true}` (database + storage) |
| Detailed readiness | `GET /api/ready/detail` | `OPENJM_OPS_TOKEN` bearer | `ready: true`; per-component report |
| Version identity | `GET /api/version` | none | `version`, `release_id`, `release_train` match the release |
| Metrics | `GET /api/metrics` | `OPENJM_OPS_TOKEN` bearer | HTTP 200 Prometheus text (404 when disabled) |
| Public config | `GET /api/config/public` | none | white-label display metadata only |

Production hides `/api/ready/detail` and `/api/metrics` (404) when no
`OPENJM_OPS_TOKEN` is set, so the detailed surface is not exposed to an
unauthenticated caller. The public `/api/ready` is deliberately cheap: it never
runs the migration runner, a DB-wide count or a remote model probe.

### Detailed readiness components

`/api/ready/detail` returns these components. Gating components are
`database`, `migrations` and `storage`; the rest are reported but do not take
the process out of rotation.

| Component | Gates readiness | What it reports |
| --- | --- | --- |
| database | yes | connectivity, pool size/checked-out, latency |
| migrations | yes | current revision and head; `ok` when equal, `degraded` otherwise |
| storage | yes | upload directory writability and free space; a low-space warning is `warning` |
| knowledge | no | DB-GPT backend, vector path writable (`disabled` when knowledge is off) |
| model_provider | no | mode, model id, transport (http/https), fallback policy, reachability; never the host or credential |
| scheduler | no | enabled flag, active schedules, last run |
| connectors | no | instance counts by status (no content, no secrets) |
| notifications | no | pending count |

No secret or tenant content appears in any operational response.

## Process-supervisor checks

### Compose

| Service | Healthcheck | Effect |
| --- | --- | --- |
| `db` | `pg_isready -U openjm -d openjm` | `backend` waits for `condition: service_healthy` |
| `backend` | HTTP GET `/api/health` returns 200 | container health status |

The backend healthcheck uses liveness, not readiness, so a model-provider outage
does not restart the container.

### systemd

`deploy/systemd/openjm.service` runs `ExecStartPre=python -m app.core.preflight`
before serving, so an unsafe production configuration prevents the unit from
starting at all. Restart is bounded: `Restart=on-failure`, `RestartSec=5`,
`StartLimitIntervalSec=120`, `StartLimitBurst=5`.

## Configuration and schema checks

| Check | Command | Pass condition |
| --- | --- | --- |
| Configuration preflight | `python -m app.core.preflight` (or `python scripts/openjm_ops.py config`) | exit 0; report `RESULT: VALID` |
| Schema inspection | `python scripts/openjm_ops.py upgrade --check` | no error; prints schema before/head/after |
| Backup verification | `python scripts/openjm_ops.py verify <backup>` | `ok: true`, zero problems |

The preflight is the same validator called by the application lifespan, so a
passing CLI check and a successful startup agree.

## Proposed consolidated `doctor` command (design)

**Status: proposed for REL1. Not implemented.** Today an operator runs the checks
above individually. A single `python scripts/openjm_ops.py doctor` subcommand
would run them in order and print a single pass/fail summary, reusing the
existing libraries rather than reimplementing them:

1. **configuration** - `validate_configuration(get_settings())` (preflight);
2. **database** - a `SELECT 1` and the readiness `database` component;
3. **schema** - `assert_known_schema_revision` plus `migrations.revision == head`;
4. **storage** - upload/vector/backup writability and free space;
5. **model provider** - the gateway `probe()` (reported, never fatal);
6. **backup** - optionally `verify_backup` on the newest backup directory.

Each check should be independently addressable (`doctor --only storage`) so a
scripted deployment probe can call one, and the exit code should be non-zero
when any gating check fails. This keeps the "liveness versus readiness" split,
does not invent a new health surface, and never prints a secret.