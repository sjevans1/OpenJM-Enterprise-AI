# Release manifest and checksum design

**Status: proposed for REL1. Not implemented.** The repository does not yet emit
a release manifest or a checksum file. This document defines the schema so the
REL1 packaging work has a fixed target. It is grounded in the existing release
identity (`backend/app/version.py`), the existing backup manifest as the
precedent for a versioned, checksummed artefact
([BACKUP_RESTORE_CONTRACT.md](BACKUP_RESTORE_CONTRACT.md)), and the Alembic head
revision.

## Release identity today

| Field | Source | Surfaced in |
| --- | --- | --- |
| product | `app.version.build_info` | `/api/version`, `/api/health`, `/api/config/public` |
| version | `app.version.PRODUCT_VERSION` | `/api/version`, `/api/health` |
| release_train | `app.version.RELEASE_TRAIN` (currently `VS8`) | `/api/version` |
| release_id | `OPENJM_RELEASE_ID` | `/api/version`, `/api/config/public` |
| schema head | the Alembic script head (currently `0021_support_content_scope`) | `/api/ready` |

### A drift to resolve before REL1

The product version is defined in one place at runtime
(`backend/app/version.py`, `PRODUCT_VERSION = "0.2.0"`) but the build manifests
disagree with it: `backend/pyproject.toml` declares `version = "0.1.0"` and
`frontend/package.json` declares `version = "0.1.0"`. The version module's own
comment says to keep the three in step.

**Recommended canonical source.** `backend/app/version.py` `PRODUCT_VERSION` is
the canonical source. It is already the value the running system reports:
`/api/version`, `/api/health` and `/api/config/public` all surface it, the
FastAPI application version is set from it, and the backup manifest
(`app/ops/backup.py`) stamps it. `docs/RELEASE.md` already names
`backend/app/version.py` as the single source of truth for the runtime product
version. The two package manifests are build metadata that must agree with it,
not independent authorities.

**Minimal safe normalization.** Two isolated one-line edits bring the build
manifests into agreement: set `version` in `backend/pyproject.toml` and in
`frontend/package.json` to `0.2.0`. Neither edit changes runtime behaviour, and
neither file is imported by application code. A short test that asserts the three
values are equal would prevent the drift recurring. This is the smallest change
that removes the contradiction a release manifest would otherwise carry; a
build-time derivation from the Python source is the more durable option but is a
larger change and is not required to close the finding.

This is recorded as a finding for the REL1 packaging work. It is not corrected
in this documentation lane, which changes no product code.

## Proposed release manifest schema

`release-manifest.json`, emitted by the release build and shipped in the release
artefact (see [OFFLINE_BUNDLE.md](OFFLINE_BUNDLE.md)):

| Field | Type | Meaning |
| --- | --- | --- |
| `manifest_version` | int | manifest schema version (start at `1`, mirroring the backup manifest) |
| `product` | string | product name (`app.version.build_info()["product"]`) |
| `version` | string | `PRODUCT_VERSION` |
| `release_id` | string | `OPENJM_RELEASE_ID` for this build |
| `release_train` | string | programme that produced the build (`RELEASE_TRAIN`) |
| `schema_head` | string | the Alembic head revision this build expects |
| `built_at` | string | UTC ISO-8601 build timestamp |
| `arch` | string | target CPU architecture (for an offline bundle) |
| `components` | object | per-component versions (backend, frontend, images) |
| `files` | array | per-file records |

Each `files` entry follows the backup-manifest shape for consistency: `path`
(relative to the release root), `sha256`, `bytes`, and a `class` label such as
`source`, `wheel`, `image`, `frontend`, `model`.

```json
{
  "manifest_version": 1,
  "product": "OpenJM Enterprise AI",
  "version": "0.2.0",
  "release_id": "rel1.0.0+build.42",
  "release_train": "REL1",
  "schema_head": "0021_support_content_scope",
  "built_at": "2026-01-01T00:00:00Z",
  "arch": "x86_64",
  "components": {
    "backend": "0.2.0",
    "frontend": "0.2.0",
    "backend_image": "sha256:...",
    "proxy_image": "sha256:..."
  },
  "files": [
    {"path": "source/openjm-0.2.0.tar.gz", "sha256": "...", "bytes": 0, "class": "source"},
    {"path": "wheels/httpx-0.27.0.whl", "sha256": "...", "bytes": 0, "class": "wheel"}
  ]
}
```

## Checksum design

Two levels, so a partial transfer is detectable before anything is trusted:

1. **Per-file checksums** in the manifest's `files` array (SHA-256), mirroring
   the backup manifest and its `verify_backup` behaviour.
2. **A `SHA256SUMS` file** over the manifest and every payload file, in the
   conventional `sha256  path` format, so a recipient can verify with a standard
   tool before unpacking.

A release consumer verifies in this order: checksum the archive, verify
`SHA256SUMS`, then verify each manifest entry against the unpacked tree. Only
after all three pass is the artefact trusted.

## Signing (open question)

The manifest and `SHA256SUMS` make corruption and truncation detectable, not
tampering. Whether REL1 also signs the manifest is an operator decision and is
not specified here: provenance signing needs a key-management story (who holds
the key, how it is rotated) that this lane does not decide. Recorded as an open
question for the REL1 packaging work rather than assumed.

## Relationship to the runtime

The manifest's `version`, `release_id` and `schema_head` must equal what the
running deployment reports:

- `/api/version` returns `version`, `release_id` and `release_train`;
- `/api/ready` reports the migration `revision` against `head`, which must equal
  the manifest's `schema_head`.

A mismatch between the manifest and `/api/version` is a packaging defect, and is
exactly the class of defect the version drift noted above would produce.