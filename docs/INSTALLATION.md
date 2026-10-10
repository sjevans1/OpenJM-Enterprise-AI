# Installation and bootstrap (REL1)

Repeatable installation for a supported Linux host and for Windows via WSL2,
with no source-code editing required. Both paths run the same installer so they
cannot drift apart.

REL1-A established the supported online installer contract. REL1-B extends that
contract with locked dependencies, offline bundle assembly, and release
integrity. See `docs/OFFLINE_BUNDLE.md` for the REL1-B bundle contract and
current NOT RUN gates.

## Prerequisites (checked automatically)

| Dependency | Version | Why |
| --- | --- | --- |
| Python | 3.11 only | backend + knowledge engine; current DB-GPT dependency chain does not support this release on 3.12 |
| Node.js | 22 | frontend build |
| npm | bundled with Node | frontend build |
| git | any modern | clone/update |

The installer fails early, with a message, when a required dependency or
supported version is missing. It is idempotent: re-running it preserves an
existing `.env` and never deletes production data.

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
3. creates a repo-local `backend/.venv`;
4. installs backend dependencies:
   - production: the application only, with no `[dev]` extras;
   - development: the application plus `[dev]` extras;
5. runs the configuration preflight (`python -m app.core.preflight`) and stops
   on an unsafe production configuration;
6. migrates the application-metadata database (`scripts/openjm_ops.py upgrade`);
7. builds the frontend (`frontend/dist`) unless `--skip-frontend` was selected;
8. runs the stdlib-only REL1-A post-install verifier.

Serving the app is deliberately explicit: the installer configures and validates
but does not start a background service. Use the systemd unit
(`deploy/systemd/openjm.service`) or Compose (`deploy/compose/`).

## Windows + WSL2

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-wsl2.ps1 -Profile production
```

The script:

- verifies WSL2 is present and warns if the default version is 1;
- ensures the `Ubuntu` distribution exists (installing it if needed);
- maps the Windows repository path to its WSL path;
- installs Linux prerequisites inside WSL if missing;
- propagates the selected development/production profile and frontend choice;
- delegates to `scripts/install-linux.sh`, so a WSL2 install and a native Linux
  install exercise the same code path.

Start the backend from inside WSL (this is the supported path; a Windows-native
backend is not a supported profile):

```bash
wsl -d Ubuntu -- bash -lc "cd /mnt/c/path/to/repo/backend && source .venv/bin/activate \
  && env -u PYTHONPATH -u PYTHONHOME .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000"
```

## Release payload integrity

REL1-B1 adds a stdlib-only integrity gate for assembled release payloads.

```bash
python3.11 scripts/release-integrity.py generate /path/to/openjm-release
python3.11 scripts/release-integrity.py verify /path/to/openjm-release
```

Production distribution should publish the generated manifest SHA-256 through a
trusted channel and pin it during verification:

```bash
python3.11 scripts/release-integrity.py verify /path/to/openjm-release \
  --expected-manifest-sha256 <trusted-sha256>
```

This integrity tool does **not** imply that the offline wheelhouse/npm cache is
already complete. Dependency locking, bundle assembly, and registry-blocked
installation remain REL1-B2/B3/B4 gates until executed and accepted.

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
