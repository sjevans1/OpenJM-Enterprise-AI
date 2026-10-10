# REL1-B4 host-acceptance evidence

REL1-B4 closes the REL1 offline-release train: a deterministic release archive
(B4-F), tamper-negative and path-traversal coverage (B4-D), machine-testable
Windows/WSL2 contract checks (B4-E), and this host-acceptance record (B4-H).

This is the canonical evidence table. Rows marked `PASS` were executed and the
result recorded; rows marked `NOT RUN` were not executed in this environment and
are **not claimed**. Where a stricter equivalent was executed on the acceptance
host it is stated explicitly under the row.

## Environment of record

| Field | Value |
| --- | --- |
| Acceptance host | WSL2 (Ubuntu guest) on the maintainer workstation |
| OS | Linux (WSL2 guest; `uname` Microsoft-standard kernel) |
| Python | 3.11.15 |
| Node / npm | 22.23.2 / 10.9.8 |
| Source Git SHA | `f1f06b5825f224e778dbb8b10cba19f19561f250` |
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

## Committed-lock release artifacts (built on the acceptance host)

| Field | Value |
| --- | --- |
| Production wheelhouse | 181 distributions, 3.1 GB, built from `backend/requirements.lock` (`scripts/build-python-wheelhouse.sh`, `--require-hashes --only-binary=:all:`) |
| Bundle | `openjm-rel1-0.2.0/`, 876 payload files, 3.1 GB |
| npm offline cache | 26 MB, deterministic digest `ea249931ecd51bf628e075f0c6062e0996c3ba4f405c99c99d5bbb527703fc2e` |
| `RELEASE-MANIFEST.json` sha256 | `d9f56c72ecd96898237e702157f7c8370904b32402023f9bec8ce00d84f9ead6` (reproduced identically on a clean rebuild) |
| Release archive | `openjm-rel1-0.2.0.tar.gz` |
| Archive bytes | `3242771473` |
| Archive sha256 | `033aeada4c392b87b39307b7c997ba48dd9ea8d7364bb8527684e3f84ef98fe4` |

## Acceptance table

| # | Item | Status | Evidence |
| --- | --- | --- | --- |
| 1 | Clean Linux **development** install (`scripts/install-linux.sh --profile development`) | NOT RUN | Installer not executed here; no separate clean host available. |
| 2 | Clean Linux **production** install (`scripts/install-linux.sh --profile production`) | NOT RUN / PARTIAL | Installer not executed here; the equivalent committed-lock offline install (item 3) was executed manually on this host. |
| 3 | Offline committed-lock install (full bundle, committed lock) | PASS | `scripts/rel1-offline-acceptance.sh` on the assembled bundle: `python offline install: OK` (181 wheels, `--no-index --find-links wheelhouse --require-hashes`), registry-blocked `npm ci` (138 packages) + production build OK, `REL1 offline acceptance passed (registry-blocked)`. No registry fallback. |
| 4 | Trusted-digest verification (out-of-band manifest sha) | PASS | `scripts/release-integrity.py verify <bundle> --expected-manifest-sha256 d9f56c72...` -> `verified release payload: 876 files`. Code negative case: `test_wrong_trusted_manifest_sha256_is_rejected`. |
| 5 | Tamper-negative coverage | PASS | `backend/tests/test_rel1b_tamper_negative.py` (all cases). Additionally observed fail-closed in practice: after a readiness run mutated the bundle, archiving refused with `bundle verification failed` (unexpected payload files: `__pycache__/*.pyc`, `app/data/openjm.db`, `app/data/credentials.key`, npm cache logs). |
| 6 | Deterministic release archive | PASS | Two independent builds of the real 3.1 GB payload produced identical bytes `3242771473` and identical sha256 `033aeada...`. Code test: `test_rel1b_archive.py`. |
| 7 | Windows/WSL2 **runtime** install | NOT RUN | No real Windows host exercised; `scripts/install-wsl2.ps1` not run. |
| 8 | Windows/WSL2 **contract** (scripts only) | PASS | `backend/tests/test_rel1b_windows_contract.py`. |
| 9 | Migration (`openjm_ops.py upgrade`) | PASS | Offline host run: `Running upgrade 0020_bv6_report_curation -> 0021_support_content_scope`; `schema head: 0021_support_content_scope`; `RESULT: OK`; application version `0.2.0`. |
| 10 | `/api/ready` | PASS | Offline host run after migration: `/api/ready` -> 200 `{"ready":true,"status":"ready"}`; `/api/health` -> 200; uvicorn `Application startup complete`. |
| 11 | Frontend production build | PASS | Registry-blocked `npm ci --offline` + `npm run build` from the release npm cache: `tsc --noEmit && vite build` OK, `dist/index.html` emitted. |
| 12 | Browser / persona journey | NOT RUN | No browser/auth journey executed against the deployed build. |

## Blocked / decision required

- **Offline editable app install (`pip install --no-deps -e .`).** The application
  build backend `hatchling` is not present in the production wheelhouse or in
  `backend/requirements.lock`, so the installer's editable app step cannot build
  the application in a registry-blocked environment. For this acceptance the
  backend was run from the bundle source via `PYTHONPATH` instead. This is a
  release-tooling gap, not a security or product regression. Options for human
  decision: vendor the build backend into the offline payload, ship a pre-built
  application wheel, or drop the editable install from the offline path and run
  from source. Installer semantics were not changed.

## Code-tested coverage detail

### Tamper-negative (B4-D) — `backend/tests/test_rel1b_tamper_negative.py`

Verification fails closed (raises before any install/execute step) for:
modified payload file; deleted payload file; unexpected extra payload file;
modified manifest; wrong externally trusted manifest sha256; symlink payload;
path-traversal manifest entry (`..`); absolute-path manifest entry; reserved-file
manifest entry. Each case rebuilds its own bundle; a clean bundle is proven to
verify, so the rejections are real.

### Deterministic archive (B4-F) — `scripts/build-rel1-archive.sh`

Verifies the bundle (optionally against an external trusted digest) before
writing anything; refuses unverified payloads. Deterministic flags:
`--sort=name`, `--mtime=@0`, `--numeric-owner --owner=0 --group=0`,
`--format=gnu`, `gzip -n`; members relative to `openjm-rel1-<version>/`; refuses
in-repo output; never committed.

### Windows/WSL2 contract (B4-E) — `backend/tests/test_rel1b_windows_contract.py`

`install-wsl2.ps1` forwards `--profile $Profile` (explicit default, never
silently dropped) and `--skip-frontend`, delegates to `scripts/install-linux.sh`
(no divergent dependency path), and neither it nor `install-linux.sh` carries
`[dev]`; Python 3.11 and Node 22 are enforced in the canonical installer.

## Not run

Separate clean Linux host for B4-A/B installer runs; real Windows/WSL2 runtime;
browser/persona journey. These require environments not available here and are
not claimed. The offline equivalent executed on this host is recorded under items
3, 4, 9, 10 and 11.
