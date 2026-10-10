# REL1 foundation handoff

A handoff for the REL1 packaging and deployment work. It records what this
documentation lane produced, what is grounded in existing code versus proposed
for REL1, what was not executed, and the next steps. This revision replays the original REL1 preparation documents onto accepted main `a047a991cc8882f3f8c0f444387ea190acb689d4` and refreshes deployment facts after BV5, INF1-C, BV6-A/B and delegated support acceptance. It remains preparation, not proof that REL1 is complete.

## Lane

| Field | Value |
| --- | --- |
| Lane | REL1 preparation (documentation and design foundation) |
| Branch | `replay/pr63` |
| Base | `eafce8f3ded930511785eed72eec9c1fbc165627` (`origin/main`) |
| Scope | docs only under `docs/deployment/`, `docs/operations/`, `docs/handoff/` |
| Product code changed | none |

## Reconciliation onto the current baseline

The documents were first written against an earlier baseline. They are replayed
here onto the current `origin/main` and re-checked against the code. The
deployment-relevant modules the foundation is grounded in
(`app/core/config.py`, `app/core/preflight.py`, `app/api/system.py`,
`app/ops/`, `app/migrations_runner.py`, `app/version.py`, `deploy/`,
`scripts/`) are byte-identical between the original base and this baseline, so
the grounded claims hold unchanged. Three corrections were applied and two
documents were added:

- corrected the branch reference in the component inventory (it named a branch
  that no longer exists);
- corrected the backup directory example in the backup/restore contract to the
  format the code actually writes, `%Y%m%dT%H%M%SZ` (for example
  `20260101T000000Z`);
- expanded [../deployment/RELEASE_MANIFEST.md](../deployment/RELEASE_MANIFEST.md)
  with a concrete canonical-version recommendation for the drift below;
- added [../operations/DIAGNOSTICS_AND_SUPPORT_BUNDLE.md](../operations/DIAGNOSTICS_AND_SUPPORT_BUNDLE.md)
  to cover structured logging, correlation ids and the proposed support bundle;
- added a shutdown-ordering section to
  [../deployment/DEPENDENCY_GRAPH.md](../deployment/DEPENDENCY_GRAPH.md).

## What this lane produced

An index and a set of documents under [../deployment/README.md](../deployment/README.md):

| Document | Status |
| --- | --- |
| [COMPONENT_INVENTORY.md](../deployment/COMPONENT_INVENTORY.md) | grounded |
| [DEPENDENCY_GRAPH.md](../deployment/DEPENDENCY_GRAPH.md) | grounded |
| [PORTS_STORAGE_SECRETS.md](../deployment/PORTS_STORAGE_SECRETS.md) | grounded |
| [PROFILES.md](../deployment/PROFILES.md) | grounded |
| [CONNECTED_VS_AIRGAPPED.md](../deployment/CONNECTED_VS_AIRGAPPED.md) | grounded requirements + proposed air-gapped path |
| [BACKUP_RESTORE_CONTRACT.md](../deployment/BACKUP_RESTORE_CONTRACT.md) | grounded |
| [UPDATE_ROLLBACK_CONTRACT.md](../deployment/UPDATE_ROLLBACK_CONTRACT.md) | grounded |
| [LINUX_INSTALLER.md](../deployment/LINUX_INSTALLER.md) | grounded |
| [WINDOWS_INSTALLER.md](../deployment/WINDOWS_INSTALLER.md) | grounded |
| [OFFLINE_BUNDLE.md](../deployment/OFFLINE_BUNDLE.md) | proposed design |
| [RELEASE_MANIFEST.md](../deployment/RELEASE_MANIFEST.md) | proposed design |
| [HEALTH_DOCTOR_CHECKS.md](../operations/HEALTH_DOCTOR_CHECKS.md) | grounded + proposed `doctor` command |
| [DIAGNOSTICS_AND_SUPPORT_BUNDLE.md](../operations/DIAGNOSTICS_AND_SUPPORT_BUNDLE.md) | grounded + proposed support bundle |

## Grounded versus proposed

**Grounded in existing repository code:** the component inventory, dependency
graph (including shutdown order), ports/storage/secrets/configuration inventory,
the profiles, the connected install's network requirements, the backup/restore
and update/rollback contracts (from `backend/app/ops/`), the installer
architectures (from `scripts/`), the health/readiness checks (from
`backend/app/api/system.py`), and the structured logging, correlation-id and
metrics surface (from `backend/app/core/observability.py`,
`backend/app/core/middleware.py`).

**Proposed for REL1 (not implemented):**

1. The offline bundle and the installer's ability to consume it
   ([OFFLINE_BUNDLE.md](../deployment/OFFLINE_BUNDLE.md)).
2. The release manifest and `SHA256SUMS` emission
   ([RELEASE_MANIFEST.md](../deployment/RELEASE_MANIFEST.md)).
3. A consolidated `openjm_ops.py doctor` command
   ([HEALTH_DOCTOR_CHECKS.md](../operations/HEALTH_DOCTOR_CHECKS.md)).
4. A `openjm_ops.py support-bundle` command
   ([DIAGNOSTICS_AND_SUPPORT_BUNDLE.md](../operations/DIAGNOSTICS_AND_SUPPORT_BUNDLE.md)).
5. A decision on whether the release manifest is signed.

## Findings the REL1 work must address

1. **Version drift.** `backend/app/version.py` sets `PRODUCT_VERSION = "0.2.0"`,
   but `backend/pyproject.toml` and `frontend/package.json` both declare
   `0.1.0`. The version module's comment says the three should stay in step, and
   `docs/RELEASE.md` names `backend/app/version.py` as the single source of
   truth. `backend/app/version.py` `PRODUCT_VERSION` is the recommended canonical
   source; the minimal normalization is to set the two package manifests to
   `0.2.0`. See
   [../deployment/RELEASE_MANIFEST.md](../deployment/RELEASE_MANIFEST.md#a-drift-to-resolve-before-rel1).
2. **Air-gapped installer gap.** `scripts/install-linux.sh` currently assumes
   network access to PyPI and the npm registry and pulls container base images.
   No offline source is accepted. See
   [CONNECTED_VS_AIRGAPPED.md](../deployment/CONNECTED_VS_AIRGAPPED.md).

## What was NOT run

- No installer, bundle, image build or Compose bring-up was executed. This lane
  changes documentation only and does not exercise the deployment paths it
  describes.
- No product code, schema, migration, configuration or CI file was changed.
- No release manifest or checksum file was generated (none exists).
- The link checker was run over the whole tree and reports zero broken links
  and zero Markdown problems.

## Next steps for REL1

1. Reconcile the product version across `version.py`, `pyproject.toml` and
   `package.json` so a single source of truth holds.
2. Implement the release manifest and `SHA256SUMS` emission to the schema in
   [RELEASE_MANIFEST.md](../deployment/RELEASE_MANIFEST.md).
3. Extend `scripts/install-linux.sh` to accept an offline source and produce the
   [OFFLINE_BUNDLE.md](../deployment/OFFLINE_BUNDLE.md) structure; add the
   block-the-registries install test that proves no outbound call is made.
4. Add the `doctor` command described in
   [HEALTH_DOCTOR_CHECKS.md](../operations/HEALTH_DOCTOR_CHECKS.md) and the
   support-bundle command described in
   [DIAGNOSTICS_AND_SUPPORT_BUNDLE.md](../operations/DIAGNOSTICS_AND_SUPPORT_BUNDLE.md).
5. Decide the manifest signing story.

## Risks

- The proposed designs (bundle, manifest, doctor, support bundle) are
  unvalidated: they are shaped from the existing backup-manifest, installer and
  observability code, but no build has exercised them.
- The version drift means any release artefact produced before it is reconciled
  will carry inconsistent identity.
- The air-gapped path is the largest open item: it touches the installer, image
  handling and the embedding-model cache, and needs a real disconnected-host
  test rather than a flag check.

## Related accepted documents

[../DEPLOYMENT_PROFILES.md](../DEPLOYMENT_PROFILES.md),
[../INSTALLATION.md](../INSTALLATION.md),
[../CONFIGURATION.md](../CONFIGURATION.md),
[../OPERATIONS.md](../OPERATIONS.md),
[../BACKUP_RESTORE.md](../BACKUP_RESTORE.md),
[../UPGRADE_ROLLBACK.md](../UPGRADE_ROLLBACK.md),
[../RELEASE.md](../RELEASE.md).
