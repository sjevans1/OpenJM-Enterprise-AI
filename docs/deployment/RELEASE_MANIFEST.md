# Release manifest and checksum design

**Status: REL1-B implementation in progress.** `scripts/build-rel1b-bundle.py` now emits `BUNDLE-MANIFEST.json` plus `SHA256SUMS` for the connected-build/offline-install payload. Production image capture remains an acceptance gate before this is complete. It is grounded in the existing release
identity (`backend/app/version.py`), the existing backup manifest as the
precedent for a versioned, checksummed artefact
([BACKUP_RESTORE_CONTRACT.md](BACKUP_RESTORE_CONTRACT.md)), and the Alembic head
revision.

## Release identity today

| Field | Source | Surfaced in |
| --- | --- | --- |
| product | `app.version.build_info` | `/api/version`, `/api/health`, `/api/config/public` |
| version | `app.version.PRODUCT_VERSION` | `/api/version`, `/api/health` |
| release_train | `app.version.RELEASE_TRAIN` (currently `REL1`) | `/api/version` |
| release_id | `OPENJM_RELEASE_ID` | `/api/version`, `/api/config/public` |
| schema head | the Alembic script head (currently `0021_support_content_scope`) | `/api/ready` |

### Release identity normalization (REL1-A)

REL1-A establishes `backend/app/version.py::PRODUCT_VERSION` as the
canonical product version and machine-checks the backend package metadata,
frontend package metadata and npm lock metadata against it. They are normalized
to `0.2.0`, and the release train is `REL1`.

The runtime and package manifests must remain identical. A mismatch is an
acceptance failure, not a documentation-only warning.

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