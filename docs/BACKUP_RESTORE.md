# Backup, restore and disaster recovery (VS8 Workstream E)

## Authoritative data set

`app/ops/backup.py` declares the taxonomy so the decision is reviewable:

| Data class | Classification | Included |
| --- | --- | --- |
| application metadata database | authoritative | yes |
| uploaded / local documents | authoritative | yes |
| connector-owned cached material | authoritative | yes (via uploads/stored material) |
| credential-vault key material | authoritative | yes (as key file) |
| configuration snapshot (non-secret) | authoritative | yes |
| vector / Knowledge index | reproducible | opportunistic (rebuildable from documents) |
| leases / in-flight cursors | ephemeral | no |

## Commands

```bash
# Create an application-consistent backup (default: <backup_dir>/<UTC timestamp>)
python scripts/openjm_ops.py backup --dest /var/backups/openjm/2026-01-01T000000Z

# Verify manifest + every checksum before trusting it
python scripts/openjm_ops.py verify /var/backups/openjm/2026-01-01T000000Z

# Restore into a clean, isolated target
python scripts/openjm_ops.py restore /var/backups/openjm/2026-01-01T000000Z \
  --target-data-dir /srv/recovered/data \
  --target-database-url sqlite+aiosqlite:////srv/recovered/data/openjm.db
```

## Guarantees

- **Application-consistent for SQLite**: the online backup API takes a
  transactionally consistent snapshot while the application keeps serving.
  PostgreSQL uses `pg_dump`.
- **Manifest**: UTC timestamp, product version, release id, profile, schema
  revision, data-engine and per-file SHA-256 + byte counts.
- **Integrity verification**: `verify` re-checks every checksum; `restore`
  refuses an unverified backup.
- **Writes during backup**: for SQLite the snapshot is consistent up to the
  moment it is taken; for PostgreSQL use `pg_dump` against a deployment whose
  write volume you accept, or `pg_basebackup` for a cluster-consistent copy.
- **Backup destination**: keep it on encrypted, permission-restricted storage;
  the backup contains the credential-vault key material, so protect it as a
  secret.
- **Restore is clean and isolated**: it refuses to write into a non-empty target
  unless `--force` is passed.
- **No dependence on the original instance**: the restored deployment needs only
  the backup contents and its configuration.
- **Tenant boundaries and authorization state are preserved**: identities,
  memberships and per-tenant ownership columns travel with the metadata DB.

## Recovery model and RPO/RTO

Retention restores state, not in-flight work. On restore:

- terminal operational records are preserved;
- in-flight `running` rows are recovered by the normal startup recovery
  (report runs become `interrupted`; document leases are reclaimable);
- connector/scheduler state resumes from its persisted checkpoint.

This is a **restore-based** recovery with a bounded window equal to your backup
cadence. Typical guidance: RPO = backup interval (for example 24h with daily
backups + WAL/continuous archiving for tighter targets); RTO = time to provision
a host, restore, and start (minutes). These are expectations to size against —
OpenJM does not claim zero data loss.

## Acceptance

`tests/test_vs8_dr_acceptance.py` performs a real destructive-style recovery on
an isolated target: seed representative VS1–VS7 data for two tenants → backup →
destroy the original → restore to a fresh target → start the restored system →
validate identities/tenants, Knowledge evidence, structured-source metadata,
reports, connector metadata/schedules/notifications, credential decryption, and
prove no cross-tenant corruption.
