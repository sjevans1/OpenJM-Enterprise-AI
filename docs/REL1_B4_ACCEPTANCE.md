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
| 1 | Clean Linux **development** install | **NOT RUN** | no clean Linux host (inventory A) |
| 2 | Clean Linux **production** install (PostgreSQL + production paths) | **NOT RUN** | no clean Linux host, no root, no PostgreSQL (inventory B) |
| 3 | Offline committed-lock install of the **current** artifact | **PASS** (subset) | user-space venv: `pip install --no-index --find-links python/wheelhouse --require-hashes -r python/requirements.lock` then `pip install --no-deps python/app/*.whl`; `app` imported from `site-packages`, `pip show` 0.2.0, dev extras present 0. Not a clean-host row. |
| 4 | Trusted-digest verification | **PASS** | `release-integrity.py verify <bundle> --expected-manifest-sha256 fb97c0bf...` -> `verified release payload: 916 files` |
| 5 | Tamper-negative (bounded, practical) | **PASS** | on a disposable hardlink copy, mutating one payload (`app/README.md`) was refused (`ERROR: size mismatch`); the canonical bundle re-verified clean at 916 files |
| 6 | Deterministic release archive | **PARTIAL** | the current artifact was archived once (`ff707eb5...`, `3243305430` bytes). A byte-identical second build was not re-run; the deterministic property is a property of `build-rel1-archive.sh` and the previous two-build equality belongs to the `44ceb0c7` artifact. |
| 7 | Windows/WSL2 **runtime** install | **NOT RUN** | supported path executed from Windows PowerShell (see below) but the clean runtime could not complete the install |
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

Two observations to feed back to the release contract:

- `install-wsl2.ps1` fails at its `param()` default when PowerShell reports an
  empty `$PSScriptRoot` (`Join-Path : Cannot bind argument to parameter 'Path'
  because it is an empty string`). Passing `-RepoPath` explicitly works. A
  defensive default (for example falling back to `$MyInvocation.MyCommand.Path`)
  would make the script robust.
- The prerequisite step assumes a `sudo`-capable non-root user; a root-only
  distribution is reported as `sudo: command not found` and then the installer
  fails on the missing Python 3.11. Both are environment/host ergonomics, not
  product-core defects.

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
