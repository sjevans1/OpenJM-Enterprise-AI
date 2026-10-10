# Backup and restore contract

The formal contract for backup, verification and restore. It states what is
backed up, the exact manifest schema, the invariants a caller can rely on, and
the recovery window. Grounded in `backend/app/ops/backup.py` and
`scripts/openjm_ops.py`. The narrative operational view is
[../BACKUP_RESTORE.md](../BACKUP_RESTORE.md).

## Data classification

The authoritative set is declared once in `backend/app/ops/backup.py`
(`DATA_CLASSES`) and classified so the decision is reviewable.

| Data class | Classification | In a backup |
| --- | --- | --- |
| application metadata | authoritative | always |
| uploads | authoritative | when the upload directory exists |
| credential_key | authoritative | when the key file exists |
| configuration_snapshot | authoritative | always (non-secret effective config) |
| connector_cached_material | authoritative | via uploaded/stored material |
| vector_index | reproducible | opportunistic (`--skip-vector` to omit) |
| leases_and_cursors | ephemeral | never |

Ephemeral state is recovered by normal startup recovery, not by the backup:
in-flight report runs become `interrupted`, document leases are reclaimable,
and connector/scheduler state resumes from its persisted checkpoint.

## Commands

```bash
# Create (default destination: <backup_dir>/<UTC timestamp>, e.g. 20260101T000000Z)
python scripts/openjm_ops.py backup --dest /var/backups/openjm/20260101T000000Z

# Verify manifest and every checksum
python scripts/openjm_ops.py verify /var/backups/openjm/20260101T000000Z

# Restore into a clean, isolated target
python scripts/openjm_ops.py restore /var/backups/openjm/20260101T000000Z \
  --target-data-dir /srv/recovered/data \
  --target-database-url postgresql+asyncpg://openjm:secret@db:5432/openjm
```

## Backup layout

```
<backup-dir>/
  manifest.json                     # the schema below
  db/openjm.db                      # SQLite: online-backup snapshot
  db/openjm.pg.sql                  # PostgreSQL: pg_dump --format=plain
  uploads.tar.gz                    # when upload_dir exists
  keys/<key-file-name>              # when the credential key file exists
  config/effective-config.json      # non-secret effective configuration
  vector.tar.gz                     # reproducible, when included and non-empty
```

## Manifest schema

`manifest.json` (MANIFEST_VERSION = 1), written by `BackupManifest.to_dict()`:

| Field | Type | Meaning |
| --- | --- | --- |
| `manifest_version` | int | manifest schema version (currently `1`) |
| `created_at` | string | UTC ISO-8601 timestamp |
| `product` | string | product display name |
| `version` | string | `app.version.PRODUCT_VERSION` at backup time |
| `release_id` | string or null | `OPENJM_RELEASE_ID` |
| `profile` | string | `development` or `production` |
| `schema_revision` | string or null | recorded Alembic revision |
| `database_engine` | string | `sqlite` or `postgresql` |
| `classes` | object | the data-class taxonomy (a copy of `DATA_CLASSES`) |
| `files` | array | per-file records |

Each `files` entry: `path` (relative to the backup root), `sha256`, `bytes`,
`class`.

## Invariants

These hold for every backup, and are the contract a caller can depend on:

1. **Application-consistent snapshot.** SQLite uses the online backup API (a
   transactionally consistent copy while the app keeps serving). PostgreSQL uses
   `pg_dump --format=plain --no-owner`.
2. **Every file is recorded with a SHA-256 and byte count.** `verify` re-checks
   each one; a missing file, a checksum mismatch or a size mismatch is reported.
3. **Restore refuses an unverified backup.** `restore_backup` calls
   `verify_backup` and aborts when `ok` is false.
4. **Restore refuses a non-empty target without `--force`.** Data is never
   silently overwritten.
5. **Engine match is enforced.** A PostgreSQL target cannot restore a SQLite
   backup and vice versa.
6. **The backup is self-contained.** The restored deployment needs only the
   backup contents and its configuration; it does not depend on the original
   instance.
7. **No secret value is persisted as a value.** The configuration snapshot
   records secret-classified settings as presence flags
   (`<name>_present`), never as values. The credential key travels as a file,
   not inlined in JSON.

## Backing up PostgreSQL

`create_backup` resolves `pg_dump` from `PATH`, falling back to the binaries
bundled with `pgserver` when the client tools are not on `PATH`, and strips the
SQLAlchemy driver suffix (`postgresql+asyncpg://` to `postgresql://`) because
libpq rejects the suffix. The same resolution is used on restore with `psql`.

## Recovery window (RPO/RTO)

Recovery is restore-based. The recoverable window equals the backup cadence.

- **RPO** = backup interval. Daily backups with `pg_dump` give an RPO of up to a
  day; for a tighter target, use continuous archiving / WAL shipping at the
  PostgreSQL layer. OpenJM does not claim zero data loss.
- **RTO** = time to provision a host, restore the backup, and start the service
  (minutes on a prepared host).

Protect the backup directory as a secret: it contains the credential-vault key
material and the metadata database.

## Acceptance

`tests/test_vs8_dr_acceptance.py` performs a real destructive-style recovery on
an isolated target: seed representative data for two tenants, back up, destroy
the original, restore to a fresh target, start the restored system, then
validate identities/tenants, Knowledge evidence, structured-source metadata,
reports, connector metadata/schedules/notifications, credential decryption, and
tenant separation.