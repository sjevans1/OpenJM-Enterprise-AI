# Upgrade and rollback (VS8 Workstream G)

## Supported upgrade path

From the accepted pre-VS8 baseline (VS7) to the VS8 release candidate, using
additive, guarded migrations.

```bash
# Inspect first — refuse an unknown/future schema and report versions
python scripts/openjm_ops.py upgrade --check

# Apply (preflight → migrate → postflight)
python scripts/openjm_ops.py upgrade
```

The upgrade step:

1. **preflight** — read the current revision; if it is a revision this build does
   not know (a schema written by a newer release), refuse and exit non-zero;
2. **backup recommendation** — an operator is told to back up a populated
   deployment before an in-place upgrade;
3. **migrate** — `adopt_and_upgrade` adopts a pre-migration database (stamps the
   baseline, creates any genuinely missing baseline tables) and applies each
   revision. Every revision is **idempotent per object**, so a deployment
   upgraded in place is upgraded rather than aborted;
4. **postflight** — confirm the recorded revision now equals head.

Startup (`app.db.init_db`) runs the same preflight and migration, so a service
restart is a safe upgrade. **Startup refuses an unknown future schema revision**
rather than running newer code against a schema it cannot reason about.

## Application version and schema version

- Application version: `app.version.PRODUCT_VERSION`, surfaced in `/api/version`
  and `/api/health`.
- Schema version: the Alembic revision, surfaced in `/api/ready`
  (`migrations.revision` vs `migrations.head`).

## Rollback

Rollback is **explicit** and honest about its limits.

Preferred, in order:

1. **Backward-compatible additive migrations** — the default. New columns are
   nullable or defaulted; existing rows are untouched, so the previous
   application version can usually still read the schema.
2. **Application rollback while the schema stays compatible** — redeploy the
   previous application version against the same database. Safe only while no
   new column is *required* by the running code.
3. **Restore from backup** — when a migration is destructive/non-reversible.
   Restore the pre-upgrade backup into a clean target
   (`docs/BACKUP_RESTORE.md`) and start the previous version against it.

**OpenJM does not claim a schema downgrade (`alembic downgrade`) is safe.** No
destructive migration is reversed in place; the documented recovery is by
restore.

## Acceptance

`tests/test_vs8_upgrade.py` proves: a fresh database reaches head; the upgrade is
idempotent; and an unknown future revision refuses startup. The candidate is also
verified against a real PostgreSQL server where the environment permits
(`tests/test_postgres_metadata.py`).
