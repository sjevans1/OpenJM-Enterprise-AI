# Installation and bootstrap (VS8 Workstream B)

Repeatable installation for a supported Linux host and for Windows via WSL2,
with no source-code editing required. Both paths run the same installer so they
cannot drift apart.

## Prerequisites (checked automatically)

| Dependency | Version | Why |
| --- | --- | --- |
| Python | 3.11 (3.12 will not build DB-GPT) | backend + knowledge engine |
| Node.js | 20+ (22 tested) | frontend build |
| npm | bundled with Node | frontend build |
| git | any modern | clone/update |

The installer fails early, with a message, when a dependency, port, storage path
or permission is missing. It is idempotent: re-running it preserves an existing
`.env` and never deletes production data.

## Linux

```bash
git clone https://github.com/sjevans1/OpenJM-Enterprise-AI.git
cd OpenJM-Enterprise-AI
scripts/install-linux.sh --profile production      # or --profile development
```

What it does, in order:

1. verifies prerequisites and versions;
2. copies `.env.production.example` (production) or `.env.example`
   (development) to `.env` **only if `.env` is absent**;
3. creates a repo-local `backend/.venv` and installs `-e ".[dev]"`;
4. runs the configuration preflight (`python -m app.core.preflight`) and stops
   on an unsafe production configuration;
5. migrates the application-metadata database (`scripts/openjm_ops.py upgrade`);
6. builds the frontend (`frontend/dist`).

Serving the app is deliberately explicit: the installer configures and validates
but does not start a background service. Use the systemd unit
(`deploy/systemd/openjm.service`) or Compose (`deploy/compose/`).

## Windows + WSL2

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-wsl2.ps1
```

The script:

- verifies WSL2 is present and warns if the default version is 1;
- ensures the `Ubuntu` distribution exists (installing it if needed);
- maps the Windows repository path to its WSL path;
- installs Linux prerequisites inside WSL if missing;
- delegates to `scripts/install-linux.sh`, so a WSL2 install and a native Linux
  install exercise the same code path.

Start the backend from inside WSL (this is the supported path; a Windows-native
backend is not a supported profile):

```bash
wsl -d Ubuntu -- bash -lc "cd /mnt/c/path/to/repo/backend && source .venv/bin/activate \
  && env -u PYTHONPATH -u PYTHONHOME .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000"
```

## Fresh install vs upgrade

- **Fresh install**: no database file/URL yet — the upgrade step creates the
  schema at head and provisions the local identity.
- **Upgrade**: an existing database is adopted and upgraded additively. The
  installer never overwrites or deletes existing tables; see
  `docs/UPGRADE_ROLLBACK.md`.

## Uninstall / cleanup

Uninstalling removes the application and its virtual environment. It does **not**
delete user data unless explicitly requested:

```bash
# Stop the service / containers first.
rm -rf backend/.venv frontend/node_modules frontend/dist
# Data (delete ONLY if you intend to discard it):
#   ./data/            (development: db, uploads, vector, key)
#   /var/lib/openjm    (production: uploads, vector)
#   PostgreSQL database (drop only with explicit intent)
```

A release install does not depend on Hermes, developer home-directory state,
ambient `.env` files, or previous test services.
