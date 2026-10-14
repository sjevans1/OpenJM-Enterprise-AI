# Product workflow review — 14 October 2026

Reviewed baseline: `b45ca8dad5fa79d12eba20fefd4d7d6c76df24b9`.

The target product is a governed company workspace: business databases and document knowledge feed permission-aware questions and reports; company administrators manage their own people and information policies; OpenJM operators manage service oversight without gaining customer-content access by implication.

The backend contains substantial functionality, but several interfaces do not complete practical business tasks. Passing implementation tests alone did not establish usable workflows. This correction addresses reproduced gaps without replacing the authorization or inference architecture.

## Corrections in this change

| Finding | Resulting behavior |
|---|---|
| User-role dropdown immediately PATCHed access | Selection is a draft; Save role shows the named person and old/new role; Cancel sends no write. Errors preserve the draft. |
| Revoke/restore access, sessions, group removals and steward changes applied immediately | Named confirmation with consequences, request lock, error feedback and completion feedback. Group additions and user provisioning also receive review. |
| Re-provisioning an existing member could change their role or restore revoked access | Identical active provisioning remains idempotent; role/status changes require their dedicated guarded actions. |
| Admin policy page was only a list of labels | Classification, department, workspace sharing and allowed groups can be reviewed and saved through existing policy APIs. Current grants are returned so edits cannot unknowingly erase them. |
| A department could be assigned but never explicitly cleared | Additive `clear_department` flag; old omitted/null semantics retained; conflicting assign+clear refused; authorization and audit remain in the existing services. |
| Admin drafts vanished on navigation | Leaving Administration requires a discard decision; browser navigation warns; section switching is disabled while a role/policy draft is open. |
| User provisioning was labeled as email despite requiring the external identity subject | Exact sign-in subject and separate optional contact email; Viewer default; no claim that an email invitation or identity-provider account is created. |
| Usage UI always claimed plan data was unavailable | Configured plan name, subscription state and available allowance are rendered; unconfigured plans are identified accurately. |
| Missing workspace activity view | Latest 50 governance events are accessible in Administration. |
| Source setup enabled a connection immediately and could look unsaved after verification failed | New UI-created connections start disabled; the form clears once creation succeeds; test/schema failures direct the user to retry on that saved source. Explicit enable confirmation follows policy review. |
| Source/document/report removals lacked review | Named confirmations explain effects on evidence and reports; credential removal does not imply deleting the external database. |
| Controls were shown without checking their write/delete permissions | Knowledge and Data management controls reflect server-resolved permissions; backend authorization remains authoritative. |
| Database copy referred to an internal development slice | Product guidance explains supported engines and Data/Hybrid usage; unsupported integrations are stated honestly. |
| Operator views stopped at the first page and gave no freshness cue | Fleet, usage and audit pagination; manual refresh; last-successful-refresh time; stale asynchronous results cannot overwrite newer views. |
| Narrow-screen CSS hid the entire navigation and admin tab labels | Mobile navigation control/drawer; readable administration tabs; tables scroll inside their container. |

## Integration and product-depth gaps that remain

These are separate capabilities, not fixed by adding UI labels. This change must not be described as completing the entire enterprise vision.

1. **SQL Server / ERP / warehouses / object stores.** The direct SQL path currently supports PostgreSQL and SQLite. The connector implementation present in this baseline is Workspace. SQL Server, specific ERPs, other warehouses and MinIO/S3 need actual adapters, credential lifecycle, discovery, scoped reads, policy propagation, failure recovery and acceptance against the real source. MinIO as deployment storage is not a business-content connector.
2. **Unified onboarding.** A company-wide Sources setup journey should select a real supported adapter, discover resources, define scope/classification/groups, validate with an authorized and unauthorized persona, and explicitly activate. The current Knowledge upload retains its existing default classification/sharing behavior; pre-ingestion policy selection/quarantine is not added here.
3. **Tenant-wide source lifecycle.** Admins can now inspect and edit tenant policies, but native source connection-management/list routes remain ownership-scoped. A full company catalog with scoped admin stewardship, connection editing/rotation, ownership transfer, and document lifecycle management is still needed. Do not widen those routes by removing the ownership filter without a separate authorization design and negative tests.
4. **OpenJM management depth.** The Control Center remains primarily a monitoring interface. Existing platform tenant/operator/support/entitlement APIs are not yet exposed as complete provisioning, support-delegation and commercial-management journeys. Capability-specific UI and explicit reviewed changes are required when those workflows are added.
5. **Policy concurrency and effective-access inspection.** The editor preserves the loaded grants but does not introduce optimistic concurrency/ETags, row/column-level policy, bulk operations or an effective-permissions simulator. Concurrent administrators can still overwrite each other's changes; a subsequent policy-versioning package should handle this explicitly.
6. **Identity onboarding.** Adding a subject establishes membership, not an email invitation or account creation in Keycloak. A real invitation/directory integration requires an intentional identity-provider workflow.

## Verification

- Existing and new frontend tests: 114 passed locally before browser-harness additions; TypeScript and production build passed.
- New tests cover no write on selection/cancel; role confirmation; refusal retaining draft; duplicate-request suppression; keyboard focus; policy grant preservation; configured plan rendering; disabled source creation; source verification failure recovery; operator pagination and stale-response handling.
- Backend regressions cover full tenant-scoped policy projection, explicit department clearing, preserved groups, audit records, authority/cross-tenant denials, and the re-provisioning bypass. Use the hosted Python 3.11 suite for execution.
- Three Chromium browser journeys capture screenshots for administration, role review, policy editing, data setup and mobile navigation. API responses are synthetic fixtures: these prove interface behavior and layout, not live authentication, database integration or model-answer quality.
- Browser binaries could not be downloaded in the initial local runtime. Browser execution and screenshot inspection are pending the hosted run; do not infer success from test registration.
- Live deployed SSO, real PostgreSQL/ERP/object-store connections and real-model evidence/denial journeys: NOT RUN in this review. They remain release acceptance gates.

## Review boundary

Prepare a draft PR and retain the user's merge decision. No automatic deployment or merge. Review the CI result on the final candidate and inspect the browser evidence. A green result for these corrections is not a new claim that every source integration or all productization work is complete.
