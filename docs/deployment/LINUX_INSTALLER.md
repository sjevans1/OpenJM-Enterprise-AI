# Linux installer architecture

The architecture of `scripts/install-linux.sh`, the single repeatable installer
for a supported Linux host. It is also the script the Windows/WSL2 bootstrap
delegates to, so a WSL2 install and a native Linux install exercise the same
code path. Grounded in `scripts/install-linux.sh`.

## Design goals

- **Idempotent.** Re-running preserves an existing `.env` and never deletes
  production data.
- **Fail early.** A missing dependency, wrong version or missing prerequisite
  stops the run with a message before anything is installed.
- **No source edits.** It never edits the checkout and never depends on a
  developer home directory, Hermes state, ambient `.env` or a previous test
  service.
- **No service start.** It configures and validates but does not start a
  background service; serving is explicit (systemd unit or Compose).

## Interface

```
scripts/install-linux.sh [--profile development|production] [--skip-frontend]
```

| Flag | Effect |
| --- | --- |
| `--profile development` (default) | copies `.env.example` to `.env` when absent |
| `--profile production` | copies `.env.production.example` to `.env` when absent |
| `--skip-frontend` | skips the Node/npm prerequisite check and the frontend build |

## Stages

1. **Prerequisites (version-checked).** `git` required. Python must be 3.11
   (the script probes `python3.11`, `python3.12`, `python3` and accepts only a
   3.11 interpreter; DB-GPT pins aiohttp 3.8.4, which does not build on 3.12).
   Unless `--skip-frontend`, Node 20+ and `npm` are required.
2. **Configuration template.** If `.env` exists it is left untouched. Otherwise
   the profile-appropriate template is copied and the operator is told to edit
   it. The production copy is a reminder to set real secrets before start.
3. **Backend environment.** Create `backend/.venv` if absent, activate it,
   upgrade pip, install `-e ".[dev]"` with `--prefer-binary`.
4. **Configuration preflight.** `python -m app.core.preflight` runs inside the
   venv. A non-zero exit (an unsafe production configuration) stops the install.
5. **Database migration.** `python ../scripts/openjm_ops.py upgrade --check`
   (advisory) then `python ../scripts/openjm_ops.py upgrade` applies the schema.
6. **Frontend build.** `npm ci --no-audit --no-fund && npm run build` into
   `frontend/dist`, unless `--skip-frontend`.

On success it prints the start command (`uvicorn app.main:app` on
`127.0.0.1:8000`), a reminder to serve the built frontend behind the reverse
proxy, and a readiness check (`curl -s http://127.0.0.1:8000/api/ready`).

## Relationship to the runtime

The installer builds and validates; it does not configure the process
supervisor. After install, the operator chooses one of:

- **systemd**: install `deploy/systemd/openjm.service`, write an
  `EnvironmentFile` at `/etc/openjm/openjm.env` (`0600`, owner `openjm`),
  `systemctl daemon-reload && systemctl enable --now openjm`. The unit runs the
  preflight as `ExecStartPre` and then uvicorn (see [PROFILES.md](PROFILES.md)).
- **Compose**: build and bring up `deploy/compose/docker-compose.prod.yml`.

## Fresh install versus upgrade

- **Fresh install:** no database exists, so the migration step creates the schema
  at head and provisions the local identity.
- **Upgrade:** an existing database is adopted and upgraded additively; tables
  are never dropped ([UPDATE_ROLLBACK_CONTRACT.md](UPDATE_ROLLBACK_CONTRACT.md)).

## Uninstall / cleanup

Uninstalling removes the application and its virtual environment; it does not
delete user data unless explicitly requested. The commands and the data paths to
discard (or keep) are listed in [../INSTALLATION.md](../INSTALLATION.md).

## Known constraint

The installer assumes network access to PyPI, the npm registry and (for the
frontend build) Node availability. An air-gapped install needs the offline
bundle described in [OFFLINE_BUNDLE.md](OFFLINE_BUNDLE.md); the installer does
not yet accept an offline source. That extension is REL1 packaging work, not
present in this lane.