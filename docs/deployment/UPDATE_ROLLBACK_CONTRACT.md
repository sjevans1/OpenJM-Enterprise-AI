# Update and rollback contract

The formal contract for applying a new application version to an existing
deployment, and for recovering when it must be undone. Grounded in
`backend/app/ops/upgrade.py`, `backend/app/migrations_runner.py`,
`backend/app/db.py` (`init_db`) and `scripts/openjm_ops.py`. The narrative
operational view is [../UPGRADE_ROLLBACK.md](../UPGRADE_ROLLBACK.md).

## The supported update path

An update is a code replacement plus an additive, idempotent schema migration.

```bash
# Inspect first: refuse an unknown/future schema, report versions
python scripts/openjm_ops.py upgrade --check

# Apply: preflight -> migrate -> postflight
python scripts/openjm_ops.py upgrade
```

The same migration runs at startup (`app.db.init_db`), so a service restart is a
safe upgrade.

### Preflight

`preflight_upgrade` reads the recorded revision and calls
`assert_known_schema_revision`. If the database records a revision this build
does not know (a schema written by a newer release), it raises
`UnknownSchemaRevisionError` and the command exits non-zero. An empty database
(no recorded revision) and an older but known revision both proceed; the latter
is upgraded.

### Migrate

`run_upgrade` calls `adopt_and_upgrade`. This:

1. detects a pre-migration database (a `conversations` table with no recorded
   revision) and adopts it by stamping the baseline revision, creating any
   genuinely missing baseline tables from the shared baseline metadata first;
2. applies each revision with `command.upgrade(config, "head")`.

Every revision is idempotent per object (`has_table`/`has_column`/`has_index`
guards), so a deployment upgraded in place is upgraded rather than aborted.

### Postflight

`run_upgrade` confirms the recorded revision equals this build's head, and the
report prints `schema before`, `schema head` and `schema after`.

## Contract invariants

1. **Refuse an unknown future schema.** Startup refuses a revision absent from
   this build's script directory rather than running newer code against a schema
   it cannot reason about.
2. **Migrations are additive and per-object idempotent.** Running the upgrade
   twice is safe.
3. **No customer data is touched.** The runner only touches OpenJM's own
   application-metadata database, never a connected structured source.
4. **A backup is recommended before an in-place upgrade of a populated
   deployment.** `backup_recommendation()` names the exact command and states
   that rollback from an incompatible schema is by restore, not downgrade.
5. **Postflight asserts head.** The reported "after" revision must match head or
   the report carries an error.

## Rollback contract

Rollback is explicit and honest about its limits. In preference order:

1. **Backward-compatible additive migrations** (the default). New columns are
   nullable or defaulted and existing rows are untouched, so the previous
   application version can usually still read the schema.
2. **Application rollback while the schema stays compatible.** Redeploy the
   previous application version against the same database. Safe only while no
   new column is required by the running code.
3. **Restore from a pre-upgrade backup.** When a migration is
   destructive/non-reversible, restore the backup into a clean target
   ([BACKUP_RESTORE_CONTRACT.md](BACKUP_RESTORE_CONTRACT.md)) and start the
   previous version against it.

**OpenJM does not claim a schema downgrade (`alembic downgrade`) is safe.**
`backend/app/ops/upgrade.py` never reverses a destructive migration in place.
Recovery is by restore.

## Version identity across an update

| Identity | Source | Surfaced |
| --- | --- | --- |
| Application version | `backend/app/version.py` (`PRODUCT_VERSION`) | `/api/version`, `/api/health`, logs |
| Release/build id | `OPENJM_RELEASE_ID` | `/api/version`, `/api/config/public` |
| Schema version | the Alembic revision | `/api/ready` (`migrations.revision` vs `migrations.head`) |

An update is complete when the application version reports the new value and
`migrations.revision` equals `migrations.head`.

## Container updates

For a Compose deployment, an update is: rebuild or load the new images, then
`docker compose -f deploy/compose/docker-compose.prod.yml up -d`. The backend
runs `ExecStartPre`-equivalent preflight (`python -m app.core.preflight`) and
then migrations at startup, so the schema advances as part of the roll-out. To
roll back while the schema is compatible, redeploy the previous images against
the same `openjm-db` volume; otherwise restore the pre-update backup into a fresh
database and point the backend at it.

## Acceptance

`tests/test_vs8_upgrade.py` proves a fresh database reaches head, the upgrade is
idempotent, and an unknown future revision refuses startup. PostgreSQL is
exercised where the environment permits (`tests/test_postgres_metadata.py`).