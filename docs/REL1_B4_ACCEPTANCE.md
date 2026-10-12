# REL1-B4 host-acceptance evidence

This document holds two qualifications. The **original REL1-B4 record**
artifact source `44ceb0c7` is retained below as history. The **current-main host
qualification** (2026-10-11, source `7e859f2801bee1d6b5d52688b633709e5e9ed27d`)
is recorded in its own section and is the operative status; the historical tables
must not be read as current-artifact evidence.

REL1-B4 closes the REL1 offline-release train: the production application-wheel
install (removing the offline editable-install blocker), a deterministic release
archive (B4-F), tamper-negative and path-traversal coverage (B4-D),
machine-testable Windows/WSL2 contract checks (B4-E), and this host-acceptance
record (B4-H).

Rows marked `PASS` were executed and the result recorded. Rows marked `NOT RUN`
were not executed in this environment and are **not claimed**. Where a stricter
equivalent was executed on the acceptance host it is stated under the row.

## Environment of record

| Field | Value |
| --- | --- |
| Acceptance host | WSL2 (Ubuntu guest) on the maintainer workstation, non-root |
| Python | 3.11.15 |
| Node / npm | 22.23.2 / 10.9.8 |
| Source Git SHA (artifact build) | `44ceb0c7ba15b5cee00a0a5e73d2ee9b4a7d830d` (branch `rel1-b4-acceptance`, base `main@f1f06b58`) |
| Free disk during acceptance | ~813 GB |

## Reference identity

| Field | Value |
| --- | --- |
| Product version | `0.2.0` (`backend/app/version.py::PRODUCT_VERSION`) |
| Release train | `REL1` (`backend/app/version.py::RELEASE_TRAIN`) |
| Accepted production lock sha256 | `90afebf3c719b1db838cbf77a3799477c8ac312d16817aca7fab670b34fde72b` |
| Accepted development lock sha256 | `fa23810518e9a3646c570f1c54a94e6dadecfef49d0b82f0c4ed7279d40f7bd0` |
| Resolver toolchain | pip 26.0.1 / pip-tools 7.5.3 |
| Backend runtime | Python 3.11 |
| Frontend build runtime | Node.js 22 |
| Production install | excludes Python `[dev]` extras |

## Current-main host qualification (2026-10-11)

Restamped to the accepted product baseline
`main@7e859f2801bee1d6b5d52688b633709e5e9ed27d` (QA1, QA2, QA3 and OPS1 accepted
and merged). Rows below are the current status; the earlier tables are history.

### Environment inventory (Phase 1)

| Env | Requirement | Present | Classification |
| --- | --- | --- | --- |
| A | native/separate clean Linux host, development | no | **NOT RUN** |
| B | native/separate clean Linux host, production (PostgreSQL, production paths) | no | **NOT RUN** |
| C | Windows host with WSL2 | yes | executed (see below) |

Why A/B are NOT RUN, exactly:

- no separate Linux machine is attached, and no virtualization tooling is
  installed (multipass, libvirt/virt-install, qemu-system-x86_64, vagrant,
  VirtualBox and lxc are all absent);
- the WSL2 distro is excluded by the acceptance rule as a clean Linux host, and a
  Docker container is likewise not a host;
- provisioning one would require root; `sudo` here requires a password, so no
  non-interactive apt/qemu/systemd/PostgreSQL provisioning is possible;
- paid or remote cloud infrastructure was not used (not authorised).

`/dev/kvm` is present, so a VM is physically conceivable, but qemu cannot be
installed without root and no authorised external host was available.

### Reference identity (current artifact)

| Field | Value |
| --- | --- |
| Source Git SHA | `7e859f2801bee1d6b5d52688b633709e5e9ed27d` |
| Product version / train | `0.2.0` / `REL1` |
| Production lock sha256 | `90afebf3c719b1db838cbf77a3799477c8ac312d16817aca7fab670b34fde72b` (unchanged from the accepted lock) |
| Development lock sha256 | `fa23810518e9a3646c570f1c54a94e6dadecfef49d0b82f0c4ed7279d40f7bd0` (unchanged) |
| Application wheel | `openjm_enterprise_ai_backend-0.2.0-py3-none-any.whl` |
| Application wheel sha256 | `106bc5513f5ac73026b3c678f8498526ca2a0603cfc386da13470931b319250b` |
| Production wheelhouse | 182 distributions, 3.1 GB |
| Bundle payload files | 916 |
| Bundle size | 3.1 GB |
| npm offline cache digest | `ea249931ecd51bf628e075f0c6062e0996c3ba4f405c99c99d5bbb527703fc2e` |
| `RELEASE-MANIFEST.json` sha256 | `fb97c0bf1ddd30703eaab94e46531e62cdf371da4ffc5e8fdd315acac59c9702` |
| Release archive | `openjm-rel1-0.2.0.tar.gz` |
| Archive bytes | `3243305430` |
| Archive sha256 | `ff707eb540ff6395da7a36433b6c98d3bc9d8a9222dccba871ed36c74a35a6bd` |

The application wheel digest differs from the `44ceb0c7` artifact because the
product code changed (QA2/QA3 corrections and OPS1). The production and
development locks are unchanged, so lock-level evidence remains valid.

### Bundle toolchain note (not a product defect)

The first local bundle build failed with `aiohttp==3.8.4` unresolvable under
pip 26.1.2. Rebuilding with the CI-equivalent pip 24.0 succeeded. The accepted
lock-drift gate builds the same production wheelhouse from the same lock and
passes on this SHA in CI, so this is a resolver-toolchain difference in the local
environment; the bundle must be built with the pinned resolver toolchain.

### Current status (every B4 row)

| # | Item | Status | Evidence |
| --- | --- | --- | --- |
| 1 | Clean Linux **development** install | **PASS** (2026-10-12) | Executed for real on a fresh Docker Agentic Platform CLOUD sandbox `openjm-rel1-dev` (`sbx_001m4qacck9mzv3jptmrzqm00mz`, medium 4 vCPU / 8 GiB, Ubuntu 26.04.1 LTS, kernel 6.12.103, x86_64). Clean-host proof: no OpenJM checkout, no `.venv`, no `node_modules`, no OpenJM database, bare interpreter (pip list = 6). Independent GitHub clone at `2456f92a74d091ed425e24256f9910152671b4ca`. Operator prerequisites provisioned first: Python 3.11.9 built from the GPG-verified python.org source (digest `e7de3240...`, independently re-verified against an unrelated mirror, prefix `/opt/openjm/python-3.11`) and the distribution's own Node v22.22.1. `scripts/install-linux.sh --profile development` then ran unmodified and exited **0** (`REL1-A VERIFY OK: version=0.2.0 profile=development frontend=present`), migration current == head `0021_support_content_scope`, frontend built, backend started from the new venv on port 18077, `/api/health` 200, `/api/ready` 200 `ready:true`. See the evidence table below. |
| 2 | Clean Linux **production** install (PostgreSQL + production paths) | **NOT RUN** | no clean Linux host, no root, no PostgreSQL (inventory B) |
| 3 | Offline committed-lock install of the **current** artifact | **PASS** (subset) | user-space venv: `pip install --no-index --find-links python/wheelhouse --require-hashes -r python/requirements.lock` then `pip install --no-deps python/app/*.whl`; `app` imported from `site-packages`, `pip show` 0.2.0, dev extras present 0. Not a clean-host row. |
| 4 | Trusted-digest verification | **PASS** | `release-integrity.py verify <bundle> --expected-manifest-sha256 fb97c0bf...` -> `verified release payload: 916 files` |
| 5 | Tamper-negative (bounded, practical) | **PASS** | on a disposable hardlink copy, mutating one payload (`app/README.md`) was refused (`ERROR: size mismatch`); the canonical bundle re-verified clean at 916 files |
| 6 | Deterministic release archive | **PARTIAL** | the current artifact was archived once (`ff707eb5...`, `3243305430` bytes). A byte-identical second build was not re-run; the deterministic property is a property of `build-rel1-archive.sh` and the previous two-build equality belongs to the `44ceb0c7` artifact. |
| 7 | Windows/WSL2 **runtime** install | **PASS** (2026-10-12) | Real supported chain executed on the disposable distro `openjm-rel1b4`: Windows PowerShell 5.1.26100.9444 on Windows 11 Pro build 26200 -> hardened `scripts/install-wsl2.ps1` (WSL 2.6.3.0, kernel 6.6.87.2, Ubuntu 24.04.5, root uid 0) -> `bash scripts/install-linux.sh --profile development`. Operator prereqs provisioned before the run: Python 3.11.9 built from the GPG-verified python.org source (`e7de3240...`, prefix `/opt/openjm/python-3.11`, `/usr/local/bin/python3.11`) and Node v22.14.0 from the official archive (checksum `69b09dba...`). RepoPath `C:\rel1b4\OpenJM-Enterprise-AI` -> `/mnt/c/rel1b4/OpenJM-Enterprise-AI` at `c3937c88`. Bootstrap exit 0; frontend built (vite, 137 npm packages, `dist/` emitted); `REL1-A VERIFY OK: version=0.2.0 profile=development frontend=present`; migration current == head `0021_support_content_scope`; backend started from the new venv on port 18160; `/api/health` 200; `/api/ready` 200 `{"ready":true,"status":"ready"}` |
| 8 | Windows/WSL2 **contract** (scripts) | **PASS** | `backend/tests/test_rel1b_windows_contract.py`, unchanged and green on this SHA in CI |
| 9 | Migration to head | **PASS** (CI, current SHA) | full backend suite green on `7e859f28` (run 38186244075); host-level migration NOT RUN |
| 10 | `/api/ready` / `/api/health` | **PASS** (CI/prior host) | prior accepted host evidence plus CI; not re-run on a clean host |
| 11 | Frontend production build | **PASS** | CI Frontend / Node 22 green on `7e859f28` (run 38186244075) |
| 12 | Browser / persona journey on a production install | **NOT RUN** | no clean production host to serve it |

### Windows + WSL2 supported path (row 7 detail)

Executed from Windows PowerShell against a fresh, separate WSL2 distribution so
the supported path was exercised from a clean runtime:

| Field | Value |
| --- | --- |
| Windows | Windows 11 Pro, build 26200 (10.0.26200) |
| PowerShell | 5.1.26100.9444 |
| WSL | 2.6.3.0, kernel 6.6.87.2, WSLg 1.0.71 |
| Repository | `C:\rel1b4\OpenJM-Enterprise-AI` at `7e859f2801bee1d6b5d52688b633709e5e9ed27d` |
| Target distro | `openjm-rel1b4` (fresh, imported; separate from the user's existing `Ubuntu-24.04` and `Ubuntu`, which were left untouched) |
| Distro runtime | Ubuntu 24.04.5 LTS, WSL2 kernel, no Python, no Node |

Observed behaviour of `scripts/install-wsl2.ps1`:

- distribution detection: PASS (`Detected distribution 'openjm-rel1b4'`);
- Windows to WSL repository mapping: PASS
  (`C:\rel1b4\...` -> `/mnt/c/rel1b4/...`);
- prerequisite check and delegation: PASS (it attempted prerequisites, then
  invoked `bash scripts/install-linux.sh --profile development`);
- the canonical installer ran and failed fast and correctly with
  `ERROR: Python 3.11 is required`;
- the PowerShell bootstrap propagated the failure and exited non-zero.

It could not complete because the fresh runtime had no Python 3.11 and the
bootstrap's own prerequisite step needs `sudo`, which the imported root-only
distribution does not provide. Recorded as **NOT RUN**, not PASS.

### REL1-B4H installer hardening (2026-10-12)

The follow-on hardening above landed in REL1-B4H and was verified on the real
Windows host.

| Item | Result |
| --- | --- |
| RepoPath default resolution (H1) | fixed: body-scope resolution, explicit wins, `$PSCommandPath` then `$MyInvocation`, cwd never used, `ProviderPath` for UNC, precise failure when unresolvable |
| Privilege detection (H2) | fixed: root / sudo-capable / no-sudo classified before any install; root uses apt directly, sudo uses `sudo -n`, no-sudo fails with the required message. **Review correction:** sudo capability now means USABLE non-interactive sudo (`command -v sudo && sudo -n true`); a sudo binary that still demands a password is classified as no usable sudo |
| RepoPath contract (H1) | **Review correction:** the supported source is a Windows drive-letter path mapped to `/mnt/<drive>/...`; a UNC or provider path fails closed with an actionable message instead of being mapped |
| Python 3.11 strategy (H3) | Option A selected: explicit operator prerequisite. The invalid `apt-get install python3.11` path is removed; a pinned third-party source was rejected to avoid widening the release supply chain |
| Node 22 (H4) | preserved as required-and-never-auto-installed; wrong major and absent Node both reported precisely |
| Reporting (H5) | six precise failure categories, each non-zero exit; the old cascading `sudo: command not found` then `Python 3.11 is required` is gone |
| Delegation (H6) | unchanged: `bash scripts/install-linux.sh --profile <profile>` with `-SkipFrontend`; no dependency resolution in the PowerShell layer |

Behavioural evidence (bounded harness, stub `wsl`, no real distribution created):

- previous revision: **7 passed, 10 failed**;
- hardened revision: **17 passed, 0 failed**;
- after the two review corrections: **25 passed, 0 failed**, and **21 passed / 4 failed** against the pre-correction candidate `ded54e8` (three UNC cases plus the unusable-sudo case).

Real-runtime evidence on the disposable distro `openjm-rel1b4` (Ubuntu 24.04.5,
uid 0, no sudo, no Python 3.11):

- hardened: `Repository: C:\rel1b4\OpenJM-Enterprise-AI`, `WSL path:
  /mnt/c/rel1b4/OpenJM-Enterprise-AI`, `user state = root (uid 0)`, then the
  precise Python 3.11 prerequisite failure;
- previous revision on the same runtime: `Installing Linux prerequisites inside
  WSL...`, `bash: line 1: sudo: command not found`, then the misleading
  `Linux installer failed inside WSL (exit 1)`.

Row 7 (Windows/WSL2 runtime install) remains **NOT RUN**. The hardening removes
the reporting and abstraction defects; it does not remove the Python 3.11
prerequisite, which must still be provisioned in the target distribution before
the install can complete. The full development install, backend start,
`/api/health`, `/api/ready` and the migration remain NOT RUN for that reason.

### Follow-on recommendation: bounded REL1-B4 installer hardening

None of the following is fixed, and none may be treated as resolved merely
because `-RepoPath` was supplied or because the test runtime happened to lack
`sudo`. They are open release-tooling concerns for the supported Windows/WSL2
path and are recommended for a separate bounded package BEFORE the next
Windows/WSL2 runtime attempt.

1. **Python 3.11 acquisition on a stock Ubuntu 24.04 (Noble).** The supported
   bootstrap contains `sudo apt-get install ... python3.11 ...`. Ubuntu 24.04's
   default Python line is 3.12, so `python3.11` is not available from the stock
   Noble archive. On the fresh Noble runtime exercised here the install did not
   succeed (`python3.11: command not found` afterwards). The bootstrap therefore
   needs either a pinned, explicitly documented source for Python 3.11 (for
   example a PPA added by the script), a documented prerequisite that the
   operator supplies 3.11, or a supported alternative to acquiring it. Relying on
   `apt-get install python3.11` on a stock fresh Noble environment is not a valid
   bootstrap path as written.
2. **`$PSScriptRoot` empty in the `param()` default.** `install-wsl2.ps1` fails
   with `Join-Path : Cannot bind argument to parameter 'Path' because it is an
   empty string` when PowerShell reports an empty `$PSScriptRoot` at parameter
   binding time. Passing `-RepoPath` explicitly works around it, but the default
   remains broken; a defensive fallback (for example to
   `$MyInvocation.MyCommand.Path`) would make the script robust.
3. **Root-only / no-`sudo` runtime is not detected.** The prerequisite step
   assumes a `sudo`-capable non-root user. A root-only distribution reports
   `sudo: command not found`, after which the canonical installer fails further
   downstream on the missing Python 3.11 rather than surfacing the real cause.
   Detecting the runtime user model (or using a no-`sudo` path when already root)
   would make the bootstrap fail early and clearly.

Recommended bounded follow-on package: **REL1-B4 installer hardening**, covering
the three items above with focused tests on the bootstrap's detection,
prerequisite acquisition and failure reporting, no change to
`install-linux.sh` semantics, and no weakening of the Python 3.11 / Node 22
requirement (DB-GPT pins `aiohttp==3.8.4`, which does not build on 3.12).

## Committed-lock release artifacts (built on the acceptance host)

| Field | Value |
| --- | --- |
| Production wheelhouse | 181 distributions, 3.1 GB, from `backend/requirements.lock` (`--require-hashes --only-binary=:all:`) |
| Application wheel | `openjm_enterprise_ai_backend-0.2.0-py3-none-any.whl`, sha256 `cde425359f38295dac899d358e57ea0b823387135d228179fbeacb9803a4f081` |
| Bundle | `openjm-rel1-0.2.0/`, 884 payload files, 3.1 GB |
| npm offline cache | 26 MB, deterministic digest `ea249931ecd51bf628e075f0c6062e0996c3ba4f405c99c99d5bbb527703fc2e` |
| `RELEASE-MANIFEST.json` sha256 | `f3c9926c79c4b7c57929a62c1f57b66cad8d070b26578b71fc425a0d47739236` |
| Release archive | `openjm-rel1-0.2.0.tar.gz` |
| Archive bytes | `3243220300` |
| Archive sha256 | `00fa8adbd6409b5fa840d4196989fd6c9e14469f159ea157df8da67af27718ba` (identical across two builds) |

## Acceptance table

| # | Item | Status | Evidence |
| --- | --- | --- | --- |
| 1 | Clean Linux **development** install (`install-linux.sh --profile development`) | NOT RUN | Not executed on a separate clean host; development retains the editable install. |
| 2 | Clean Linux **production** install (`install-linux.sh --profile production`) | PARTIAL (offline dependency + wheel install PASS; full PostgreSQL production profile NOT RUN) | The supported installer ran offline from the bundle: dependency install from the bundled wheelhouse (`--no-index --require-hashes`) and application-wheel install (`--no-deps`) both succeeded (see item 3). It then stopped at the fail-closed production preflight (`PermissionError` writing `/var/lib/openjm`; the production profile also requires a PostgreSQL-backed config). The host is non-root with no PostgreSQL, so the full PG production profile is not runnable here. |
| 3 | Offline committed-lock install (full bundle, committed lock) | PASS | Installer offline step: `app` imported from `site-packages` (`.../backend/.venv/lib/python3.11/site-packages/app/__init__.py`), `pip show openjm-enterprise-ai-backend` = 0.2.0, and no `pytest`/`pgserver`/`pytest-asyncio` present (production excludes dev extras). No editable install, no build backend, no PYTHONPATH, no package index. |
| 4 | Trusted-digest verification (out-of-band manifest sha) | PASS | `release-integrity.py verify <bundle> --expected-manifest-sha256 f3c9926c...` -> `verified release payload: 884 files` (fresh environment). Code negative case: `test_wrong_trusted_manifest_sha256_is_rejected`. |
| 5 | Tamper-negative coverage | PASS | `backend/tests/test_rel1b_tamper_negative.py` (all cases). Observed fail-closed in practice: a mutated bundle was refused at archive time (`bundle verification failed`; unexpected payload files). |
| 6 | Deterministic release archive | PASS | Two independent builds of the real 3.1 GB payload: identical bytes `3243220300` and identical sha256 `00fa8adb...`. Code test: `test_rel1b_archive.py`. |
| 7 | Windows/WSL2 **runtime** install | NOT RUN | No real Windows host exercised; `install-wsl2.ps1` not run. |
| 8 | Windows/WSL2 **contract** (scripts only) | PASS | `backend/tests/test_rel1b_windows_contract.py`. |
| 9 | Migration (`openjm_ops.py upgrade`) | PASS | Installer-built venv (wheel install, no PYTHONPATH): `0020_bv6_report_curation -> 0021_support_content_scope`; head `0021_support_content_scope`; `RESULT: OK`; version `0.2.0`. |
| 10 | `/api/ready` / `/api/health` | PASS | Installer-built venv: `/api/ready` -> 200 `{"ready":true,"status":"ready"}`; `/api/health` -> 200; uvicorn `Application startup complete` (self-migrating wheel install). |
| 11 | Frontend production build | PASS | Registry-blocked `npm ci` from the release cache (138 packages) + `npm run build`: `tsc --noEmit && vite build` OK, `dist/index.html` emitted. |
| 12 | Browser / persona journey | NOT RUN | No browser/auth journey executed against the deployed build. |

## Production application wheel

The backend build backend (Hatchling) is not in the production wheelhouse or the
lock, so the installer's editable app step cannot run on a registry-blocked host.
Rather than add Hatchling to the runtime lock, the bundle ships a pre-built
application wheel:

- Built by `scripts/build-rel1-bundle.sh` (controlled build env, network allowed)
  via `python -m pip wheel --no-deps --wheel-dir "$BUNDLE/python/app" "$ROOT/backend"`;
  asserts exactly one `openjm_enterprise_ai_backend-<PRODUCT_VERSION>-*.whl`.
- Shipped at `python/app/`, covered by `RELEASE-MANIFEST.json` (generated last).
- Installed by `install-linux.sh --profile production`: the lock is installed
  from the bundled wheelhouse with `--no-index --find-links python/wheelhouse
  --require-hashes`, then the single bundled wheel with `--no-deps` (never
  editable, never from an index, never with build isolation). A missing or
  ambiguous wheel is a hard failure. Development keeps the editable install.
- **Self-migrating:** the wheel force-includes `migrations/` and `alembic.ini`
  (Hatchling `force-include`) because `app/migrations_runner.py` resolves the
  Alembic script directory relative to the installed package root. Without this
  the wheel install could not migrate itself; with it, startup migrations and
  `/api/ready` succeed (items 9 and 10). Regression test:
  `backend/tests/test_rel1b_app_wheel_packaging.py`.

The production runtime lock is unchanged: no new runtime dependency, no Hatchling.
Coverage lives in `backend/tests/test_rel1b_app_wheel.py` and
`test_rel1b_app_wheel_packaging.py`. The prior limitation ("installer editable
install cannot complete offline; backend run via PYTHONPATH") is **resolved for
production**.

## Blocked / decision required

- **Full PostgreSQL production profile** (root-owned paths + PostgreSQL metadata
  DB) is NOT RUN here: the acceptance host is non-root and has no PostgreSQL. The
  installer's fail-closed preflight behaved correctly. Running the full
  production profile on a prepared host remains outstanding.

## Code-tested coverage detail

- **Tamper-negative (B4-D)** — `backend/tests/test_rel1b_tamper_negative.py`:
  modified/deleted/unexpected payload; modified manifest; wrong trusted digest;
  symlink; path-traversal/absolute/reserved manifest entries.
- **Deterministic archive (B4-F)** — `scripts/build-rel1-archive.sh`: verifies
  before writing; `--sort=name --mtime=@0 --numeric-owner --owner=0 --group=0
  --format=gnu`, `gzip -n`; relative members; refuses in-repo output.
- **Windows/WSL2 contract (B4-E)** — `backend/tests/test_rel1b_windows_contract.py`:
  profile + `--skip-frontend` forwarded to the canonical installer, no divergent
  dependency path, `[dev]` absent, Python 3.11 / Node 22 enforced.

## Not run

Clean Linux **development** installer on a separate host; the full PostgreSQL
**production** profile; real Windows/WSL2 runtime; browser/persona journey. These
require environments not available here and are not claimed.


### Windows/WSL2 runtime qualification evidence (2026-10-12)

| Field | Value |
| --- | --- |
| Baseline SHA | `c3937c88150c595d7a9a907688179880a4a096dc` |
| Windows | Windows 11 Pro, build 26200 (10.0.26200) |
| PowerShell | 5.1.26100.9444 |
| WSL | 2.6.3.0, kernel 6.6.87.2-microsoft-standard-WSL2 |
| Distro | `openjm-rel1b4`, Ubuntu 24.04.5 LTS, root / uid 0 (the user's `Ubuntu` and `Ubuntu-24.04` were untouched) |
| Python | 3.11.9, built from source into `/opt/openjm/python-3.11`, exposed as `/usr/local/bin/python3.11`; system `python3` remains 3.12.3 |
| Node | v22.14.0 from the official archive into `/opt/openjm/node-22`, `/usr/local/bin/node`, npm 10.9.2 (Linux paths win over the inherited Windows npm) |
| Bootstrap command | `powershell -NoProfile -ExecutionPolicy Bypass -File C:\rel1b4\OpenJM-Enterprise-AI\scripts\install-wsl2.ps1 -RepoPath C:\rel1b4\OpenJM-Enterprise-AI -Distro openjm-rel1b4 -Profile development` |
| Bootstrap exit | **0** (completion banner printed) |
| RepoPath mapping | `C:\rel1b4\OpenJM-Enterprise-AI` -> `/mnt/c/rel1b4/OpenJM-Enterprise-AI` |
| Installer stages | deps from the committed development lock; editable application install; preflight; migration; `npm ci` (137 packages); `vite build`; REL1-A verifier |
| Verifier | `REL1-A VERIFY OK: version=0.2.0 profile=development frontend=present` (exit 0) |
| Migration | current `0021_support_content_scope` == head `0021_support_content_scope` (development SQLite `./data/openjm.db`) |
| Backend | `env -u PYTHONPATH -u PYTHONHOME .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 18160`, pid 14521, venv python `/opt/openjm/python-3.11/bin/python3.11`, `Application startup complete` |
| `/api/health` | 200 `{"status":"ok","product":"OpenJM Enterprise AI","version":"0.2.0","knowledge_engine":"DB-GPT","model":"gemma-4-12b-local","profile":"development"}` |
| `/api/ready` | 200 `{"ready":true,"status":"ready"}` |
| Readiness detail | database ok, migrations ok (no drift), storage ok, knowledge ok, scheduler ok, connectors ok, notifications ok. `model_provider` reports `error`/`ConnectTimeout` for the development-profile default local model, which is **not** a readiness gate (readiness gates only on database, migrations and storage). No credential was exposed, no model was substituted, and Rahkia was not involved. |
| Frontend | `frontend/dist` emitted (index.html + assets); no browser/persona journey claimed here (separate #79 row) |

The clean Linux production/PostgreSQL host and the production-installed browser/persona
journey remain **NOT RUN** and OPEN under Issue #79. Clean Linux development (above) and
the Windows/WSL2 supported runtime are **PASS**.


### Clean Linux development host qualification evidence (2026-10-12)

| Field | Value |
| --- | --- |
| Platform | Docker Agentic Platform **Cloud** sandbox (not local Docker, not Compose, not Offload) |
| Sandbox | `openjm-rel1-dev` / `sbx_001m4qacck9mzv3jptmrzqm00mz`, medium shape (4 vCPU / 8 GiB), TTL 4h, `linux/amd64` |
| OS / kernel / arch | Ubuntu 26.04.1 LTS (Resolute Raccoon) / 6.12.103 / x86_64 |
| User | `agent`, uid 1000, groups `agent sudo docker`; passwordless `sudo -n` usable |
| Free disk | 29.4 GB (well above the ~12-15 GB minimum) |
| Clean-host proof | no OpenJM checkout, `.venv`, `node_modules` or database anywhere; bare interpreter (pip list = 6) |
| Clone | `git clone https://github.com/sjevans1/OpenJM-Enterprise-AI.git` inside the sandbox, checked out `2456f92a74d091ed425e24256f9910152671b4ca` (`rev-parse` matches, tree clean) |
| Python | 3.11.9 from official python.org source; expected = observed = `e7de3240a8bc2b1e1ba5c81bf943f06861ff494b69fda990ce2722a504c6153d`, corroborated by an unrelated mirror, GPG `Good signature` (CPython release key); isolated at `/opt/openjm/python-3.11`, exposed as `/usr/local/bin/python3.11`, system `python3` left at 3.14.4; stdlib verified (ssl, hashlib, sqlite3, ctypes, bz2, lzma, venv) and pip 24.0 |
| Node / npm | distribution-provided `/usr/bin/node` v22.22.1, `/usr/bin/npm` 9.2.0 (Node 22 already present, used as-is) |
| Installer | `bash scripts/install-linux.sh --profile development` (unmodified) — exit **0** |
| Installer stages | development lock installed from the committed lock; editable development application install; migration; `npm ci` (138 packages); `vite build`; `frontend built into frontend/dist` |
| Verifier | `REL1-A VERIFY OK: version=0.2.0 profile=development frontend=present`; `REL1-A post-install verification passed` |
| Migration | repository head `0021_support_content_scope` == current `0021_support_content_scope` (development SQLite `./data/openjm.db`) — host evidence |
| Backend | `env -u PYTHONPATH -u PYTHONHOME .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 18077`, pid 16392, venv python `/opt/openjm/python-3.11/bin/python3.11`, `Application startup complete` |
| `/api/health` | 200 `{"status":"ok","product":"OpenJM Enterprise AI","version":"0.2.0","knowledge_engine":"DB-GPT","model":"gemma-4-12b-local","profile":"development"}` |
| `/api/ready` | 200 `{"ready":true,"status":"ready"}`; components database/migrations/storage/knowledge/scheduler/connectors all ok, `storage.free_mb` 19708 |

`model_provider` reported `error`/`ConnectError` for the development-profile default local model
(`gemma-4-12b-local`). That component is explicitly not a readiness gate, no provider was
started or substituted, no credential was exposed, and Rahkia was not involved.

### Docker Cloud Compose feasibility (NOTE only, not a row change)

A bounded toy probe was run in the same sandbox, outside the repository, using only disposable
toy services (PostgreSQL 16-alpine plus a dependent service declaring
`depends_on: db: condition: service_healthy` and a `pg_isready` healthcheck that mirrors the
openJM production Compose pattern).

Result: **COMPOSE VIABLE.** Private in-sandbox Docker Engine 29.8.1 (overlayfs storage,
cgroup v2) and Compose v5.5.1. `docker compose up -d` returned 0; the `db` service went
`Waiting -> Healthy`; the dependent service was released and its own logs prove real
connectivity (`DEPENDENT_STARTED`, `DEPENDENT_READY_AFTER_1_TRIES`, query result `42`,
`DEPENDENT_QUERY_OK`); a third container confirmed `accepting connections`; `docker compose down -v`
returned 0. The documented Docker Cloud healthcheck/`service_healthy` limitation did **not**
reproduce on this engine and Compose version, and the proof came from service logs and an actual
dependent query rather than `docker inspect` alone.

This does **not** change production status: the clean Linux production/PostgreSQL host row
remains NOT RUN, and production was not executed. It establishes that Docker Cloud remains a
production-host candidate. Production-host eligibility is therefore **INCONCLUSIVE** pending the
real production run, not FAIL.

### PRODUCTION TOOLING FINDING: RESOLVED (2026-10-12)

`deploy/compose/Dockerfile.proxy` previously built the shipped frontend on
`node:20-bookworm-slim`, while Issue #79 requires production to remain Node 22 and the accepted
installer/toolchain contract enforces Node 22. The production proxy image is the image that
actually ships the SPA, so production would have served a frontend built on an unsupported
toolchain even though every other gate reported Node 22.

**Corrected and proven**, on the branch `rel1-node22-proxy-correction` from
`main@390b3454f46c933cbcf34daa79e91476cadec165`:

| Step | Evidence |
| --- | --- |
| RED | New contract test `test_production_proxy_frontend_builder_uses_node_22` failed on the accepted baseline: `AssertionError: frontend builder must be Node 22, found node:20-bookworm-slim` |
| Change | `FROM node:20-bookworm-slim AS frontend` -> `FROM node:22-bookworm-slim AS frontend` (Caddy runtime stage untouched) |
| GREEN | Focused test passes; the whole `test_rel1a_reproducible_deployment.py` contract file passes 6/6 |
| Image build | `docker build -f deploy/compose/Dockerfile.proxy -t openjm-rel1-proxy-node22:local .` from a clean copy of the repository root (excluding host `node_modules`/`dist`/venv, since the Dockerfile does `COPY frontend/` and there is no `.dockerignore`): **exit 0**; `22-bookworm-slim: Pulling from library/node`; `npm ci` -> `added 138 packages`; `npm run build` -> `tsc --noEmit && vite build` -> `built in 3.07s` with `dist/index.html` and `dist/assets/` emitted; `COPY --from=frontend /app/dist /srv/openjm/frontend`; image `openjm-rel1-proxy-node22:local` id `6b920f7c9cb3`, 93.3 MB |
| Runtime smoke | Disposable container on `127.0.0.1:18091` with `OPENJM_SITE_ADDRESS=:80`: Caddy `server running` / `serving initial configuration`; `GET /` -> **HTTP 200**, 455 bytes returning the built SPA (`<title>OpenJM Enterprise AI</title>`, `/assets/index-B7FImGXs.js`); referenced asset -> **HTTP 200**, 278901 bytes; container stopped and removed afterward |

This resolves the tooling finding. It does **not** change any #79 host row: the clean Linux
production/PostgreSQL host remains **NOT RUN** and the production-installed browser/persona
journey remains **NOT RUN**. The production-host qualification itself is still outstanding.


### PRODUCTION COMPOSE BACKEND: release-contract alignment (2026-10-12)

Finding. `deploy/compose/Dockerfile` performed
`pip install --no-cache-dir --prefer-binary -e "/opt/openjm/backend"`, so the
production Compose backend ran an **editable** application install whose runtime
dependency graph was resolved from open-ended project metadata rather than
`backend/requirements.lock`. A Compose deployment could therefore run a different
dependency graph from the artifact under qualification, which made a production
Compose/browser run indefensible as release evidence. No #79 host row was affected
by the finding itself.

Correction (branch `rel1-compose-backend-release-alignment`, from
`main@bc0c4587dc40be8763e8392b8817ac65bb218186`). The image is now two-stage:

| Stage | Contents |
| --- | --- |
| `builder` | `python:3.11-slim` + build-only tooling; produces the application wheel with `pip wheel --no-deps` from the exact backend source |
| `runtime` | `python:3.11-slim` + `curl` only; creates `/opt/openjm/backend/.venv`; installs the committed `backend/requirements.lock` with `--require-hashes`; copies the wheel from the build stage and installs it `--no-deps`, non-editable |

`entrypoint.sh`, the preflight-then-uvicorn CMD, the unprivileged `openjm` user,
the 8000 port, `PYTHONPATH=` and the production volume paths are unchanged. No
compiler ships in the runtime stage, which is consistent with the lock being
binary-only (the REL1 lock-drift gate builds a binary-only wheelhouse from it).

| Step | Evidence |
| --- | --- |
| RED | New contract test `test_production_compose_backend_obeys_locked_release_contract` failed on the accepted baseline at the first lock assertion: `assert 'requirements.lock' in dockerfile` |
| GREEN | Focused test passes; `backend/tests/test_rel1a_reproducible_deployment.py` passes 7/7 |
| Image build | `docker build -f deploy/compose/Dockerfile -t openjm-rel1-backend-locked:local .` from a clean copy of the repository root (excluding host `.venv`, `data`, `node_modules`, `dist`, `.git`, since the repository has no `.dockerignore`): **exit 0**; `Step 1/21 FROM python:3.11-slim AS builder`, `Step 7/21 FROM python:3.11-slim AS runtime`; the builder produced `Successfully built openjm-enterprise-ai-backend`; the runtime stage installed the lock under hash enforcement; image id `1fb035ddf55c`, 20.5 GB |
| Runtime image inspection | `Python 3.11.17`; `app.__file__` = `/opt/openjm/backend/.venv/lib/python3.11/site-packages/app/__init__.py` (installed, **not** an editable checkout); package `openjm-enterprise-ai-backend` version `0.2.0`; **no** `__editable__*`, `.egg-link` or `*.pth` artefacts; `direct_url.json` records `file:///opt/openjm/wheels/openjm_enterprise_ai_backend-0.2.0-py3-none-any.whl` with `archive_info.hash sha256=cc15c086...` (a wheel install, not `dir_info.editable`); **no application source directory** in the runtime image (only `requirements.lock` remains under `/opt/openjm/backend`); `gcc`/`cc`/`make` absent; `PYTHONPATH` unset; `pip check` → `No broken requirements found` |
| Dev extras | `pytest`, `pytest-asyncio` and `pgserver` are **absent**; `pypdf 6.20.0` is **present** because it is genuinely part of `backend/requirements.lock` (taken from the lock itself rather than assumed) |
| Compose smoke | `docker compose -f deploy/compose/docker-compose.prod.yml up -d db backend` with disposable ephemeral secrets and a temporary override pinning the already-inspected image tag. `db` (`postgres:16`, PostgreSQL 16.15) reached healthy and `pg_isready` succeeded. The backend ran the **production preflight** and then migrated PostgreSQL from `0013` through to `0021_support_content_scope`, logging `Application startup complete.` and `Uvicorn running on http://0.0.0.0:8000`. `/api/health` → **200** `{"status":"ok",...,"model":"openjm-private-8b","profile":"production"}`; `/api/ready` → **200** `{"ready":true,"status":"ready"}`; `alembic_version` in PostgreSQL = `0021_support_content_scope` == repository head; `/var/lib/openjm`, `/etc/openjm` and `/var/backups/openjm` were mounted and writable by `openjm` (uid 999); `docker compose down -v` removed every container, volume and network, the developer `.env` was restored and the disposable secret material deleted |

Ergonomics finding discovered during the smoke (recorded, not a defect in this
correction). The backend runs as the unprivileged `openjm` user (uid 999), and
Docker Compose mounts the database password file preserving the host file's
ownership and mode. A secret created as the developer account with mode `600`
(uid 1000) is therefore **unreadable** by the container user, and the entrypoint
fails with `cat: /run/secrets/openjm_db_password: Permission denied` before the
preflight ever runs. The smoke proceeded by relaxing the disposable secret to
`644`. The compose header comment does not state a required ownership or mode for
this file, so a real deployment can hit the same wall. Recommended as a separate
bounded follow-on: document the required secret ownership/mode (or have the
entrypoint report this specific cause clearly). Not fixed here, and no product or
release-tooling code was changed for it.

Boundary: this establishes **PRODUCTION COMPOSE RELEASE PATH TOOLING: PASS**. It
does **not** change any #79 host row. The clean Linux production/PostgreSQL host
remains **NOT RUN**, and the production-installed browser/persona journey remains
**NOT RUN**.


### PRODUCTION COMPOSE DATABASE SECRET: file-backed delivery replaced (2026-10-12)

Failure (reproduced while smoke-testing the previous correction). The Compose
database password was a file-backed secret:
`secrets: openjm_db_password: { file: ./secrets/openjm_db_password.txt }`. Compose
bind-mounts `file:` secrets, so the host file's owner and mode travel into the
container unchanged, and the secret is **not** remapped to a service `uid`/`gid`/
`mode`. The backend runs unprivileged as `openjm` (**uid 999, gid 999**) while the
operator creates the file as their own account (**uid 1000**); a restrictive
`0600` host file is therefore unreadable and the entrypoint failed with
`cat: /run/secrets/openjm_db_password: Permission denied` before the preflight
ever ran. The only escapes were running the backend as root or loosening the
secret to `0644` on disk, both rejected for production.

The PostgreSQL container was measured too rather than assumed: its server process
runs as **uid 999, gid 999** (`postgres`). The coincidence that both images use
999 is not relied upon anywhere in the fix.

Correction (branch `rel1-compose-secret-hardening`, from
`main@e46c9e078008d597c3649e288d389ec2cd5cbda9`). The top-level Compose secret is
now **environment-sourced**:

```
secrets:
  openjm_db_password:
    environment: OPENJM_DB_PASSWORD_SECRET
```

Compose consumes `OPENJM_DB_PASSWORD_SECRET` from the process environment it was
started with and materialises `/run/secrets/openjm_db_password` itself, so no
operator-owned file with a mismatched uid/mode exists at all. The backend keeps the
accepted file contract (`OPENJM_DB_PASSWORD_FILE=/run/secrets/openjm_db_password`)
and `entrypoint.sh` still assembles the DSN from it, unchanged. The backend was
**not** changed: it stays unprivileged. The source variable belongs to the Compose
invocation only; it is not added to any service `environment:` and not to the
backend `env_file:` template, and it is not a file, so no `0644` on-disk secret is
ever required. `file:` was rejected precisely because uid/gid/mode remapping is not
honoured for that source type.

| Step | Evidence |
| --- | --- |
| RED | New contract test `test_production_compose_db_secret_delivery_is_secure` (parses the Compose YAML) failed on the baseline at `assert "file" not in secret` with `assert 'file' not in {'file': './secrets/openjm_db_password.txt'}` |
| GREEN | Focused test passes; `backend/tests/test_rel1a_reproducible_deployment.py` passes 8/8 |
| Restrictive smoke (positive) | `OPENJM_DB_PASSWORD_SECRET` generated with `openssl rand -hex 24` into the Compose process environment only, never printed and never written to a file; `deploy/compose/secrets` did **not** exist. `docker compose -f deploy/compose/docker-compose.prod.yml up -d db backend` → `UP_RC=0`; `db` (`postgres:16`, PostgreSQL 16.15) healthy in 5s and `pg_isready` accepting connections; backend reachable in 9s running as **`openjm` uid 999 gid 999**; `/run/secrets/openjm_db_password` present, owner `root:root`, mode `444`, readable by that unprivileged user, contents never read or printed; entrypoint assembled `OPENJM_DATABASE_URL` from the file |
| Env leak check | Backend process environment inspected by **key name only**: `OPENJM_DB_PASSWORD_SECRET` count **0** (the secret source is not leaked into the application). `OPENJM_DATABASE_URL` and `OPENJM_DB_PASSWORD_FILE` are present as the accepted runtime contract. `OPENJM_DB_PASSWORD` is present only as the template's stock `REPLACE_ME` placeholder from `env_file` and is **not** authoritative: the entrypoint overrides `OPENJM_DATABASE_URL` from the mounted secret, so the real password is never duplicated into the application environment |
| Single source | 21 alembic upgrade steps ran and reached head; PostgreSQL `alembic_version` = `0021_support_content_scope` == repository head; `/api/health` → **200** `{"status":"ok",...,"model":"openjm-private-8b","profile":"production"}`; `/api/ready` → **200** `{"ready":true,"status":"ready"}`. Authentication succeeded through the shared secret mechanism, proving db and backend use the same value without either value being printed |
| Negative case | With `OPENJM_DB_PASSWORD_SECRET` unset, Compose refuses before anything runs: `environment variable "OPENJM_DB_PASSWORD_SECRET" required by secret "compose_openjm_db_password" is not set`; backend health `HTTP 000`; it did **not** fall back to the `.env` placeholder. Fail-closed confirmed |
| Cleanup | `docker compose down -v` (both cases); the developer `.env` restored; the temporary override file removed; `OPENJM_DB_PASSWORD_SECRET` unset from the shell; 0 leftover containers, 0 volumes, and no `deploy/compose/secrets` directory in the working tree. No secret value was printed at any point |

Boundary: this is production Compose **tooling** qualification. It does **not**
mark the clean Linux production/PostgreSQL host PASS, and the production-installed
browser/persona journey remains NOT RUN.
