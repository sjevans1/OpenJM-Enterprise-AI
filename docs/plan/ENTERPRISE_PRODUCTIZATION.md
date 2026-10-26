# Enterprise product depth and consistent UI

Status: **PLANNED — implementation and acceptance pending.** Recorded 15 October 2026 against `main@b45ca8dad5fa79d12eba20fefd4d7d6c76df24b9`.

This programme follows the owner's manual review: the product must complete practical business tasks, and the premium interaction quality must extend across every UI. This document is the execution backlog; the [UI quality standard](../design/UI_QUALITY_STANDARD.md) defines the experience contract, and the [journey acceptance contract](../qa/ENTERPRISE_PRODUCT_ACCEPTANCE.md) defines proof of completion. [PLAN](../PLAN.md) is the current entry point.

The accepted VS/BV/M/INF/E/REL1 work remains the foundation. Historical passes in the [acceptance matrix](../ACCEPTANCE_MATRIX.md) retain their original scope and candidate identity. They do not accept the new programme. The initial workflow correction, [PR #109](https://github.com/sjevans1/OpenJM-Enterprise-AI/pull/109), was **merged on 2026-10-14** at `bc8f02e5662378a6c954871c042233c635e43e63` from reviewed head `e3854d1e84230afb639cef4e3ed53a2ad515a203`. Its [review](https://github.com/sjevans1/OpenJM-Enterprise-AI/blob/e3854d1e84230afb639cef4e3ed53a2ad515a203/docs/quality/2026-10-14-product-workflow-review.md) explains the reproduced defects and remaining gaps. The post-merge STANDARD result and any residual EP-00 reconciliation must be recorded before marking EP-00 accepted; neither this planning change nor historical evidence accepts EP-01–14.

## 1. Product outcome and boundaries

A company connects its supported business systems and brings policies, SOPs and documents into Knowledge. Employees ask questions across Knowledge and Data, receive traceable answers, and create governed reports. Tenant administrators control identities, ownership, scope, classification and lifecycle. OpenJM operates the service across tenants through a separate provider capability boundary.

“Premium” means that users can understand the current state, complete their task, review consequential changes, recover from failure, and trust the result. Visual consistency, accessibility and operational depth are acceptance requirements throughout delivery.

| Plane | Daily journey | Authority boundary |
| --- | --- | --- |
| Employee and steward workspace | Sign in → find permitted content/data → ask → inspect evidence → save/share a governed result → recover from stale or unavailable evidence | Server-resolved tenant membership and resource permissions; a hidden UI control is never the authorization check |
| Company administration | Set up organization → onboard people → connect and govern information → validate access → activate → monitor → rotate/reassign/retire | Tenant-scoped administrative and stewardship capabilities; administration does not automatically grant all content access |
| OpenJM Control Center | Provision tenant → establish owner and service plan → monitor health/usage → resolve incidents with bounded delegation → suspend/reactivate/offboard | Explicit platform capabilities; metadata oversight does not imply access to customer prompts, answers, documents or rows |

Keep one application with clearly separated navigation and role-aware shells unless an ADR establishes a concrete need to split it. The active tenant and provider context must be obvious on every applicable page. Do not introduce a casual “view as customer” bypass.

### Decisions carried forward

- Keep explicit General, Knowledge, Data and Hybrid modes, authorization before evidence/model context, read-only bounded SQL, auditable writes and fail-closed behavior.
- Preserve the accepted [v1 policy decisions](PRODUCT_DECISIONS.md): `public`/`internal` with `tenant_visible=true` shares **within the tenant**; source-level classification plus object narrowing is the current Data policy unit. Row/column classification and implicit mode routing remain separate decisions.
- Policy selection before a new Knowledge upload is in scope. A quarantine/publication boundary requires an explicit additive lifecycle contract; it must not silently reclassify existing content or change legacy upload defaults. Resolve this in EP-06 before implementation.
- Support PostgreSQL and SQLite as implemented today; distinguish SQLite development/demo use from customer production integration qualification. The implemented connector is Workspace. SQL Server, ERP, other warehouses and MinIO/S3 business-content access are planned capabilities. MinIO deployment storage is not an ingestion adapter.
- “Microsoft SQL Server” is a defined database target. “Microsoft server” is not a protocol: Windows file shares, SharePoint and directory services require separately named contracts and packages when selected.
- Preserve REL1 installation, offline packaging, upgrade/recovery and zero-tribal-knowledge handoff. Requalify affected paths when new drivers, dependencies or migrations change them.
- Rahkia production cutover remains with the later specialist phase. Use the supported model gateway for this programme's real answer-quality gates. Workspace remains an independent project.

## 2. Requirements and ownership

| ID | Required outcome | Delivery packages | Acceptance journeys |
| --- | --- | --- | --- |
| ER-01 | One coherent visual and interaction system on every shipped surface, including failure states and mobile | EP-01–03; all later UI uses the same components | EJ-01, EJ-12, surface inventory |
| ER-02 | No access, sharing, activation or destructive change caused merely by changing a field; reviewed commits and durable feedback | EP-00–04, EP-13 | EJ-02, EJ-03, EJ-10 |
| ER-03 | Tenant-wide administrative inventory with correctly scoped lifecycle authority | EP-04–05 | EJ-03, EJ-06 |
| ER-04 | Governed Knowledge intake with permissions fixed before retrieval becomes possible | EP-06 | EJ-04, EJ-09 |
| ER-05 | A company can reach its first permitted Knowledge + Data answer through a resumable setup journey | EP-07–08 | EJ-05, EJ-07, EJ-09 |
| ER-06 | Real source adapters with scoped reads, credential lifecycle, synchronization/recovery and honest support claims | EP-09–12 | EJ-08, EJ-09, EJ-13 |
| ER-07 | Complete provider operations with explicit tenant, capability, audit and support-delegation boundaries | EP-13 | EJ-10, EJ-11 |
| ER-08 | Answers, evidence, artifacts and reports remain permission-correct after revocation, source changes and failures | EP-02, EP-04–12 | EJ-06, EJ-09 |
| ER-09 | The accepted product can be installed, upgraded, recovered and handed over without undocumented knowledge | Every package's handoff delta; EP-14 | EJ-13, EJ-14 |

The product owner accepts business behavior and material scope decisions. The implementation owner delivers bounded packages. A reviewer other than the implementer assesses returned evidence; the tenant identity/data owner supplies approved test environments and validates source permissions; the release/operator owner qualifies packaging and recovery. Assign named people when opening each package. CI supplies evidence, not product-owner approval.

## 3. Delivery sequence

| Milestone | Packages | Exit condition |
| --- | --- | --- |
| M0 — controlled baseline | EP-00 | Accepted workflow correction recorded; current implementation and outstanding decisions reconciled |
| M1 — consistent existing product | EP-01 → EP-02 → EP-03 | All existing UI inventory rows reviewed at desktop/mobile; common actions/states/components; no orphaned old-style workflows |
| M2 — usable company governance | EP-04 → EP-05/06; EP-07 and EP-09 before EP-08 | Tenant catalog, concurrency, policy-first intake, common source lifecycle and identity/setup journeys pass on existing supported sources |
| M3 — real enterprise integrations | EP-09 → EP-10/11/12 | Each advertised adapter passes its own real-system contract; unqualified adapters remain unavailable |
| M4 — complete provider operations | EP-13, after EP-03/04 | Tenant lifecycle, entitlements and bounded support complete their separate capability-specific journeys |
| M5 — release and handoff | EP-14 | Frozen candidate passes required journeys, security negatives, operational gates and product review |

This is dependency order, not a calendar estimate. EP-13 can be scheduled before individual adapters once its prerequisites are accepted; no requirement to finish every adapter before improving provider operations. EP-05 and EP-06 share policy/lifecycle seams and should normally be sequenced to avoid conflicts. Only explicitly authorized independent work proceeds while a candidate is frozen. No new concurrency or automated supervisor is enabled by this plan.

## 4. Bounded work packages

All packages below start **PLANNED**. Acceptance evidence is **NOT RUN** for this programme. Split the named subpackages into separate reviewable changes where indicated; do not turn this document into one large implementation PR. Each package includes the shared UI contract and handoff delta.

### EP-00 — establish the correction baseline

- **Depends on:** accepted [PR #109](https://github.com/sjevans1/OpenJM-Enterprise-AI/pull/109), merged at `bc8f02e5662378a6c954871c042233c635e43e63`. Record the exact post-merge STANDARD result before the EP-00 closeout.
- **Deliver:** record the accepted commit; reconcile any base movement; inventory remaining immediate mutations, misleading labels, partial failures and permission assumptions. Keep the role/policy draft-save-review behavior and mobile/readability corrections as the reference.
- **Pass:** selection and cancellation cause zero writes; refused writes retain drafts; successful writes refresh from persisted state. Confirm UI and backend authorization cases covered by #109 remain valid on the accepted base.
- **Evidence / scope:** reuse exact applicable #109 evidence; rerun only invalidated checks. Do not declare its synthetic browser fixtures to be real SSO/model acceptance. No merge without owner authorization.

### EP-01 — shared experience foundation and reference journeys

- **Depends on:** EP-00. **Owns:** all three planes; ER-01/02.
- **Deliver:** semantic design tokens, reusable accessible primitives, a component/state gallery, and three integrated reference views: employee answer + evidence, tenant user/policy review, provider tenant detail + reviewed action. Start from the existing navy/teal/light workspace and #109 behavior. **Mandatory repository/library discovery:** evaluate Watermelon UI and at least two maintained accessible alternatives (for example Radix, React Aria, Base UI and shadcn/ui), alongside adapting existing code. Decide build/adapt/reuse with a short ADR covering accessibility, maintenance, integration effort, licensing, dependency cost, offline compatibility and visual fit; no library chosen solely for attractive screenshots.
- **Implementation seams:** `frontend/src/styles.css`, `App.tsx`, `AdminPanel.tsx`, `OperatorConsole.tsx`; reuse/refine `ConfirmAction.tsx` and `PolicyEditor.tsx` after #109 acceptance. Proposed shared code under `frontend/src/ui/`; extract feature views incrementally.
- **Pass:** one implementation for buttons, fields, statuses, dialogs and review/save state; normal/loading/empty/error/denied/conflict states in the gallery; keyboard/mobile checks; product-owner review of the three reference journeys before broad migration.
- **Handoff:** component usage guide, token definitions, approved screenshot references, component-source evaluation/ADR and recorded exceptions. **Excludes:** backend authority changes and wholesale application rewrite.

#### EP-01 component reuse decision gate

Before creating a new primitive, review reusable projects rather than rebuilding a design system from scratch. The starting shortlist (not a preapproved dependency) is:

- [Watermelon UI](https://ui.watermelon.sh/) and its [open-source platform](https://github.com/WatermelonCorp/watermelon-platform) / registry for editable components, blocks and dashboard references.
- [Radix Primitives](https://www.radix-ui.com/primitives), [React Aria Components](https://react-spectrum.adobe.com/react-aria/), [Base UI](https://base-ui.com/) and [shadcn/ui](https://ui.shadcn.com/) as potential accessible primitive/composition layers.
- Existing OpenJM components, especially the accepted `ConfirmAction` and `PolicyEditor`, for minimal-dependency adaptation.

For each shortlisted option, identify repository URL/commit or package version, license(s) and any attribution obligations, maintenance/security posture, keyboard/focus/ARIA behavior, theming/token fit, React/Vite/CSS compatibility, package size/dependency graph, offline packaging and long-term ownership. A public showcase is not itself an accessibility or security qualification. Confirm whether a registry asset is a reusable source component, a decorative demo, or a dependency that must be maintained.

Prototype at least one consequential interaction (reviewed role/policy change) and one information-dense interaction (searchable/paginated tenant list or evidence table) with realistic error/denial and narrow-screen states. Compare the rendered prototypes to the accepted OpenJM navy/teal direction and document the choice: **reuse**, **adapt** or **build**, with explicit rejection reasons. Prefer source-owned, composable components where appropriate; do not install entire UI kits or animation stacks merely because a demo looks polished. Record screenshots, keyboard findings, tested versions and the owner-approved component strategy before migrating EP-02/03 surfaces.

### EP-02 — complete the employee workspace migration

- **Depends on:** EP-01. **Owns:** UI inventory UI-00–06; ER-01/08.
- **Deliver in bounded waves:** (a) authentication, navigation, conversation and evidence/artifacts; (b) Knowledge, Data and Saved Reports. Apply the approved hierarchy, filters, form/actions, pagination, detail views, responsive behavior and state patterns. Explain source scope, evidence freshness, partial coverage and blocked actions in business language. Preserve useful drafts and selected context through recoverable failures.
- **Pass:** EJ-01/09/12 on every affected surface; no fake success, unusable future navigation or undisclosed fallback from missing business evidence to a general answer; reports and exports reauthorize, and evidence links stay meaningful. Capture long names, dense tables and empty/failure cases as well as populated screens.
- **Handoff:** update user help and the inventory evidence column. **Excludes:** new adapters, arbitrary theme changes and undocumented changes to mode semantics.

### EP-03 — complete tenant-admin and provider UI migration

- **Depends on:** EP-01/02. **Owns:** UI-07–14; ER-01/02/07.
- **Deliver in bounded waves:** (a) people, departments, groups, stewards, policies, usage and activity; (b) Connectors and Operations; (c) Control Center overview, tenant list/detail, usage, audit, diagnostics, security and releases. Reuse common filtering/pagination/detail/action patterns while keeping tenant and provider authority distinct.
- **Pass:** EJ-02/12; every existing view has truthful last-refresh/error/empty states, discoverable capability-aware controls, reviewed consequential actions, keyboard access and mobile navigation. Existing monitoring is fully usable even where later management features remain planned.
- **Handoff:** admin/operator guidance and complete inventory evidence. **Excludes:** pretending monitoring-only panels already implement EP-13 management.

### EP-04 — versioned policy writes and effective-access inspection

- **Depends on:** EP-00/01. **Owns:** tenant governance; ER-02/03/08.
- **Deliver:** optimistic concurrency for policy and relevant membership changes, atomic policy+audit writes, and an authorized effective-access explanation using the **same server evaluator** as retrieval/execution. Review old/new values and affected scope; stale saves display a conflict and require explicit reconciliation. Define compatibility for existing API clients before making revision tokens mandatory.
- **Implementation seams:** `backend/app/services/access_governance.py`, `document_policy.py`, `api/admin.py`, `models.py`, migrations and policy UI.
- **Pass:** two administrators cannot silently overwrite changes; forged/stale tokens and cross-tenant subjects fail; inspection reveals neither inaccessible resource identities nor content and cannot impersonate a user. Explain group/department/classification and upstream-connector restrictions accurately. Recheck access at execution/commit, including a revocation between preview and save.
- **Handoff:** API/versioning contract, migration/rollback limits, policy precedence examples. **Excludes:** row/column policy and bulk grants without a separate decision.

### EP-05 — tenant source and document lifecycle

- **Depends on:** EP-04 and applicable EP-02/03 surfaces. **Owns:** tenant admin/steward; ER-03/08.
- **Deliver:** paginated tenant administrative inventory, resource detail, responsible owner/steward, capability-specific connection editing/credential rotation, ownership transfer, refresh/reindex, disable/retire and deletion with downstream report/evidence impact. Separate administrative metadata from content permission. Define native-source and connector-owned-resource authority explicitly.
- **Implementation seams:** `api/data.py`, `api/knowledge.py`, `api/connectors.py`, `services/data_sources.py`, `document_lifecycle.py`, `credentials.py`, connector services and source/report scope guards.
- **Pass:** company admins can perform authorized lifecycle tasks without being the original creator; ordinary users cannot enumerate or mutate others' resources; no tenant crossover; stale in-flight jobs cannot republish retired material; revocation stops future retrieval, runs and exports. Unknown network outcomes are reconciled before retry to avoid duplicate resources or audit actions.
- **Handoff:** ownership and credential lifecycle runbooks, retirement/retention semantics, migration/backfill and recovery evidence. **Excludes:** removing ownership predicates without a replacement authorization contract, or deleting the external business database.

### EP-06 — Knowledge intake with policy before publication

- **Depends on:** EP-04/05. **Owns:** employee upload, steward review, tenant administration; ER-04/08.
- **Deliver:** select files → validate format/limits → choose classification, department and audience → review → upload/index → explicit ready/failed status. Show tenant-wide sharing clearly. Define additive intake/publication states and who may publish; where approval is required, keep quarantined content out of retrieval until authorized publication. Direct API paths must enforce the same rules.
- **Decision gate:** document compatibility with existing `internal`/tenant-visible defaults, existing clients and already indexed documents; obtain owner acceptance for any change to that contract. Server-side policy assignment must precede index exposure, including chunks, previews and retries.
- **Pass:** a restricted test fact is never retrievable by an unauthorized persona during upload, indexing, publication, replacement, failure or retry. Partial batch failures have per-file recovery without duplicate documents; replacement/reindex preserves or explicitly reviews policy; users see real progress and useful errors.
- **Handoff:** intake state diagram/contract, supported formats, lifecycle/retention rules and user guidance. **Excludes:** silently changing all historical documents or claiming upstream file ACLs exist for manual uploads.

### EP-07 — identity onboarding and offboarding

- **Depends on:** EP-03/04. **Owns:** tenant admin, invited user; ER-02/05.
- **Deliver:** choose the deployment-appropriate path: existing OIDC identity membership or an explicitly supported invitation/directory integration. For an implemented invitation path, model pending/accepted/expired/revoked, verified tenant binding, bounded resend and duplicate-safe acceptance. Distinguish email from the identity-provider subject. Offboarding includes sessions, group membership, ownership handover and audit.
- **Pass:** real OIDC sign-in establishes only the intended tenant/role; invitation replay, expiry, cross-tenant acceptance, subject/email collision and revoked membership fail safely; no invitation is reported sent unless a real delivery service performed it. Preview and confirm all access grants.
- **Handoff:** identity-provider setup, delivery/expiry operations and recovery. **Excludes:** pretending a membership POST creates an account, universal SCIM support or a new identity provider without a bounded integration decision.

### EP-08 — guided company setup and first value

- **Depends on:** EP-04–07 and EP-09's common source contract. **Owns:** tenant admin + employee; ER-05.
- **Deliver:** resumable company setup: organization/owner → identity and initial groups → supported source → credentials/test/discovery → scope and policy → Knowledge intake → access validation → explicit activation → first grounded question. Reuse normal management screens and APIs; setup must not create a parallel configuration system. Record completed steps from server state and explain unmet prerequisites.
- **Pass:** with prepared fixtures and credentials, a fresh tenant reaches a permitted Data + Knowledge/Hybrid answer without shell, database editing or an engineer. A negative persona is denied; interrupted/repeated setup does not duplicate tenants, memberships, documents or connections. Capture completion time and friction under the acceptance profile.
- **Handoff:** tenant-admin quick start and first-value walkthrough. **Excludes:** exposing unsupported adapters as selectable working integrations.

### EP-09 — common source contract and existing-adapter lifecycle

- **Depends on:** EP-04/05; use EP-01 components. **Owns:** Data/Knowledge/Connectors; ER-06/08.
- **Deliver:** a capability registry and one setup presentation over native SQL and connector paths without erasing their distinct semantics. Specify configure, secret-reference/rotation, test, discover, scoped read, policy intersection, readiness, incremental sync/cursor where applicable, cancellation/timeouts, retry/backoff, disable, retirement and audit. Distinguish federated live SQL from ingested documents; show last successful sync/schema refresh and current coverage truthfully.
- **Implementation seams:** `core/connectors.py`, `services/connectors/base.py`, `service.py`, `sync.py`, `ingest.py`, `data_sources.py`, structured planner/executor/SQL policy and existing APIs.
- **Pass:** PostgreSQL and Workspace complete the common lifecycle; SQLite retains its supported development behavior. Revoked upstream credentials/permissions, provider outages, schema drift and sync restart remain fail-closed. Secrets never appear in list/detail/evidence/error/log output. Permission changes invalidate cached evidence and affected runs according to a documented freshness contract.
- **Handoff:** adapter authoring/qualification contract, supported-version/auth matrix, source state model and a licensed dependency/offline inventory. **Excludes:** a universal adapter implemented as labels or a rewrite of working connectors solely for abstraction.

### EP-10 — Microsoft SQL Server adapter

- **Depends on:** EP-09. **Owns:** Data; ER-06/08.
- **Deliver:** explicitly supported server/version/auth combinations, driver packaging and TLS configuration, schema discovery, qualified identifiers, dialect-aware read-only execution and bounded query cancellation. Validate query policy for SQL Server constructs; do not reuse PostgreSQL-specific checks blindly.
- **Pass:** EJ-08 against a real isolated SQL Server instance, known-result queries, unauthorized objects, mutating/multi-statement statements, timeout, outage, credential rotation and schema change. Test supported Linux and Windows/WSL2 installation paths and offline driver distribution/licensing before advertising them.
- **Handoff:** DBA setup, least-privilege account instructions, version/auth matrix and support diagnostics. **Excludes:** “all Microsoft servers,” untested Windows-integrated authentication or arbitrary SQL execution.

### EP-11 — MinIO / S3-compatible Knowledge connector

- **Depends on:** EP-06/09. **Owns:** Knowledge/Connectors; ER-04/06/08.
- **Deliver:** bucket/prefix-scoped discovery, allowed content types/limits, encrypted credential lifecycle, stable object identity/version or ETag, incremental ingestion, update/delete reconciliation and checkpoints. Decide and document upstream permission mapping: a service credential's reach must not automatically become every user's reach.
- **Pass:** real MinIO qualification; separately qualify any advertised S3 provider/version/profile. Unauthorized bucket/prefix access and unintended permission widening fail; retries do not duplicate objects; removed/changed objects and revoked credentials stop retrieval; large/broken objects have bounded failure and recovery.
- **Handoff:** sync/retention/deletion contract, access-policy mapping and runbook. **Excludes:** treating the app's storage bucket as an existing business-content connector or claiming universal S3 compatibility from one fixture.

### EP-12 — first named ERP and first named warehouse

- **Depends on:** EP-09; EP-10 only if the selected integration relies on it. **Owns:** Data and/or Knowledge; ER-06/08.
- **Split:** EP-12A selects and qualifies one ERP; EP-12B selects and qualifies one warehouse. Each needs its own integration contract and acceptance record.
- **Readiness gate:** product/data owner names the actual vendor, version, deployment, business entities, access method, approved test system and read-only credentials. Compare native API, documented read replica and existing mature adapter; record licensing, rate limits, tenancy, semantic mapping and support costs. Selection stays **UNRESOLVED** until that record exists; other packages can proceed.
- **Deliver / pass:** one business question with reconciled expected figures, source provenance and policy-correct evidence; entity/schema discovery, incremental reads where applicable, throttling, outage/retry, permission revocation and schema/version change. A successful raw SQL connection is not by itself an ERP semantic integration.
- **Handoff:** supported entity/version/auth matrix, data-owner reconciliation and recovery instructions. **Excludes:** “all ERPs,” “all warehouses,” financial write-back or vendor claims without live evidence.

### EP-13 — full OpenJM provider journeys

- **Depends on:** EP-03/04; EP-07 where owner identity onboarding is used. **Owns:** provider plane; ER-02/07.
- **EP-13A tenant lifecycle:** create tenant, establish first owner, see readiness, suspend/reactivate with effect preview, and explicit offboarding/retention/export workflow. Preserve customer data on suspension; deletion/retention needs an approved contract. Build over `api/platform.py` and platform services where available; add missing audited contracts deliberately.
- **EP-13B commercial/service management:** plan assignment, entitlement/capacity limits, usage period/freshness and reviewed changes, with customer-admin read visibility and enforcement matching the displayed plan. Existing `platform_entitlements.py` is aggregate metadata; do not assume edit endpoints exist. Add narrowly authorized mutation APIs and audit where needed. Billing/payment collection is a separate business decision.
- **EP-13C operators and support:** capability-based operator administration, tenant-approved time-limited support requests/grants, purpose and resource scope, visible active delegation, expiry/revocation, audit and useful incident diagnostics. Preserve the accepted support ceiling/content-scope contract and last-operator protections.
- **Pass:** EJ-10/11; tenant admins cannot call provider APIs; provider monitoring cannot read customer content; expired/revoked support fails mid-journey; commercial/service state changes are consistent, reviewed, duplicate-safe and audited. Stale fleet detail cannot execute an action against the wrong tenant.
- **Handoff:** provider runbooks, commercial-state semantics, incident/support playbook and offboarding data contract. **Excludes:** unrestricted impersonation, provider content access by default and a billing engine disguised as a usage screen.

### EP-14 — integrated qualification and handoff

- **Depends on:** accepted in-scope packages, including all UI inventory rows. Named adapters cannot be silently dropped to close this programme; unresolved vendor selection remains explicitly open unless the owner changes scope.
- **Deliver:** frozen candidate and support matrix, complete requirement/journey evidence, desktop/mobile visual review across all planes, live tenant-isolation and model-grounded-answer checks, upgrades from the accepted baseline, backup/restore and affected online/offline installation. Refresh user/admin/provider documentation and walkthroughs from the accepted product.
- **Pass:** all required [acceptance journeys](../qa/ENTERPRISE_PRODUCT_ACCEPTANCE.md) pass or carry an explicit owner-approved scoped deferral; no blocking UX or authorization defects, no unsupported integration claims, and an independent operator completes the changed setup/lifecycle using repository instructions. The product owner reviews the real workflows before release.
- **Handoff:** acceptance manifest with candidate/artifact/configuration identity, evidence retention, supported versions, limitations, operator ownership and known-issue response. **Excludes:** inherited live PASS from an older artifact, automatic deployment and Rahkia specialist qualification.

## 5. Decisions and risks to resolve at package entry

| Decision / risk | Owner role | Resolve by | Default until resolved |
| --- | --- | --- | --- |
| Shared primitives: adapt existing code or add a maintained accessible library | UI implementation owner + reviewer | EP-01 | Preserve current visual identity; no new UI dependency assumed |
| Effective-access metadata visibility and policy revision compatibility | Security/governance reviewer + product owner | EP-04 | Existing server checks remain authoritative; no new enumeration |
| Quarantine/publication and legacy upload semantics | Product owner + data-governance owner | EP-06 | Existing content and defaults unchanged; new flow cannot claim quarantine without enforcement |
| Identity invitation/directory/delivery mechanism per deployment | Tenant identity owner | EP-07 | Existing-subject membership only; no simulated email/account creation |
| Versions/auth/driver distribution for SQL Server; bucket permission mapping for MinIO/S3 | Integration owner + data owner | EP-10/11 | Only qualified combinations appear as supported |
| Exact ERP and warehouse targets/test systems | Product owner + customer data owner | EP-12A/B | Explicitly unselected and unqualified; do not block unrelated packages |
| Provider commercial authority, support request approval, offboarding retention | Product owner + operator/security owner | EP-13 | Preserve current authority boundaries; no new billing/deletion implication |
| Network/model latency and large catalog workload | Performance/release owner | EP-09/14 | Use measured acceptance profile; do not publish an unmeasured SLA |

## 6. Execution, verification and review contract

Use the repository's [CI tiers](../CI_TIERS.md), [tool-neutral CI workflow](../CI_WORKFLOW.md), [contribution guide](../../CONTRIBUTING.md) and [Developer Handoff](../DEVELOPER_HANDOFF.md). The product UI quality standard and acceptance contract in this repository are authoritative. Do not silently replace security gates, model-serving routes or release constraints; propose and review any workflow-policy change independently.

Every implementation packet must specify: package/requirement/journey IDs; accepted base and dependencies; named implementation/review/data owners; allowed scope and API contract; selected UI inventory rows and states; migration/compatibility effects; focused tests; live environments; handoff delta; evidence and stop boundary. No packet is ready if its material decisions above are unresolved.

Use focused local verification during implementation, FAST on the reviewable head, STANDARD at completed package qualification, and FULL plus applicable live checks at the frozen acceptance boundary. Consult the actual workflow and record which checks ran; a fixture browser pass is not a live source/identity/model pass. Documentation-only planning needs link/consistency checks, not an expensive release build. Reuse evidence only while the candidate inputs and tested risk remain applicable.

For UI packages, code review must check actual state/permission wiring and a human must inspect rendered reference and affected screens. Screenshot output alone is not visual acceptance. For security-sensitive behavior, test through the API as well as the UI. Keep synthetic fixtures free of customer secrets; do not upload customer databases, prompts or credentials as CI evidence.

Record each package in a small execution ledger with `PLANNED`, `IN PROGRESS`, `AWAITING REVIEW`, `ACCEPTED` or `BLOCKED`, a PR/head, evidence links and the exact next step. Begin with EP-00. Update this plan only for material product/architecture changes, not to redefine a failed acceptance criterion. Stop after bounded failed repair attempts when the remaining cause is architectural or needs unavailable live access; describe the blocker and preserve the reviewable work.

Completion requires business capability, consistent experience, authorization correctness, live integration proof and maintainable handoff. Merge and deployment remain explicit owner decisions.
