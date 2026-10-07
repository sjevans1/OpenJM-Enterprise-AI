# VS8 acceptance gates — self-hosted commercialization readiness

VS8 adds operational acceptance on top of the product gates (VS1–VS7). Each gate
below is backed by an automated test or a runnable script; the evidence for the
release candidate is recorded in `docs/VS8_ACCEPTANCE_REPORT.md`.

## Automated gates (`backend/tests/`)

| Gate | Test | What it proves |
| --- | --- | --- |
| Production config / security | `test_vs8_config_preflight.py` | safe production config passes; every unsafe combination fails closed; secrets never echoed |
| Version / health / readiness / logs / metrics | `test_vs8_system_and_hardening.py` | endpoint shapes, no secret leakage, secure headers, correlation id, `413`, `429`, redaction |
| Retention | `test_vs8_retention.py` | dry-run default, bounded, tenant-scoped, audited, never touches audit/report data |
| Backup / restore | `test_vs8_backup_restore.py` | application-consistent backup, checksum verification, clean-target restore, tamper detection |
| Upgrade / rollback guard | `test_vs8_upgrade.py` | fresh DB reaches head, idempotent upgrade, unknown future revision refuses startup |
| Backup/restore DR acceptance | `test_vs8_dr_acceptance.py` | real seed → backup → destroy → restore → validate across two tenants |
| PostgreSQL metadata | `test_postgres_metadata.py` | schema/triggers/migration on a real PostgreSQL server |
| Full regression | `pytest -q` | no VS1–VS7 leg regresses |

## Runtime / clean-install gates

These exercise a real deployment and are recorded with their environment.

1. **Linux clean install** — `scripts/install-linux.sh` on a host with no
   developer `.env`: install → preflight → migrate → health green → login →
   representative Knowledge/Data/Hybrid/report/action/connector operations.
2. **Windows / WSL2 clean install** — `scripts/install-wsl2.ps1` delegating to
   the Linux installer, then the same core acceptance. If a Windows/WSL2
   environment is unavailable, the exact limitation is recorded and the install
   path is validated deterministically instead — it is never claimed as run.
3. **Local / offline model** — configure a local OpenAI-compatible model; prove
   the core AI path with no required public model API; record capability limits.
4. **Private hosted model API** — configure the backend with a private
   OpenAI-compatible endpoint + server-side credential; prove the client never
   receives the credential or direct provider authorization; prove requests flow
   through the backend; prove outage/timeout is a bounded explicit failure with
   no silent public fallback; prove logs/health/config do not leak the
   credential.
5. **Restore** — restore a backup into a clean target and validate the product.
6. **Upgrade** — upgrade the accepted baseline to the candidate and validate.

## Required security/regression matrix

Production profile cannot silently use dev auth; missing critical secrets fail
closed; unknown/future schema refuses startup; clean install does not depend on
ambient developer files; backup contains all authoritative data; restore
preserves tenant isolation and does not resurrect revoked access; upgrade
preserves VS1–VS7 data; rollback is proven to its documented limits; health
endpoints leak no secrets/content; logs redact credentials; metrics leak no
tenant content; retention cannot cross tenant boundaries and is bounded/audited;
CORS/host/proxy follow production policy; upload/path-traversal limits hold;
rate/resource bounds fail safely; load causes no cross-tenant leakage;
connector/scheduler/action authorization fails closed under concurrency;
Workspace stays optional/independent; local/offline mode does not require
Workspace; private hosted routing is backend-only and never exposes provider
credentials; a private provider outage cannot fall back to an unapproved public
provider.

## Final release gate

Before PR review: all gates above green, exact-head GitHub Actions **fast** and
**full** green on the frozen SHA, release documentation complete, and known
limitations explicit. Then STOP for review — no merge without explicit approval.
