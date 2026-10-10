# Logging, diagnostics and the support bundle (REL1)

What a running deployment emits for diagnosis, where it comes from, and the
shape of a single support artefact an operator can hand to support. The logging,
correlation and metrics surface is implemented and grounded in
`backend/app/core/observability.py`, `backend/app/core/middleware.py` and
`backend/app/api/system.py`. The support bundle is a **design proposed for
REL1** and is not implemented. The endpoint reference is
[../OPERATIONS.md](../OPERATIONS.md); the check catalogue an operator runs by
hand is [HEALTH_DOCTOR_CHECKS.md](HEALTH_DOCTOR_CHECKS.md).

## Structured logging

`app.core.observability.configure_structured_logging()` installs one JSON
formatter on the root logger at application start (called first in the
`app.main` lifespan). It is a no-op if already installed. Every record is one
JSON object on one line.

Fixed fields on every record: `ts` (`%Y-%m-%dT%H:%M:%S%z`), `level`, `logger`,
`message`, `correlation_id`. Optional fields are emitted only when a caller
passes them: `category`, `duration_ms`, `tenant_id`, `principal_id`,
`failure_category`, `audit_id`. When a record carries exception info, an
`error` object is added as `{error_type, error}` with the message truncated to
300 characters.

### Correlation ids

- A per-request `ContextVar` holds the correlation id. Concurrent requests never
  share one.
- `CorrelationIdMiddleware` honours an inbound `X-Correlation-ID` header, up to
  a safe maximum of 64 characters, and otherwise generates a UUID.
- The id is echoed on the response as `X-Correlation-ID` (both by the
  correlation-id middleware and defensively by the security-header middleware).
- `CorrelationIdFilter` injects the current id into every log record, so a log
  line and an HTTP response can be joined by the same id.
- The generic exception handler
  (`app.core.middleware.safe_exception_handler`) logs a failure category and
  returns `{"detail": "internal server error", "correlation_id": ...}`. It never
  returns a stack trace or infrastructure detail.

### Secret redaction

`safe_fields(**fields)` replaces the value of any key matching the secret
pattern with `[redacted]`. The pattern is
`secret|password|token|api[_-]?key|authorization|credential|bearer|client_secret|private[_-]?key|fernet`.
Redaction is by key name, so a secret value is never inspected, hashed or
partially echoed. No endpoint response and no log line carries a bearer token,
connector secret, raw credential, provider base-URL host or full sensitive
document.

## Metrics as a diagnostics surface

`GET /api/metrics` returns Prometheus text exposition and is the machine-readable
companion to the logs. It is guarded by the same `OPENJM_OPS_TOKEN` bearer as
`/api/ready/detail`: without a token, a production profile hides it (404), and a
disabled deployment returns 404. Metric labels are a closed,
low-cardinality vocabulary; they never carry tenant content, a principal id, a
URL or an error message. The declared metric names are in
`app/core/observability.py` (`METRICS.declare(...)`), so the catalogue is
reviewable in code rather than only in prose.

## What already answers a diagnostic question

| Question | Surface | Grounded in |
| --- | --- | --- |
| Is the process serving? | `GET /api/health` | `app/api/system.py` |
| Can it serve core requests? | `GET /api/ready` | `app/api/system.py` |
| Which component is degraded? | `GET /api/ready/detail` (ops token) | `app/api/system.py` |
| What version and release id? | `GET /api/version`, `GET /api/config/public` | `app/api/system.py`, `app/version.py` |
| Does the schema match the build? | `migrations.revision` vs `migrations.head` in readiness | `app/api/system.py`, `app/migrations_runner.py` |
| Is the configuration safe for its profile? | `python -m app.core.preflight` (or `scripts/openjm_ops.py config`) | `app/core/preflight.py` |
| What happened on one request? | the JSON log line carrying its `correlation_id` | `app/core/observability.py` |
| Rate and latency of requests? | `GET /api/metrics` | `app/core/middleware.py`, `app/core/observability.py` |

## Proposed support bundle (design)

**Status: proposed for REL1. Not implemented.** Today an operator collects the
surfaces above by hand. A single command,
`python scripts/openjm_ops.py support-bundle`, would gather them into one
timestamped archive. It reuses the existing libraries; it does not add a new
health surface and it does not change the running product.

Proposed layout:

```
openjm-support-<UTC timestamp>/
  manifest.json            # bundle schema version, created_at, product version,
                           # release id, release train, deployment profile,
                           # schema revision, and a per-file SHA-256 + bytes list
  version.json             # app.version.build_info(settings)
  config/
    preflight.json         # validate_configuration report (settings named, no values)
    effective-config.json  # the non-secret snapshot app/ops/backup.py already builds
  readiness.json           # /api/ready/detail component map (redacted by construction)
  schema.json              # recorded revision, script head(s), migration runner result
  metrics.txt              # METRICS.render() Prometheus text snapshot
  logs/
    openjm.log             # a bounded trailing window of the JSON log stream
  backups/
    latest-verify.json     # verify_backup() result for the newest backup dir, if any
```

Design rules, matching the code that already exists:

1. **Never include a secret or tenant content.** The bundle is assembled from
   surfaces already redacted by construction (readiness, preflight, the
   non-secret config snapshot, metrics with closed labels). It must not read
   `.env`, a secret file, the credential-vault key, a provider credential or a
   customer document.
2. **Name a setting, never a value.** The preflight report already follows this;
   the bundle inherits it.
3. **Integrity metadata in the manifest**, mirroring the backup manifest
   (`archived content with SHA-256 + bytes per file`) so the recipient can detect
   truncation before trusting the archive.
4. **Bounded.** The log window and every count are capped, so a bundle is a
   fixed-size artefact rather than a growing dump.
5. **Off by default and explicit on invocation.** No bundle is produced unless
   the command is run.

The bundle is a transport for the checks an operator already runs. It does not
replace `doctor` ([HEALTH_DOCTOR_CHECKS.md](HEALTH_DOCTOR_CHECKS.md)); `doctor`
decides pass/fail, the bundle carries the evidence.

## What is not a diagnostics surface

- No shell, SQL or filesystem tool is exposed to the model or to a tenant. The
  operational surface is read-only and credential-guarded.
- No stack trace or infrastructure exception text reaches a response or a
  customer log line.
- No proprietary observability service is required; logs are JSON on stdout and
  metrics are Prometheus text.
