# REL1-B4 host-acceptance evidence

REL1-B4 closes the REL1 offline-release train: a deterministic release archive
(B4-F), tamper-negative and path-traversal coverage (B4-D), machine-testable
Windows/WSL2 contract checks (B4-E), and this host-acceptance record (B4-H).

This file is the canonical evidence table. **Only rows proven by committed code
tests are filled in here.** Every host/runtime item that must be executed on a
real clean Linux/WSL2 host is marked `NOT RUN` and is completed by the lead on
the frozen commit.

## Reference identity

| Field | Value |
| --- | --- |
| Product version | `0.2.0` (`backend/app/version.py::PRODUCT_VERSION`) |
| Release train | `REL1` (`backend/app/version.py::RELEASE_TRAIN`) |
| Accepted production lock sha256 | `90afebf3c719b1db838cbf77a3799477c8ac312d16817aca7fab670b34fde72b` (`backend/requirements.lock`) |
| Accepted development lock sha256 | `fa23810518e9a3646c570f1c54a94e6dadecfef49d0b82f0c4ed7279d40f7bd0` (`backend/requirements-dev.lock`) |
| Backend runtime | Python 3.11 |
| Frontend build runtime | Node.js 22 |
| Production install | excludes Python `[dev]` extras |

## Host-acceptance evidence table

| # | Item | Status | Evidence / command | Notes |
| --- | --- | --- | --- | --- |
| 1 | Clean Linux **development** install | NOT RUN | `scripts/install-linux.sh --profile development` on a clean Linux host | Lead fills host result |
| 2 | Clean Linux **production** install | NOT RUN | `scripts/install-linux.sh --profile production` on a clean Linux host | Lead fills host result |
| 3 | Offline committed-lock install (full bundle, committed lock) | NOT RUN | `scripts/rel1-offline-acceptance.sh <bundle_dir>` on a host | Lead fills host result |
| 4 | Trusted-digest verification | PARTIAL (code) / NOT RUN (host) | `backend/tests/test_rel1b_tamper_negative.py::test_wrong_trusted_manifest_sha256_is_rejected`; host: `scripts/release-integrity.py verify <bundle> --expected-manifest-sha256 <out-of-band sha>` | Mechanism proven in code; host run pending |
| 5 | Tamper-negative coverage | PASS (code) | `backend/tests/test_rel1b_tamper_negative.py` (all cases) | See coverage list below |
| 6 | Deterministic release archive | PASS (code) | `backend/tests/test_rel1b_archive.py::test_same_bundle_built_twice_yields_byte_identical_archive` | Two builds, same sha256 |
| 7 | Windows/WSL2 **runtime** install | NOT RUN | `scripts/install-wsl2.ps1` on a real Windows host | Static contract proven by item 8; runtime pending |
| 8 | Windows/WSL2 **contract** (scripts only) | PASS (code) | `backend/tests/test_rel1b_windows_contract.py` | See assertions below |
| 9 | Migration (`openjm_ops.py upgrade`) | NOT RUN | `scripts/openjm_ops.py upgrade` on installed host | Lead fills host result |
| 10 | `/api/ready` | NOT RUN | `curl -s http://127.0.0.1:8000/api/ready` after install | Lead fills host result |
| 11 | Frontend production build | NOT RUN | `npm ci && npm run build` on host | Lead fills host result |
| 12 | Browser / persona journey | NOT RUN | Manual end-to-end journey on installed host | Lead fills host result |

## Code-tested coverage detail

### Tamper-negative (B4-D) — `backend/tests/test_rel1b_tamper_negative.py`

Verification fails closed (raises before any install/execute step) for:

- modified payload file — `test_modified_payload_file_is_rejected`
- deleted payload file — `test_deleted_payload_file_is_rejected`
- unexpected extra payload file — `test_unexpected_extra_payload_file_is_rejected`
- modified manifest — `test_modified_manifest_is_rejected`
- wrong externally trusted manifest sha256 — `test_wrong_trusted_manifest_sha256_is_rejected`
- symlink payload — `test_symlink_payload_is_rejected`
- path-traversal manifest entry (`..`) — `test_manifest_entry_with_parent_traversal_is_rejected`
- absolute-path manifest entry — `test_manifest_entry_with_absolute_path_is_rejected`
- reserved-file manifest entry — `test_manifest_entry_naming_reserved_file_is_rejected`

Each case rebuilds its own bundle (`make_bundle`) and never reuses a mutated
artifact. A clean bundle is proven to verify (`test_clean_bundle_verifies_before_any_tamper`),
so the rejections are real, not a verifier that fails on everything.

### Deterministic archive (B4-F) — `scripts/build-rel1-archive.sh`

- Verifies the bundle (optionally against an external trusted manifest digest)
  **before** writing anything; refuses to archive an unverified payload.
- Emits `openjm-rel1-<version>.tar.gz`, version read from `version.py` (never
  hardcoded).
- Deterministic: `--sort=name`, `--mtime=@0`, `--numeric-owner --owner=0
  --group=0`, `--format=gnu`, `gzip -n`; members are relative to the canonical
  top-level directory `openjm-rel1-<version>/` (no machine-specific absolute
  paths).
- Refuses to write into the repository tree; archives are never committed.

### Windows/WSL2 contract (B4-E) — `backend/tests/test_rel1b_windows_contract.py`

Asserted exact strings:

- `[ValidateSet("development", "production")]`, `[string]$Profile = "development"`
- `$linuxArgs = "--profile $Profile"` (profile always forwarded, never silently defaulted)
- `if ($SkipFrontend) { $linuxArgs += " --skip-frontend" }`
- `bash scripts/install-linux.sh $linuxArgs` (canonical installer, no divergent dependency path)
- No `pip install` / `npm ci` / `requirements*.lock` / `--require-hashes` in the PowerShell layer
- `python3.11` installed inside WSL; `install-linux.sh` enforces `Python 3.11 is
  required` and `[[ "$NODE_MAJOR" -eq 22 ]]`
- `".[dev]"` and `[dev]` absent from `install-linux.sh` (production excludes dev extras)

## Not run

All rows marked `NOT RUN` above require a real clean Linux or Windows/WSL2 host
and are executed by the lead on the frozen commit. None of them may be claimed
from the code tests in this repository alone.
