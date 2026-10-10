# QA1: Production-like Acceptance Environment (OpenJM Enterprise AI)

Status: **PASS** for the environment qualification. This record documents one
controlled, production-like acceptance environment for reuse by QA2 and QA3. It
is a qualification package, not a feature-development wave. No product code was
changed, and nothing is merged.

## 1. Tested identity

| Field | Value |
| --- | --- |
| Repository | `sjevans1/OpenJM-Enterprise-AI` |
| Tested Git SHA | `0d3eee49ec27ae813721bdfe95c461f6d5797f50` (current `main`) |
| Baseline requested | `0d3eee49ec27ae813721bdfe95c461f6d5797f50` (exact match) |
| Product version | `0.2.0` (`RELEASE_TRAIN=REL1`) |
| Release identifier | `rel1-qa1` (QA1 environment label) |
| Release bundle | `openjm-rel1-0.2.0` |
| Bundle manifest sha256 | `f3c9926c79c4b7c57929a62c1f57b66cad8d070b26578b71fc425a0d47739236` (verified, 884 files) |
| Production lock sha256 | `90afebf3c719b1db838cbf77a3799477c8ac312d16817aca7fab670b34fde72b` |
| Application wheel sha256 | `cde425359f38295dac899d358e57ea0b823387135d228179fbeacb9803a4f081` |

The release bundle's source payload matches the current product tree exactly; the
only deltas between the bundle build and `main` are PR #85 (CI workflow) and
PR #86 (docs). No application, lock, migration, or runtime file differs.

## 2. Environment

| Field | Value |
| --- | --- |
| Host | WSL2 (Ubuntu 24.04.4 LTS) on the maintainer workstation, non-root |
| Kernel | `6.6.87.2-microsoft-standard-WSL2` |
| Python | 3.11.15 (REL1 production virtualenv) |
| Node / npm | 22.23.2 / 10.9.8 |
| Docker / Compose | 29.1.3 / 2.40.3 |
| PostgreSQL | 16.15 (`postgres:16` container) |
| Keycloak | 26.7.4 (`quay.io/keycloak/keycloak:26.7.4`) |
| Frontend host | `caddy:2.11.4-alpine` |
| Migration head | `0021_support_content_scope` (PostgreSQL) |
| Metadata database | PostgreSQL (no SQLite fallback) |

Services (loopback only): PostgreSQL `127.0.0.1:15432`, Keycloak
`127.0.0.1:18090`, backend `0.0.0.0:18010`, frontend `127.0.0.1:15173`, model
endpoint `127.0.0.1:18080`.

## 3. Production installation

The backend runs the accepted REL1 production install, installed by
`deploy/qa/install-backend.sh`:

* dependencies installed offline from the release wheelhouse with
  `--require-hashes` against the committed production lock;
* the packaged application wheel installed with `--no-deps`;
* `app` imports from `site-packages` (no editable install, no `PYTHONPATH`);
* no `[dev]` extras present (`pytest`, `pgserver`, `hatchling` absent);
* `python -m app.core.preflight` reports `VALID` for the production profile;
* migrations run to head `0021_support_content_scope` on `PostgresqlImpl`.

## 4. Keycloak / OIDC

| Field | Value |
| --- | --- |
| Provider | Keycloak 26.7.4, realm `openjm` |
| Issuer | `http://127.0.0.1:18090/realms/openjm` |
| Client | `openjm` (confidential, secret rotated by the seeder) |
| Flow | authorization code + PKCE `S256` |
| Audience | `openjm` (via an audience protocol mapper; `aud` verified) |
| Tenant claim | `tenant` (Keycloak user attribute), `TENANT_RESOLUTION=claim` |
| Dev-auth | disabled: `OPENJM_AUTH_MODE=oidc`, `OPENJM_AUTH_ALLOW_DEV_MODE=false` |

The realm template ships as a non-secret file
(`deploy/qa/keycloak/realm-openjm.json`): client, mappers and persona user shells.
The client secret and every persona password are generated at seed time and kept
outside the repository.

## 5. Seeded tenants and personas

| Tenant | Purpose | Slug |
| --- | --- | --- |
| Acme Corporation | primary tenant (personas below) | `acme` |
| Globex Industries | second tenant for isolation testing | `globex` |
| OpenJM Platform Operations | platform-operator home tenant | `openjm-platform` |

| Persona | Tenant | Role | Resolved authority (observed) |
| --- | --- | --- | --- |
| `qa.employee` | Acme | viewer | ordinary member; no tenant admin; no stewardship; no platform capability |
| `qa.steward` | Acme | editor | stewarded over department HR (`department:c92c355c...`); no tenant admin; no platform capability |
| `qa.clientadmin` | Acme | owner | holds `tenant:admin`; no platform capability |
| `qa.other` | Globex | owner | second-tenant administrator, isolated from Acme |
| `qa.platform` | OpenJM Platform Operations | owner (platform tenant) | `platform:metadata:read`, `platform:tenants:admin`, `platform:operators:admin`; no `platform:content:support`; no membership in Acme |

The first platform operator was established exactly once through the accepted
bootstrap path (which refuses replay and never grants content support).

## 6. Governance fixtures

Departments: General, HR, Finance.
Groups: All Employees, HR Leadership (HR), Finance Leadership (Finance).

Knowledge (governed documents):

| Fixture | Classification | Visibility |
| --- | --- | --- |
| Employee Handbook | `internal` | tenant-wide |
| HR Compensation Policy | `highly_restricted` | HR Leadership group only |
| Finance Policy | `confidential` | Finance Leadership group only |

Structured data: one governed source (`QA Demo Warehouse`, engine `postgresql`,
classification `highly_restricted`, HR scoped) backed by a dedicated `qa_demo`
database with synthetic tables `customers`, `orders`, `inventory`,
`employee_summary`. No real personal or production data is present.

## 7. Model gateway and vector/RAG

| Field | Value |
| --- | --- |
| Model mode | `local` (OpenAI-compatible) |
| Endpoint | `http://127.0.0.1:18080/v1`, model `gemma-4-12b-local` |
| Credential | server-side only; never returned to the browser |
| Fallback policy | `none` |
| Usage metering | active (per-invocation events) |
| Vector/RAG | DB-GPT 0.8.2 + Chroma; embedding `sentence-transformers/all-MiniLM-L6-v2` (local Hugging Face cache; no download required) |
| Readiness (`/api/ready/detail`) | `knowledge` ok (vector writable), `model_provider` ok (mode local, model present) |

## 8. Health

* `/api/ready` -> `{"ready":true,"status":"ready"}`
* `/api/health` -> `{"status":"ok","product":"OpenJM Enterprise AI","version":"0.2.0","knowledge_engine":"DB-GPT","model":"gemma-4-12b-local","profile":"production"}`
* `/api/ready/detail` -> `ready: true`; gating components `database`, `migrations` (head `0021_support_content_scope`), `storage` all ok.
* Frontend -> `GET http://127.0.0.1:15173/` 200; deep route (SPA fallback) 200; `/api/*` proxy 200.

## 9. Phase 6 identity/governance smoke matrix

24 of 24 checks PASS against the live environment. Raw report:
`$OPENJM_QA_ENV_ROOT/artifacts/smoke_matrix.json`.

| # | Check | Result |
| --- | --- | --- |
| C-01..C-05 | all five personas authenticate via real OIDC authorization code + PKCE | PASS |
| C-06 | end user resolves to tenant A, role viewer | PASS |
| C-07 | data steward resolves to tenant A with delegated HR stewardship | PASS |
| C-08 | client admin resolves to tenant A with `tenant:admin`, no platform capability | PASS |
| C-09 | platform operator resolves to the platform tenant with exactly the bootstrap capabilities (no content support) | PASS |
| C-10 | second-tenant user resolves to tenant B | PASS |
| C-11 | client admin receives no platform capability (platform plane 403) | PASS |
| C-12 | platform operator has no implicit customer-content access (tenant A refused) | PASS |
| C-13 | ordinary employee has no tenant administration (403) | PASS |
| C-14 | data steward is not a tenant administrator (403) | PASS |
| C-15 | second tenant remains isolated (no shared governed documents) | PASS |
| C-16 | cross-tenant governed-source access refused (404, ownership scoped) | PASS |
| C-17 | no dev-auth fallback (anonymous refused, `auth_mode=oidc`) | PASS |
| C-18 | ordinary member retrieval returns authorized tenant-wide evidence | PASS |
| C-19 | ordinary member retrieval does not return the restricted HR document | PASS |
| C-20 | data steward retrieval returns the restricted HR evidence they are authorized for | PASS |
| C-21 | governed structured retrieval executes against the PostgreSQL source | PASS |
| C-22 | backend readiness detail green (PostgreSQL + model provider components) | PASS |
| C-23 | approved model-gateway path reachable and configured | PASS |
| C-24 | no Rahkia production cutover claimed by the running build | PASS |

Representative governed retrieval (from the smoke run):

* ordinary member, handbook question -> one document evidence, the
  `Employee Handbook`; the HR document is not returned.
* data steward, HR bonus question -> one document evidence, the restricted
  `HR Compensation Policy` (authorized by HR Leadership membership/stewardship).
* data steward, orders question in `data` mode -> structured evidence from the
  governed PostgreSQL source.

## 10. Acceptance criteria (QA1)

| Criterion | Status |
| --- | --- |
| Production-profile OpenJM running | PASS |
| PostgreSQL is the active metadata database | PASS |
| Migrations at current head | PASS (`0021_support_content_scope`) |
| Backend ready/health green | PASS |
| Frontend production UI reachable | PASS |
| Real OIDC login works | PASS (authorization code + PKCE) |
| Four principal personas exist and resolve correctly | PASS |
| Second tenant exists for isolation tests | PASS |
| Governed Knowledge fixtures exist | PASS |
| Governed structured Data fixtures exist | PASS |
| Approved model-gateway path executes | PASS |
| Live vector/RAG dependency healthy | PASS |
| Representative governed retrieval succeeds | PASS |
| No dev-auth fallback active | PASS |
| No Rahkia production cutover claimed | PASS |
| Environment reproducibly documented without secrets | PASS |

## 11. Repository changes

Yes: one bounded QA1 scaffolding PR. It adds environment artifacts only
(`deploy/qa/**`, `scripts/qa/**`, this document). No product code, lock,
migration, or CI change is included. Secrets are excluded by
`deploy/qa/.gitignore`; only the non-secret Keycloak realm template, the
Compose/Caddy/env templates, the synthetic SQL fixture, and the seeder/smoke
scripts are committed.

## 12. Not run (not claimed)

* The full QA2 browser persona journey (interactive UI walkthrough) is not run
  here; this environment is the substrate for it.
* The final BV6 authoritative-candidate RAG acceptance is not run here; QA1
  proves the live vector/RAG dependency is healthy and usable for it.
* Windows-native runtime and clean separate-host install remain not run.

## 13. Warnings and pitfalls

* Keycloak sets its login cookies `Secure`; over loopback HTTP a client must
  replay them explicitly (`scripts/qa/qa_env_lib.py`).
* The first platform operator has no HTTP bootstrap route; the seeder performs
  that step through the service layer, everything else over HTTP.
* Fixture visibility is classification/group scoped, so a fixture can be
  invisible even to the tenant admin (an HR-Leadership-only document is invisible
  to a Finance-titled admin). The seeder accounts for this to stay idempotent.
* The frontend host container reaches the backend via `host.docker.internal`, so
  the backend binds `0.0.0.0:18010`; host validation is enforced by
  `OPENJM_TRUSTED_HOSTS`. This is a loopback acceptance environment, not a
  public deployment.

## 14. Blockers

None. No stop condition was hit: no auth weakening, no security-boundary
failure, no destructive migration, no tenant-isolation failure, no lock change,
no Rahkia dependency, and no paid infrastructure.

## 15. Readiness and next action

QA1 is ready for QA2. Recommended next action: human review of the QA1 PR, then
proceed to QA2 persona/security journeys against this environment (browser-driven
journeys across the four personas), followed by QA3 vector/RAG plus final closure.
No merge without explicit human approval.
