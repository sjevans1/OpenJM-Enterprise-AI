# Operations: health, readiness, logs and metrics (VS8 Workstream D)

## Endpoints

| Endpoint | Purpose | Auth |
| --- | --- | --- |
| `GET /api/health` | liveness — the process is up | none |
| `GET /api/ready` | public readiness — a minimal, cheap go/no-go | none |
| `GET /api/ready/detail` | detailed readiness — per-component status | ops token |
| `GET /api/version` | product/release identity | none |
| `GET /api/config/public` | white-label display metadata | none |
| `GET /api/metrics` | Prometheus-style metrics (when enabled) | ops token |

The detailed operational surface (`/api/ready/detail`, `/api/metrics`) requires
the `OPENJM_OPS_TOKEN` bearer (`Authorization: Bearer <ops-token>`). Without a token
configured, those endpoints stay hidden (404) in a production profile; in
development they stay open for local/CI convenience. This bounds an
unauthenticated probe: the public `/api/ready` never runs the migration runner,
a DB-wide count or a remote model-provider call.

`/api/ready` returns only `{"ready": bool, "status": ...}`. The public `ready`
flag reflects the components required to serve core requests — `database` and
`storage`.

`/api/ready/detail` returns the component map. Its `ready` flag reflects only the
components required to serve core requests — `database`, `migrations`,
`storage`. The `model_provider`, `scheduler`, `connectors`, `notifications` and
`knowledge` components are reported but do **not** gate readiness, so a
model-provider outage is visible without taking the process out of rotation.

Components reported:

- **database** — connectivity + pool size/checked-out (latency);
- **migrations** — current revision and whether it equals head;
- **storage** — upload directory writability and free space (a low-space warning
  is reported, status `warning`);
- **knowledge** — DB-GPT/vector path health;
- **model_provider** — provider mode, model id, transport (http/https),
  fallback policy, and reachability. **Never** the base URL host, credential or a
  token;
- **scheduler** — enabled flag, active schedules, last run time;
- **connectors** — instance counts by status (no content, no secrets);
- **notifications** — pending count.

No secret or tenant content appears in any operational response; a test asserts
this.

## Reading detailed readiness during a dependency outage

`/api/ready/detail` is designed so that **no single failed dependency aborts the
report**. When PostgreSQL is unavailable:

- the request still returns **HTTP 200** and never 500;
- the normal top-level shape is preserved: `ready`, `profile`, `components`;
- `ready` becomes `false`;
- `components.database.status` becomes `error` (or another bounded degraded/error
  status), and other DB-backed components (`migrations`, `scheduler`,
  `connectors`, `notifications`) report bounded `error`/`degraded` status rather
  than raising;
- non-database components (`storage`, `knowledge`, `model_provider`) are still
  reported, so the map stays complete and you can see which parts are healthy.

Only bounded exception **class names** are surfaced as component detail. No
connection string, credential, SQLAlchemy transport message or traceback is ever
placed in the response, so the body is safe to paste into an incident ticket.

When the database returns, detailed readiness recovers on its own - the backend
does **not** need to be restarted.

`/api/ready` is the cheap public go/no-go and degrades to
`{"ready": false, "status": "degraded"}` in the same situation. Structured JSON
logs and correlation IDs are the complementary diagnostics when the component
map only tells you *which* component is unhappy; the logs tell you why.

## Structured logging

`app.core.observability` installs a JSON formatter with a correlation id and a
safe, fixed field set: `category`, `duration_ms`, `tenant_id`, `principal_id`,
`failure_category`, `audit_id` when present.

- Every request carrying an `X-Correlation-ID` header keeps it; otherwise a new
  id is generated and echoed on the response.
- `safe_fields()` redacts any key matching a secret pattern
  (`secret|password|token|api_key|authorization|credential|bearer|...`), so a
  caller cannot accidentally log a credential — redaction is by key name and the
  value is never inspected.
- Never logged: bearer tokens, connector secrets, raw credential values, or full
  sensitive documents for diagnostics.

## Metrics

`GET /api/metrics` (Prometheus text format; disabled deployments return 404;
requires the `OPENJM_OPS_TOKEN` bearer, or hidden in production without one):

| Metric | Type | Meaning |
| --- | --- | --- |
| `openjm_http_requests_total` | counter | requests by method/route/status |
| `openjm_http_request_duration_seconds` | histogram | request latency |
| `openjm_http_request_errors_total` | counter | failed requests |
| `openjm_model_requests_total` | counter | model requests by outcome |
| `openjm_model_request_duration_seconds` | histogram | model latency |
| `openjm_retrieval_duration_seconds` | histogram | retrieval latency |
| `openjm_scheduler_runs_total` | counter | scheduler ticks by outcome |
| `openjm_connector_sync_total` | counter | connector sync outcomes |
| `openjm_db_pool_checked_out` | gauge | pool saturation |
| `openjm_start_time_seconds` | gauge | process start |

Labels are a closed, low-cardinality vocabulary — never tenant content, a
principal id, a URL or an error message.

No proprietary observability service is required; any Prometheus-compatible
scraper works, or the endpoint can be polled directly.

## Scheduler

The in-process scheduler tick is opt-in (`OPENJM_SCHEDULER_ENABLED=true`). A
tick is bounded by `OPENJM_SCHEDULER_TICK_LIMIT`; authorization is re-proved at
execution time for every claimed occurrence (VS7 semantics are unchanged under
operations).
