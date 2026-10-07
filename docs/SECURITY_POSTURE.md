# Security posture and hardening (VS8 Workstream I)

## Production defaults

- **Dev auth disabled**: `auth_mode=oidc` is required; `auth_allow_dev_mode` is
  refused in production. There is no silent fallback from OIDC to dev identity.
- **CORS constrained**: an explicit origin list is required; the wildcard is
  refused in production.
- **Trusted proxy explicit**: `trust_proxy_headers` must be set deliberately;
  `trusted_hosts` must be an explicit list (no wildcard) so host-header attacks
  are constrained by `TrustedHostMiddleware`.
- **Secure headers**: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy: no-referrer`, `Cross-Origin-Opener-Policy: same-origin`,
  `Permissions-Policy` on every response. TLS is terminated at the reverse proxy
  (`deploy/compose/Caddyfile` includes HSTS).
- **Request/body/upload limits**: bodies default to 8 MB and multipart uploads
  to 25 MB; an over-size request is rejected with `413` before the body is read.
- **Rate/budget protection**: expensive endpoint families are rate-limited per
  client window when enabled (required in production). Two existing bounds also
  apply: the structured-query row cap and the report-run budget.
- **File type / path traversal**: uploads are stored by a generated name under
  the configured upload directory; extractors reject unsupported types.
- **Export/download authorization**: report exports are authorized per owner,
  the same as report reads.
- **Connector outbound network stays registry-bound**: connectors may only reach
  a registered type's configured endpoint; no generic egress feature is added.
- **Scheduler cannot escalate privilege**: every claimed occurrence re-proves the
  owner's *current* membership and permission before dispatch.
- **Error responses are safe**: a generic exception handler logs a category and
  returns `{"detail": "internal server error", "correlation_id": ...}` — no stack
  trace, no infrastructure detail.
- **No debug endpoints by default**: `metrics_enabled` controls `/api/metrics`
  only, and it exposes counters/gauges, never tenant content.

## Production-security test profile

`tests/test_vs8_config_preflight.py` is the "fails on unsafe combinations" test
profile: it asserts each production failure condition above raises, and that a
safe production configuration passes. `tests/test_vs8_system_and_hardening.py`
proves the `413` limit, the `429` limiter, the redaction helper, secure headers
and that health/readiness/config responses leak no secret.

## Adversarial coverage (existing, unchanged)

The VS5–VS7 security matrix still holds and is not weakened by operations:
tenant isolation, OIDC validation, governed actions, connector authorization,
and scheduler reauthorization all fail closed under concurrency.

## What OpenJM does not do

- No generic network-egress feature.
- No unsafe in-place cryptographic rotation.
- No arbitrary HTML/script from a white-label setting (display values are
  rendered as text, validated, and the accent colour must match a hex pattern).
