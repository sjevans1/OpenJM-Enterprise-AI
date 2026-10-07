# Configuration and secret bootstrap (VS8 Workstream C)

Configuration is read from the repository-root `.env` (or process environment),
with the `OPENJM_` prefix. Production startup performs a **fail-closed**
preflight; an unsafe combination refuses to serve.

## Validate before you start

```bash
# from backend/ with the venv active
python -m app.core.preflight          # human-readable report
python -m app.core.preflight --json   # machine-readable
python scripts/openjm_ops.py config   # same check via the operator CLI
```

Exit code is `0` when the configuration is valid for its profile and non-zero
otherwise. The same check runs inside the application lifespan (`app.main`), so
`uvicorn` will not start a production deployment with an unsafe configuration.

## Classification

`app/core/preflight.py` declares the whole surface and its classification
(`CONFIGURATION_SURFACE`). Categories: **required**, **optional**,
**development-only**, **secret**, **restart-required**, **safe-default**.

Selected fields:

| Setting | Class | Notes |
| --- | --- | --- |
| `deployment_profile` | required | `development` \| `production` |
| `auth_mode` | required | `oidc` required in production |
| `oidc_issuer` / `oidc_client_id` / `oidc_redirect_uri` | required (prod) | SSO |
| `oidc_client_secret` | **secret** | external IdP secret |
| `database_url` | required | PostgreSQL in production |
| `credential_encryption_key` | **secret** | Fernet key material |
| `credential_key_file` | restart-required | fallback key file |
| `cors_origins` | required | explicit list; no `*` in production |
| `trusted_hosts` | required | explicit list; no `*` in production |
| `trust_proxy_headers` | required | honour `X-Forwarded-*` behind a proxy |
| `model_provider_mode` | required | `local` \| `private_remote` |
| `model_base_url` | required | backend-only endpoint |
| `model_api_key` | **secret** | backend-only provider credential |
| `model_allow_insecure_http` | development-only | plain-HTTP model endpoint |
| `model_provider_fallback` | required | must be `none` in production |
| `rate_limit_enabled` | safe-default | must be `true` in production |
| `retention_enabled` | optional | opt-in lifecycle |
| `product_name` etc. | optional | white-label display text |

## Production failure conditions (fail closed)

Startup refuses when any of these holds in a `production` profile:

- `auth_mode` is not `oidc`, or dev-mode fallback is enabled;
- OIDC issuer/client id/client secret/redirect URI missing, or the secret is a
  placeholder;
- no credential-vault key material, or the key is a placeholder;
- the metadata database is not PostgreSQL;
- CORS origins or trusted hosts are empty or contain the wildcard;
- a `private_remote` model endpoint is plain HTTP, has no credential, or the
  credential is a placeholder;
- `model_provider_fallback` is not `none` (silent public fallback);
- rate limiting is disabled.

The report **never echoes a secret value**; it names the setting and the reason.

## Secret provisioning

- No committed production secrets: `.env.production.example` contains
  placeholders only, and `.env` is gitignored.
- Supply secrets through environment variables or secret files with `0600`
  permissions (systemd `EnvironmentFile`, Compose `secrets:`, or a mounted file).
- No known default passwords are shipped.
- Diagnostics never print a secret value.

## Secret rotation

| Secret | Rotation |
| --- | --- |
| OIDC client secret | rotate at the IdP, update the secret file, restart |
| Database credentials | rotate in PostgreSQL, update the secret file, restart |
| Connector credentials | rotate in-product (VS7): a new active credential row supersedes the previous one; the old value is never recovered in place |
| `credential_encryption_key` | **not** rotated in place: re-encrypt data source secrets with the new key under a controlled migration (read old → write new) before removing the old key. Do not overwrite the key while ciphertext encrypted with it remains. |

Unsafe in-place cryptographic rotation is intentionally not implemented.
