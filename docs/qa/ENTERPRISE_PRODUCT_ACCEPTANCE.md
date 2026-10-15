# Enterprise product journey acceptance

Status: **PLANNED; all new journey results NOT RUN.** Recorded 15 October 2026. This contract qualifies the [enterprise productization programme](../plan/ENTERPRISE_PRODUCTIZATION.md), alongside the [UI standard and surface inventory](../design/UI_QUALITY_STANDARD.md). Historical acceptance remains in [ACCEPTANCE_MATRIX](../ACCEPTANCE_MATRIX.md); those passes are not copied into these rows.

## 1. Acceptance profiles and fixtures

Use two clearly identified profiles. **Fixture UI** tests use synthetic API data for component behavior, request counts, adverse states and visual consistency. **Integrated live** tests use the candidate application, real supported identity/database/connector/model services and isolated synthetic business data. A fixture pass cannot substitute for a live integration result.

Record the commit/tree, build inputs, artifact digest where packaged, deployment/configuration profile, browser/viewport, provider versions/auth modes, fixture version, tester and execution time. Never include production credentials or customer content in evidence. For model tests record the actual model/configuration and verify expected facts/citations and denials, not exact prose.

Seed two unrelated tenants, a provider operator with metadata-only capability, a narrowly authorized support operator, tenant owner/admin, limited steward, authorized employee, denied employee and revoked user. Include two differently scoped groups, a public/internal tenant-visible document, a restricted document containing a unique secret test fact, a permitted database with known aggregate values, a forbidden object/source, an expired delegation and relevant permission changes. Include another admin/session for concurrency.

UI fixture scale: 100 users, 100 source entries and 1,000 catalog metadata entries behind paginated responses, long names and descriptions, zero results, partial pages, slow/failed requests and stale responses. Use Chromium with 1440×900, 768×1024 and 390×844 viewports; record host/CPU constraints. Check reflow at 320 CSS pixels and 200% zoom. This is a UI exercise, not a production capacity claim.

For the first-value usability session, provide a prepared test organization, supported connection credentials, documents and documented identity prerequisites. A representative tenant admin should complete setup and reach the first permitted grounded answer within **20 minutes**, excluding external account provisioning and infrastructure installation, with **zero shell/database edits or engineer intervention**. Record time, help used, confusion and recovery; a miss requires investigation and product-owner disposition rather than removing failed steps. Model/integration latency is measured separately.

For UI responsiveness, measure at least 20 representative filter/navigation/edit/submit interactions on the recorded fixture host. Input acknowledgment/busy feedback must be **p95 ≤200ms**. No unbounded rendering of the full catalog, frozen controls or duplicate writes. New adapters must declare bounded query/sync/timeout limits and a workload-specific performance target in their integration contract before implementation; universal throughput and model-latency claims are out of scope.

## 2. Golden and failure journeys

Every row is required for its implemented scope. The package column connects to ER requirements in the plan; UI rows supply the exact surface coverage. Results remain NOT RUN until linked evidence is accepted.

| ID | Journey and package | Positive proof | Failure / authority proof | Required evidence |
| --- | --- | --- | --- | --- |
| EJ-01 | Consistent daily workspace; EP-01/02 | Sign in, navigate, ask, inspect evidence/artifact, reopen conversation; same hierarchy and controls on desktop/mobile | Expired session, empty history, unavailable answer, denied evidence and long content remain usable | Fixture browser + visual/keyboard review; live SSO and persisted conversation |
| EJ-02 | Reviewed administrative change; EP-00/03/07 | Change role/group/steward scope, review named target and old/new values, confirm, refresh, inspect audit | Field change and Cancel issue zero writes; double click one logical write; refusal retains draft; no self/last-owner privilege bypass | Component/network assertions + API negative tests + browser audit/persistence |
| EJ-03 | Concurrent policy and effective access; EP-04 | Inspect permitted subject/resource policy, preview and save revision, verify actual retrieval agrees | Two admins editing same revision cannot lose updates; forged/stale/cross-tenant requests fail; no hidden metadata or content leaks; permission changes between review and save honored | Two-session integrated API/browser evidence and evaluator parity cases |
| EJ-04 | Policy-first Knowledge intake; EP-06 | Select files/audience, review, ingest, publish where applicable, retrieve allowed fact and citation | Restricted fact unavailable during pending/indexing/retry; malformed/oversized file; partial batch; wrong tenant; failed stage retry creates no duplicate | Live ingestion/retrieval + per-stage negative probes + UI recovery |
| EJ-05 | Company first value; EP-08 | New tenant completes identity/groups/source discovery/scope/policy/Knowledge/activation and a grounded answer within the profile target | Interrupt/resume setup; invalid credentials; denied persona; repeat activation; no duplicate resources or claimed unsupported integration | Recorded representative-admin session, timing and live API/model evidence |
| EJ-06 | Tenant-wide lifecycle; EP-05 | Authorized admin manages another owner's resource, rotates secret, transfers ownership, retires source/document | Ordinary user and second tenant denied; job race cannot republish; old secret unusable; future retrieval/report/export fails after revocation; no external DB deletion | Integrated lifecycle/API tests, audit and user-facing recovery |
| EJ-07 | Identity onboarding/offboarding; EP-07 | Supported invitation/existing-identity path yields correct membership; ownership handover and session revoke work | Wrong-tenant acceptance, replay, expired invitation, duplicate email/subject, revoked membership and failed delivery handled honestly | Real supported IdP and delivery integration where implemented; API negatives |
| EJ-08 | Adapter qualification; EP-09–12 | Configure/test/discover/scope/read or ingest; known result/evidence; sync/refresh/rotate/recover using real selected system | Bad credentials/TLS, unauthorized objects, outage, timeout, throttling, schema drift, write attempts, upstream permission revocation and secret leakage | Separate source-version/auth qualification record for PostgreSQL, Workspace and every new advertised adapter |
| EJ-09 | Governed Knowledge + Data answer and report; EP-02/04–12 | Ask a business question requiring an SOP and known database result, inspect both evidence types, save/run/export with appropriate permission | Unauthorized facts never enter model context; injected document instructions cannot elevate access; missing/partial evidence disclosed; expired access and stale source invalidate rerun/export; no fabricated business numbers | Instrumented real model/source journey, expected-value reconciliation and cross-tenant negatives |
| EJ-10 | Provider business lifecycle; EP-13A/B | Provision tenant/owner, inspect readiness/usage, review service-plan change, suspend/reactivate and follow approved offboarding steps | Tenant admin denied platform APIs; stale view/wrong tenant cannot mutate; unavailable usage not zero; duplicate action safe; service enforcement matches displayed state | Integrated provider/customer browser sessions, audit, entitlement/tenant guard tests |
| EJ-11 | Bounded support and operator authority; EP-13C | Request/approve scoped time-limited support, view active delegation, use permitted capability, revoke and inspect audit | Metadata operator cannot read content; scope/ceiling violations fail; expired/revoked grant fails in active session; operator protections upheld | Real IdP support journey + direct API/tenant/content denial tests |
| EJ-12 | All-screen consistency and accessibility; EP-01–03 and every new UI | Every UI inventory row reviewed using shared tokens/components/states; keyboard task completion and mobile readability | Empty/error/denied/partial/conflict states; hidden/clipped actions, contrast/focus failures, dead destinations and accidental writes block acceptance | Surface-by-surface record, screenshots, manual keyboard/assistive-tech review and automated accessibility findings |
| EJ-13 | Install, upgrade and recovery; EP-09–14 | Supported packaged candidate installs with qualified drivers/dependencies, upgrades existing tenant/policy/source state, backup/restore and recovery work | Registry-blocked/offline gates where supported, missing driver/secret, failed migration/sync restart and rollback limitations handled explicitly | Applicable REL1/FULL results and live changed-path operator evidence tied to artifact |
| EJ-14 | Independent handoff; EP-14 | A new operator/admin follows repo docs for changed setup, rotation, failed-sync recovery, support revocation and offboarding; limitations and support matrix are correct | No undocumented credentials, commands or expert-only rescue; unresolved adapter/live gates are visible | Independent walkthrough, accepted documentation delta and product-owner cross-plane review |

## 3. Non-negotiable failure conditions

Block the relevant package/release for accidental access/system writes; missing server authorization; cross-tenant or provider-to-customer content leakage; unauthorized evidence reaching the model; database write execution; secrets exposed in UI/logs/evidence; silent lost policy updates; fabricated success or unsupported integration claims; unusable critical controls; or a release advertised beyond its verified support matrix. No visual exception can waive an authorization boundary.

Sample multiple permission-change timings: before opening a page, after preview, during an in-flight job and before report/export consumption. Check both the normal UI and direct API calls. Inspect tenant-facing errors for non-enumeration; a 403 that reveals an unauthorized source name is not sufficient.

## 4. Evidence and acceptance record

Each package record must contain:

1. Package, ER/EJ/UI IDs, named implementer and independent reviewer, scope and decisions.
2. Exact candidate/head, actual tested tree/build inputs, artifact/configuration/provider identity and test commands/results.
3. Positive and negative cases, request/persistence/audit proof, fixture versus live labels, screenshot review findings and accessibility results.
4. Handoff/migration/support-matrix changes, defects/limitations and unresolved environment gates.
5. Reviewer disposition and owner acceptance/merge authority. Mark unexecuted gates **NOT RUN**, unavailable prerequisites **BLOCKED**, and approved future scope **DEFERRED with owner and reason**.

Store durable summaries and evidence pointers in the repo; retain approved synthetic screenshots/traces long enough for review and handoff. Record retention/expiry and preserve the accepted evidence before short-lived CI artifacts expire. Avoid committing bulky recordings, logs or any customer data. Later code does not automatically inherit older live proof; record why reused evidence is still applicable.

Use focused/FAST checks while implementing, STANDARD for completed packages and FULL plus scoped live gates on a frozen release/acceptance candidate under [CI_TIERS](../CI_TIERS.md). Do not rerun unrelated historical gates for every visual edit. Repeat affected checks when source, configuration, workflow, artifact or merge inputs invalidate their evidence. Documentation-only updates require document checks; this plan does not turn screenshots into a substitute for live acceptance.

## 5. Completion and follow-up

The programme closes when the requirement map and every applicable UI row have accepted evidence, all advertised adapters have real qualification, the cross-plane journeys pass, and the product owner accepts the experience and handoff. An unresolved first ERP/warehouse choice remains open work until resolved or explicitly deferred; a label change cannot close it.

At the first customer pilot after release, the product owner and operator review completion time, failed setup/sync cases, accidental-change reports, support burden and any permission incidents. Record concrete corrective packages in the repo. Do not claim a production SLA, universal connector coverage, or accessibility certification from this planning document or a single automated run.
