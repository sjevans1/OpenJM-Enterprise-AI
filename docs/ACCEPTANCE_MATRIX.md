# Product-Completion Acceptance Matrix (Issue #45)

Living acceptance matrix for the Hardening & Business Value Realization phase
(Issue #45). It maps the global phase-acceptance statements to current
implementation evidence and the remaining manual/runtime qualification.

- **Observed at `7e859f2801bee1d6b5d52688b633709e5e9ed27d`** on **2026-10-11** (current integrated `main`; QA1, QA2, QA3 and OPS1 accepted and merged).
- **Live qualification product heads:** `4479d82ba773affab926d81bffbc1a9d272fdbef` for QA3 (authoritative/vector-RAG qualification, see `docs/qa/QA3_ACCEPTANCE.md`) and `ecb7b69969a189a80aece6302836a4cd8d723a4f` for OPS1 (OpenJM Operations Control Center, see `docs/qa/OPS1_ACCEPTANCE.md`).
- Current accepted code includes BV1-BV6, M1-M3, INF1-A/B/C, E1-E3, REL1-A and
  REL1-B1..B4, on the accepted VS1-VS8 foundation.
- REL1 final-candidate exact-head acceptance: full run **38085064975** on
  `5fed73524c8cc8b34e750d9e800c63d22ab8f7bc` (Backend / Python 3.11, Frontend /
  Node 22, REL1 lock drift gate, REL1 offline bundle + registry-blocked gate all
  success), merged as #84 into `7ca41149`.
- Earlier exact-head full CI for the final BV6-B candidate: run **38026945641** on
  `c7475ecc3fb1362cc1643803a3560bbea9edc3e2`.
- Scope is OpenJM Enterprise AI only. Workspace remains a separate product.
- Rahkia production integration remains deliberately deferred until the current
  product build is complete.

## How to read this document

| Status | Meaning |
| --- | --- |
| **PASS** | The capability exists and has accepted automated/CI evidence at the current integrated lineage. |
| **NOT RUN** | Implementation exists, but a required manual/live/deployment qualification has not been executed. |
| **BLOCKED** | The capability is absent or cannot yet be accepted. |
| **DEFERRED** | Explicitly deferred by product decision. |

A PASS never upgrades a separately identified live/manual gate to PASS.

## Persona: End user

| ID | Acceptance statement (#45) | Current evidence | Remaining qualification | Status |
| --- | --- | --- | --- | --- |
| EU-1 | can primarily use Chat | BV4 chat-first permission-aware navigation; ordinary viewer navigation tests | final browser journey | **PASS** |
| EU-2 | sees only authorized evidence | BV1 policy enforcement before Knowledge/Data/Hybrid/model context; tenant/group/classification negatives | final browser journey | **PASS** |
| EU-3 | can create real downloadable artifacts | BV5-A/B/C: persisted authorized artifacts, controlled HTML/PDF/DOCX rendering, natural Chat artifact creation; #61/#65/#70 accepted | live artifact journey and refusal diagnostics exercised at QA2 | **PASS** |
| EU-4 | does not need to understand control-plane machinery | BV3-C + BV4 separate platform machinery from ordinary tenant navigation | final browser journey | **PASS** |

End-user automated capability: **4/4 PASS**, and the final live browser persona journey is **PASS** (QA2, 2026-10-11).

## Persona: Data steward

| ID | Acceptance statement (#45) | Current evidence | Remaining qualification | Status |
| --- | --- | --- | --- | --- |
| DS-1 | can manage only authorized governed sources | delegated steward scopes and scoped mutation authorization | final browser journey | **PASS** |
| DS-2 | can classify/tag sources | BV1-B/C source policy, groups/departments/classification and audit | final browser journey | **PASS** |
| DS-3 | can create/name governed reports | BV2 naming + governed Saved Reports | final browser journey | **PASS** |
| DS-4 | source/report access follows group/classification policy | report open/rerun/export source revalidation | final browser journey | **PASS** |
| DS-5 | can curate governed reports without making every save authoritative | BV6-A curation states `none|under_review|approved|authoritative`; separate `featured`; two-person authoritative rule; source revalidation | browser workflow PASS (QA2); live curation lifecycle PASS (QA3) | **PASS** |

Data-steward automated capability: **5/5 PASS**.

## Persona: Client admin

| ID | Acceptance statement (#45) | Current evidence | Remaining qualification | Status |
| --- | --- | --- | --- | --- |
| CA-1 | can manage users/groups/stewards/policies without platform-level access | BV3-B tenant-admin API/UX and negative platform-capability tests | final browser journey | **PASS** |
| CA-2 | cannot cross tenant boundary | tenant-scoped identity/admin tests | final browser journey | **PASS** |
| CA-3 | can revoke access and have it take effect promptly | next-request group/membership revocation plus cross-process lifecycle acceptance | final browser journey | **PASS** |

Client-admin automated capability: **3/3 PASS**.

## Persona: OpenJM operator

| ID | Acceptance statement (#45) | Current evidence | Remaining qualification | Status |
| --- | --- | --- | --- | --- |
| OA-1 | can onboard/manage tenants and platform machinery | BV3-A/C platform control plane | production-like walkthrough PASS (QA1/QA2) | **PASS** |
| OA-2 | support initial setup through explicit audited authority | #72 delegated support-content read path consumes active delegation; delegation, tenant, classification and source authorization fail closed before content exposure | live OIDC/PostgreSQL support walkthrough PASS (QA2) | **PASS** |
| OA-3 | does not receive implicit blanket customer-content access | metadata/content delegation separation; platform operator alone has no customer-content authority | production-like walkthrough PASS (QA1/QA2); platform-operator curation negative PASS (QA3) | **PASS** |

OpenJM-operator automated capability: **3/3 PASS**.

## Cross-cutting: Security

| ID | Acceptance statement (#45) | Current evidence | Remaining qualification | Status |
| --- | --- | --- | --- | --- |
| SEC-1 | negative tenant/group/classification tests | BV1 and subsequent security suites | none for automated criterion | **PASS** |
| SEC-2 | unauthorized evidence never enters model context | policy filtering occurs before retrieval/model context; BV1/BV6 negative tests | live representative journey | **PASS** |
| SEC-3 | report/artifact downloads enforce current authorization | report revalidation plus BV5 owner/tenant download authorization | live browser journey PASS (QA2); cross-tenant artifact denial PASS (QA2) | **PASS** |
| SEC-4 | no secret leakage | VS8 config/diagnostic/backup secret handling tests | deployment qualification | **PASS** |
| SEC-5 | no production dev-auth fallback | production preflight rejects dev auth/unsafe fallback | production deployment qualification | **PASS** |
| SEC-6 | audit coverage for privileged mutations | platform, steward, source-policy, curation and delegated-support audit tests | operational audit walkthrough | **PASS** |

Security automated capability: **6/6 PASS**.

## Cross-cutting: Regression

| ID | Acceptance statement (#45) | Evidence | Status |
| --- | --- | --- | --- |
| REG-1 | accepted VS1-VS8 behavior remains green | full exact-head OpenJM CI run **38026945641** on final #75 candidate lineage: Backend / Python 3.11 + Frontend / Node 22 success | **PASS** |
| REG-2 | frontend tests/typecheck/build green | Frontend / Node 22 success in run **38026945641** | **PASS** |
| REG-3 | exact-head CI acceptance before merge | #75 exact-head full run **38026945641** on `c7475ecc...`, followed by frozen-head merge | **PASS** |
| REG-4 | manual browser journey repeated at end of phase | live Chrome/Keycloak persona journeys executed at QA2 and re-exercised through the authoritative candidate path at QA3 | **PASS** |

## Feature close-out

| Area | Current state | Status |
| --- | --- | --- |
| Chat-first UX (BV4) | accepted | **PASS** |
| Source authorization (BV1-A/B/C) | accepted | **PASS** |
| Chat artifact end-to-end flow (BV5-A/B/C) | accepted through #61/#65/#70 | **PASS** automated; live browser/model path PASS (QA2) |
| Governed reports | accepted report lifecycle/revalidation | **PASS** |
| Governed report curation (BV6-A/B) | #71 curation + #75 bounded authoritative candidates | **PASS** |
| Client-admin management (BV3-B) | accepted | **PASS** |
| Platform-admin boundaries (BV3-A/C) | accepted | **PASS** |
| OpenJM Operations Control Center (OPS1) | operator plane on the accepted control plane: fleet/health, usage, audit explorer, security, diagnostics, releases; `platform:*` capability gated; browser never carries the monitoring token | **PASS** live (27 checks; see `docs/qa/OPS1_ACCEPTANCE.md`) |
| Delegated support | #72 consuming read path accepted | **PASS** automated; live OIDC/PostgreSQL walkthrough PASS (QA2) |
| Metering / entitlements (M1/M2/M3) | accepted | **PASS** |
| Admission / capacity (INF1-A/B/C) | registry/admission/capacity telemetry accepted | **PASS** |
| Backup / recovery (E1/E2/E3 + VS8) | accepted automated recovery evidence | **PASS** |
| Installer / productization (REL1) | implementation and package capability accepted: reproducible install, committed hash-pinned locks with a drift gate, production wheelhouse, offline bundle + registry-blocked acceptance, deterministic manifest/archive, packaged application wheel, CI release gates, and the prior PostgreSQL production-profile qualification (2026-10-10) | **PASS** for implementation/package capability; **NOT RUN / OPEN** for final clean-host qualification under Issue #79 (clean Linux development host; clean Linux production host with PostgreSQL and production-safe paths; supported Windows/WSL2 runtime; browser/persona journey on the production-installed artifact). See `docs/REL1_B4_ACCEPTANCE.md`. |
| Rahkia qualification | intentionally not integrated during current product build | **DEFERRED** |

## Remaining completion gates

The feature gap list is now short. The remaining work is primarily productization
and final integrated qualification, not another broad business-feature wave.

1. **REL1 implementation and packaging: PASS.** REL1-A and REL1-B1..B4 delivered
   the reproducible install, committed hash-pinned locks with a drift gate, the
   production wheelhouse, the offline bundle with registry-blocked acceptance,
   the deterministic manifest/archive, the packaged application wheel and the CI
   release gates, and the prior PostgreSQL production-profile qualification ran
   on 2026-10-10. This is implementation/package capability, and it is the only
   part of REL1 that is complete.
2. **REL1-B4 final host/runtime qualification: partly PASS, partly OPEN under
   Issue #79.** **PASS**: the clean Linux **development** host (Docker Agentic
   Platform Cloud sandbox) and the supported **Windows/WSL2** runtime install.
   **NOT RUN / OPEN**: the clean Linux **production** host with PostgreSQL and
   production-safe paths, and the final **browser/persona journey on the
   production-installed artifact**. Issue #79 makes the outstanding rows part of
   the release acceptance contract, so they are NOT optional compatibility
   coverage, and they must not be upgraded from CI, prior WSL evidence, the prior
   PostgreSQL qualification or contract tests. Current status is recorded in
   `docs/REL1_B4_ACCEPTANCE.md`.
3. **Product journey acceptance on the controlled QA environment: PASS, and
   distinct from host qualification.** QA2 executed the real OIDC persona and
   delegated-support journeys, QA3 executed the live vector/RAG
   authoritative-candidate path, and OPS1 executed the operator-plane journey.
   That is product acceptance on a production-like environment, not the
   REL1-B4 clean-host row, which remains NOT RUN.
4. **Rahkia cutover is DEFERRED by design.** Do not connect the application to
   Rahkia during the remaining product build. The specialist integration phase
   later verifies call paths, M1/M2/M3 attribution, hardware/security/performance
   and supported deployment topologies.
5. **Per-object/table classification remains deliberately deferred for v1.**
   Source-level classification plus authorized-object narrowing remains the v1
   contract unless an unsplittable mixed-sensitivity source creates a proven need.

## Live/deployment qualification

Two qualification sets are recorded here: the automated/controlled-environment
qualification performed 2026-10-10, and the host rows restamped for current `main`
(`7e859f2801bee1d6b5d52688b633709e5e9ed27d`) on 2026-10-11. Host rows carry their own
date; do not read an older row as current-artifact host evidence.

| Gate | Environment | Status | Evidence |
| --- | --- | --- | --- |
| Full PostgreSQL production-profile install/migrate/ready | real PostgreSQL (pgserver, non-root), packaged wheel, committed production lock, Python 3.11 | **PASS** | `install-linux.sh --profile production` from the release bundle: `INSTALLER_RC=0`; migrations on `PostgresqlImpl` to head `0021_support_content_scope`; `REL1-A VERIFY OK: version=0.2.0 profile=production`; `/api/ready` 200 `{"ready":true,"status":"ready"}`; `/api/health` 200; `alembic_version=0021_support_content_scope` in PostgreSQL; no SQLite metadata DB; no dev extras; no PYTHONPATH |
| Frontend production build (registry-blocked) | Node 22, release npm cache | **PASS** | `npm ci` offline (138 packages) + `npm run build`; `dist/index.html` emitted |
| Deterministic release archive | real 3.1 GB payload | **PASS** | two builds byte-identical: `00fa8adbd6409b5fa840d4196989fd6c9e14469f159ea157df8da67af27718ba`, 3243220300 bytes; manifest `f3c9926c79c4b7c57929a62c1f57b66cad8d070b26578b71fc425a0d47739236` |
| Final browser/persona journey (end user, data steward, client admin, OpenJM operator) | live QA env: PostgreSQL production profile, Keycloak 26.7.4 OIDC, seeded personas | **PASS** | QA2 (2026-10-11) live Chrome journeys for all four personas through real OIDC authorization-code + PKCE |
| Live delegated-support / OIDC walkthrough | live Keycloak 26.7.4 + PostgreSQL 16 | **PASS** | QA2 delegated-support grant, consumption, ceiling, audit, revocation and expiry exercised live; residual delegation revoked at handoff |
| Live authoritative-candidate / vector-RAG journey (BV6-B) | DB-GPT knowledge engine + Chroma, `glm-5.3-flash` | **PASS** | QA3 (2026-10-11) 50 PASS: ordinary retrieval, curation lifecycle, two-person rule, additive candidate, non-authoritative negatives, relevance gate, revocation/regrant, unauthorized and cross-tenant negatives, authorization-before-context, withdrawal. See `docs/qa/QA3_ACCEPTANCE.md` |
| Clean Linux development host install | Docker Agentic Platform Cloud sandbox `openjm-rel1-dev` (medium 4 vCPU / 8 GiB, Ubuntu 26.04.1, kernel 6.12.103, x86_64) | **PASS** (2026-10-12) | Fresh clean sandbox, independent GitHub clone at `2456f92a`, Python 3.11.9 operator-provisioned (GPG-verified python.org source, digest re-verified independently), Node 22.22.1, canonical `install-linux.sh --profile development` exit 0, REL1-A verifier OK, migration current == head `0021_support_content_scope`, frontend built, backend started from the new venv, `/api/health` 200, `/api/ready` 200 `ready:true`. See `docs/REL1_B4_ACCEPTANCE.md`. |
| Real Windows/WSL2 runtime install | Windows 11 Pro build 26200, WSL 2.6.3.0, fresh `openjm-rel1b4` distro (Ubuntu 24.04.5, root) | **PASS** (2026-10-12) | Real supported chain from Windows PowerShell through the hardened `install-wsl2.ps1` to `install-linux.sh --profile development` at `c3937c88`: bootstrap exit 0, REL1-A verifier OK, migration current == head `0021_support_content_scope`, frontend built, backend started from the new venv, `/api/health` 200, `/api/ready` 200 `ready:true`. See `docs/REL1_B4_ACCEPTANCE.md`. |
| Rahkia production cutover | n/a | **DEFERRED** | deliberate product decision |

The PostgreSQL production profile, the frontend production build, the live OIDC
persona/delegated-support journeys and the live vector/RAG authoritative-candidate
journey all pass. Current #79 host/runtime state: **PASS** for the clean Linux
development host and the Windows/WSL2 supported runtime; **NOT RUN / OPEN** for the
clean Linux production/PostgreSQL host and the production-installed browser/persona
journey. The remaining rows are mandatory release-acceptance rows, not optional
compatibility coverage, and they must not be upgraded from CI, prior WSL evidence,
the prior PostgreSQL qualification or contract tests.

## How this matrix is maintained

Restamp the accepted `main` SHA whenever a materially relevant feature or
productization package lands. Link exact-head acceptance evidence. Do not turn a
manual/live gate into PASS merely because automated CI is green. The companion
`backend/tests/test_acceptance_matrix.py` protects the document's required
persona and feature-area structure.

## REL1-B4 host qualification (current artifact)

The current-main REL1-B4 host qualification is recorded in `docs/REL1_B4_ACCEPTANCE.md`.
Re-verified on the current release artifact built from
`7e859f2801bee1d6b5d52688b633709e5e9ed27d`: trusted-digest verification PASS
(`fb97c0bf...`, 916 files), bounded tamper-negative PASS, and offline
committed-lock install of the current artifact PASS (bundled wheelhouse with
`--no-index --require-hashes`, application wheel with `--no-deps`, no dev extras).
The clean Linux development host and the Windows/WSL2 runtime install are **PASS**
(see `docs/REL1_B4_ACCEPTANCE.md`). The clean Linux production/PostgreSQL host and the
on-host browser/persona journey remain **NOT RUN** and OPEN under Issue #79.
