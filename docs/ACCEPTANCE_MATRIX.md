# Product-Completion Acceptance Matrix (Issue #45)

Living acceptance matrix for the Hardening & Business Value Realization phase
(Issue #45). It maps the global phase-acceptance statements to current
implementation evidence and the remaining manual/runtime qualification.

- **Observed at `23ea48458747e616c7f988eeb73c4d04ff7b7ef2`** on **2026-10-10** (current integrated `main`).
- **Live qualification product head: `7ca41149c7a23e8330c4dfd1f377bb4ad2feb477`**. The only intervening repository delta to current `main` is PR #85's CI-only change to `.github/workflows/ci.yml` (Ubuntu 24.04 pin + GitHub Actions v7); no application, release, runtime, lock, migration, or product files changed, so the live qualification evidence remains applicable to the current product tree.
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
| EU-3 | can create real downloadable artifacts | BV5-A/B/C: persisted authorized artifacts, controlled HTML/PDF/DOCX rendering, natural Chat artifact creation; #61/#65/#70 accepted | live browser/model journey still not run | **PASS** |
| EU-4 | does not need to understand control-plane machinery | BV3-C + BV4 separate platform machinery from ordinary tenant navigation | final browser journey | **PASS** |

End-user automated capability: **4/4 PASS**. Final browser journey remains NOT RUN.

## Persona: Data steward

| ID | Acceptance statement (#45) | Current evidence | Remaining qualification | Status |
| --- | --- | --- | --- | --- |
| DS-1 | can manage only authorized governed sources | delegated steward scopes and scoped mutation authorization | final browser journey | **PASS** |
| DS-2 | can classify/tag sources | BV1-B/C source policy, groups/departments/classification and audit | final browser journey | **PASS** |
| DS-3 | can create/name governed reports | BV2 naming + governed Saved Reports | final browser journey | **PASS** |
| DS-4 | source/report access follows group/classification policy | report open/rerun/export source revalidation | final browser journey | **PASS** |
| DS-5 | can curate governed reports without making every save authoritative | BV6-A curation states `none|under_review|approved|authoritative`; separate `featured`; two-person authoritative rule; source revalidation | browser workflow not run | **PASS** |

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
| OA-1 | can onboard/manage tenants and platform machinery | BV3-A/C platform control plane | production-like walkthrough | **PASS** |
| OA-2 | support initial setup through explicit audited authority | #72 delegated support-content read path consumes active delegation; delegation, tenant, classification and source authorization fail closed before content exposure | live OIDC/PostgreSQL support walkthrough not run | **PASS** |
| OA-3 | does not receive implicit blanket customer-content access | metadata/content delegation separation; platform operator alone has no customer-content authority | production-like walkthrough | **PASS** |

OpenJM-operator automated capability: **3/3 PASS**.

## Cross-cutting: Security

| ID | Acceptance statement (#45) | Current evidence | Remaining qualification | Status |
| --- | --- | --- | --- | --- |
| SEC-1 | negative tenant/group/classification tests | BV1 and subsequent security suites | none for automated criterion | **PASS** |
| SEC-2 | unauthorized evidence never enters model context | policy filtering occurs before retrieval/model context; BV1/BV6 negative tests | live representative journey | **PASS** |
| SEC-3 | report/artifact downloads enforce current authorization | report revalidation plus BV5 owner/tenant download authorization | live browser journey | **PASS** |
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
| REG-4 | manual browser journey repeated at end of phase | not yet executed at final phase state | **NOT RUN** |

## Feature close-out

| Area | Current state | Status |
| --- | --- | --- |
| Chat-first UX (BV4) | accepted | **PASS** |
| Source authorization (BV1-A/B/C) | accepted | **PASS** |
| Chat artifact end-to-end flow (BV5-A/B/C) | accepted through #61/#65/#70 | **PASS** automated; live browser/model path NOT RUN |
| Governed reports | accepted report lifecycle/revalidation | **PASS** |
| Governed report curation (BV6-A/B) | #71 curation + #75 bounded authoritative candidates | **PASS** |
| Client-admin management (BV3-B) | accepted | **PASS** |
| Platform-admin boundaries (BV3-A/C) | accepted | **PASS** |
| Delegated support | #72 consuming read path accepted | **PASS** automated; live OIDC/PostgreSQL walkthrough NOT RUN |
| Metering / entitlements (M1/M2/M3) | accepted | **PASS** |
| Admission / capacity (INF1-A/B/C) | registry/admission/capacity telemetry accepted | **PASS** |
| Backup / recovery (E1/E2/E3 + VS8) | accepted automated recovery evidence | **PASS** |
| Installer / productization (REL1) | REL1-A + REL1-B1..B4 accepted (reproducible install, committed hash-pinned locks, lock drift gate, offline bundle + registry-blocked acceptance, deterministic manifest/archive, packaged-wheel production install); full PostgreSQL production-profile acceptance executed 2026-10-10 | **PASS** (automated + PostgreSQL production profile); clean dev host, Windows/WSL2 runtime, live browser persona journey NOT RUN |
| Rahkia qualification | intentionally not integrated during current product build | **DEFERRED** |

## Remaining completion gates

The feature gap list is now short. The remaining work is primarily productization
and final integrated qualification, not another broad business-feature wave.

1. **REL1 productization is now accepted.** REL1-A and REL1-B1..B4 delivered the
   reproducible install, committed hash-pinned locks with a drift gate, the
   offline bundle with registry-blocked acceptance, the deterministic
   manifest/archive, and the packaged-wheel production install. The full
   PostgreSQL production-profile acceptance was executed 2026-10-10 (see
   "Live/deployment qualification" below). What remains for REL1 is live
   environment qualification that requires hosts not available in this
   environment.
2. **Final manual/browser journey is NOT RUN at the current phase state.**
   Repeat the end-user, data-steward, client-admin and OpenJM-operator golden
   journeys against the final packaged candidate.
3. **Live environment qualifications remain scoped honestly.** #72 did not run
   live OIDC/Keycloak or a live PostgreSQL support walkthrough; #75 did not run a
   real vector-engine/RAG path for authoritative candidates.
4. **Rahkia cutover is DEFERRED by design.** Do not connect the application to
   Rahkia during the remaining product build. The specialist integration phase
   later verifies call paths, M1/M2/M3 attribution, hardware/security/performance
   and supported deployment topologies.
5. **Per-object/table classification remains deliberately deferred for v1.**
   Source-level classification plus authorized-object narrowing remains the v1
   contract unless an unsplittable mixed-sensitivity source creates a proven need.

## Live/deployment qualification (2026-10-10, qualified product head `7ca41149`; current `main` `23ea4845` differs only by CI workflow modernization)

| Gate | Environment | Status | Evidence |
| --- | --- | --- | --- |
| Full PostgreSQL production-profile install/migrate/ready | real PostgreSQL (pgserver, non-root), packaged wheel, committed production lock, Python 3.11 | **PASS** | `install-linux.sh --profile production` from the release bundle: `INSTALLER_RC=0`; migrations on `PostgresqlImpl` to head `0021_support_content_scope`; `REL1-A VERIFY OK: version=0.2.0 profile=production`; `/api/ready` 200 `{"ready":true,"status":"ready"}`; `/api/health` 200; `alembic_version=0021_support_content_scope` in PostgreSQL; no SQLite metadata DB; no dev extras; no PYTHONPATH |
| Frontend production build (registry-blocked) | Node 22, release npm cache | **PASS** | `npm ci` offline (138 packages) + `npm run build`; `dist/index.html` emitted |
| Deterministic release archive | real 3.1 GB payload | **PASS** | two builds byte-identical: `00fa8adbd6409b5fa840d4196989fd6c9e14469f159ea157df8da67af27718ba`, 3243220300 bytes; manifest `f3c9926c79c4b7c57929a62c1f57b66cad8d070b26578b71fc425a0d47739236` |
| Final browser/persona journey (end user, data steward, client admin, OpenJM operator) | none | **NOT RUN** | no live OIDC/Keycloak provider and no seeded personas in this environment; production disables dev-auth by design |
| Live delegated-support / OIDC walkthrough | none | **NOT RUN** | no real Keycloak/OIDC environment available |
| Live authoritative-candidate / vector-RAG journey (BV6-B) | none | **NOT RUN** | no live vector/RAG environment (model gateway on the approved test path not provisioned here) |
| Clean Linux development host install | none | **NOT RUN** | no separate clean host |
| Real Windows/WSL2 runtime install | none | **NOT RUN** | no real Windows host; machine-testable contract PASS only |
| Rahkia production cutover | n/a | **DEFERRED** | deliberate product decision |

These live gates are NOT RUN, not PASS. The PostgreSQL production profile and the
frontend production build are the two most important remaining integrated gates
and both passed.

## How this matrix is maintained

Restamp the accepted `main` SHA whenever a materially relevant feature or
productization package lands. Link exact-head acceptance evidence. Do not turn a
manual/live gate into PASS merely because automated CI is green. The companion
`backend/tests/test_acceptance_matrix.py` protects the document's required
persona and feature-area structure.
