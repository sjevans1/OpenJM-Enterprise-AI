# Release packaging, versions and white-label (VS8 Workstream H)

## Version identity

| Source | Value | Surfaced in |
| --- | --- | --- |
| `backend/pyproject.toml` `version` | product version | build metadata |
| `backend/app/version.py` `PRODUCT_VERSION` | product version | `/api/version`, `/api/health`, logs |
| `frontend/package.json` `version` | web version | build |
| `OPENJM_RELEASE_ID` | release/build identifier | `/api/version`, `/api/config/public` |
| Alembic revision | schema version | `/api/ready` |

Single source of truth for the runtime product version is
`backend/app/version.py`.

## Packaging model

- **Linux service**: `deploy/systemd/openjm.service` (venv install).
- **Containers**: `deploy/compose/docker-compose.prod.yml` + `Dockerfile` +
  `Caddyfile` (TLS boundary).
- **Install**: `scripts/install-linux.sh`, `scripts/install-wsl2.ps1`.
- **Config template**: `.env.production.example` (placeholders only).

## Release notes / changelog process

Releases are cut from `main` after the acceptance gate (`docs/ACCEPTANCE.md`)
passes on the frozen commit. Each release records: frozen SHA, product version,
release id, the CI run ids for the fast and full tiers, and the acceptance
evidence summary appended to the release PR. Breaking changes and known
limitations are listed explicitly.

## White-label configuration

White-labelling is configuration, not a fork. Supported settings (all in
`.env`, all optional):

| Setting | Rendered as |
| --- | --- |
| `OPENJM_PRODUCT_NAME` | product/display name |
| `OPENJM_ORGANIZATION_NAME` | organization name |
| `OPENJM_BRAND_LOGO_URL` | logo asset reference |
| `OPENJM_BROWSER_PAGE_TITLE` | browser/page title |
| `OPENJM_SUPPORT_CONTACT` | support/contact text |
| `OPENJM_THEME_ACCENT` | accent colour (validated hex only) |

Safety:

- values are applied with `textContent`/`setAttribute` — **never** `innerHTML` —
  so a configured name cannot become an HTML/script injection surface;
- the accent colour is applied only when it matches a hex-colour pattern;
- security-sensitive names and endpoints are not settable (the provider base
  URL, model credential, OIDC secret and CORS/host policy are deployment-only);
- the whole frontend is **not** redesigned for white-labelling; only display
  metadata and one validated accent variable are applied.

The same metadata is exposed at `GET /api/config/public` (display text only).
