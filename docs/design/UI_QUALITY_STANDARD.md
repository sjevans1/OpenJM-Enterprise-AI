# OpenJM UI quality standard

Status: **required design and review contract for the planned enterprise programme; migration NOT COMPLETE.** Recorded 15 October 2026. Scope: every current and newly added UI in the [productization plan](../plan/ENTERPRISE_PRODUCTIZATION.md).

## 1. Experience direction

Retain the existing OpenJM navy navigation, teal accent, light work surfaces and restrained business tone. Extend the clear buttons, reviewed changes, readable administration tabs and mobile navigation established in [PR #109](https://github.com/sjevans1/OpenJM-Enterprise-AI/pull/109). Do not equate a polished landing view with a finished product.

Every screen answers: where am I, which organization/scope am I acting in, what is the current state, what can I do next, and what happened after my action? Use one clear primary action per task area, strong headings and useful information density. Detailed capabilities belong in organized detail views, progressive disclosure or secondary actions, with discoverable labels.

Use shared semantic tokens and components. A component used in the provider plane may show additional operational data, but its field, button, table, status and confirmation behavior is the same. Tenant branding may adjust approved identity/accent tokens without compromising contrast or disguising provider authority. Additional themes are not required by this programme.

## 2. Visual system to implement in EP-01

These are product defaults to validate on the reference views, not a claim that current CSS already meets every requirement.

| System | Default | Review rule |
| --- | --- | --- |
| Color | Existing navy `#071923`, teal `#087c88`, white/light surfaces and dark ink; introduce semantic surface/text/border/action/status tokens | Measure each actual text/background and interactive-state pairing; correct failing tokens rather than dimming labels |
| Typography | Existing Inter/system stack; 16px reading text, 14px dense controls/table text, 12px secondary metadata; consistent 24–32px page-title and 18–20px section-title scales | Long values wrap or have an accessible full-value view; hierarchy comes from size/weight/spacing, not all-caps microtext |
| Spacing | Shared 4/8/12/16/24/32/48px scale; 24–32px desktop content padding, 16px mobile | Align headers, filters, fields, table edges and action rows; avoid isolated one-off margins |
| Layout | Stable sidebar and content shell; bounded reading/form width, wide data views when useful | No unnecessary empty card grids; context and primary action remain easy to find |
| Shape and elevation | Consistent control/panel radii, restrained borders, shallow overlays | Reserve elevation for actual layering; status and hierarchy must not depend on shadows |
| Controls | Default 44px interactive height; compact desktop density only with deliberate spacing | Primary, secondary, quiet and destructive variants share size, focus, busy and disabled rules |
| Icons | Existing Lucide family; common sizes/stroke and consistent placement | Icon-only actions have accessible names and visible help; icons do not replace important action text |
| Motion | Short functional transitions; honor reduced motion | Never animate essential information away or use motion to hide slow requests |

Proposed token families: `surface`, `text`, `border`, `action`, `status`, `space`, `radius`, `type`, `shadow`, `focus`, `layer` and `motion`. Map current CSS into those tokens before migrating consumers. New feature CSS may compose tokens, not invent a second palette or control system.

### External component evaluation is mandatory, not automatic adoption

At EP-01 entry, inspect [Watermelon UI](https://ui.watermelon.sh/) and its [source/registry](https://github.com/WatermelonCorp/watermelon-platform) as a candidate for reusable interactive components, dashboard patterns and visual reference. Compare with maintained alternatives, including [Radix](https://www.radix-ui.com/primitives), [React Aria](https://react-spectrum.adobe.com/react-aria/), [Base UI](https://base-ui.com/) and [shadcn/ui](https://ui.shadcn.com/), plus adaptation of existing OpenJM components. See the [EP-01 reuse decision gate](../plan/ENTERPRISE_PRODUCTIZATION.md#ep-01-component-reuse-decision-gate).

No library is presumed selected. Verify current licenses and transitive license obligations, accessibility/focus handling, maintenance and security, source ownership, rendering quality, React/Vite/CSS integration, package weight and REL1 offline-install requirements. Adopt only the smallest justified subset after two realistic interaction prototypes, an ADR and product-owner visual review. Reused components must meet the same behavior, authorization-facing feedback and failure-recovery requirements as locally built components.

## 3. Shared component and state contract

Create a compact gallery of real rendered states using the project's chosen mechanism. A Storybook installation is optional; accessible examples and reviewable evidence are required. Evaluate maintained primitives in EP-01 and record the decision before adding dependencies.

| Component family | Required behavior |
| --- | --- |
| App shell, page header, section navigation | Tenant/provider context, title/description, consistent actions, current-location state, mobile drawer and keyboard return |
| Button, icon button, link, action menu | Semantic element, named action, visible focus, capability state, duplicate-submit protection, consistent busy label |
| Field, select, checkbox, text area, secret field | Persistent label/help/error, required state, validation on submit/appropriate blur, preserved values, no secret echo from saved records |
| Dialog/drawer and action review | Accessible name/description, focus containment/return, safe initial focus, explicit scope/impact, Cancel and specific commit label |
| Table/list, filters, pagination, resource detail | Stable row IDs, meaningful columns, filter reset, truthful total/coverage, selection scope, internal overflow on narrow screens |
| Status, notification, empty/error/loading state | Consistent semantics and copy, accessible announcement, useful next action, last successful refresh where relevant |
| Policy/audience editor and change summary | Current versus proposed state, audience explanation, draft tracking, conflict handling and server-authorized persistence |
| Evidence, result/artifact, job/sync status | Source identity/provenance, permission-aware details, freshness/partial state, bounded retry and honest failure |

Reuse/refine `ConfirmAction` and `PolicyEditor` from #109 after acceptance. Extract shared behavior from `App.tsx`, `AdminPanel.tsx` and `OperatorConsole.tsx` incrementally. Component extraction must preserve workflows and tests. A generic component must not erase differences in authorization, sensitive content or destructive consequences.

## 4. Rules for actions and forms

| Action class | Required interaction | Example |
| --- | --- | --- |
| View/navigation/filter | Apply immediately; retain useful selection and back-navigation context | Change table filter or open a source |
| Ordinary editable data | Draft → explicit Save or Cancel; field validation; success after server confirmation | Change a display name |
| Access, policy, service state or commercial changes | Draft → review named target, old/new values, audience and consequences → specific Confirm action | Change a user's role, publish Knowledge, activate a source, change a tenant plan |
| Destructive or difficult-to-recover action | Show named target and precise effect; safe initial focus; explicit destructive button; typed confirmation only when risk warrants it | Retire a source or delete a tenant's data under its approved retention contract |
| Multi-resource action | Show exact selected scope/count, per-item authority and outcome; review; handle partial failure | Future bulk policy or lifecycle operation |

Changing a dropdown, checkbox or tab must never silently commit an access or system change. Do not add confirmation to ordinary navigation or every low-risk field. A well-labeled review step can serve as the confirmation; avoid stacking redundant modals.

Drafts survive a refused write. Warn before discarding material changes on local navigation, modal close and browser unload where supported. Disable duplicate submission while pending. On ambiguous network completion, reconcile server state before retrying a non-idempotent action. Re-check permission and revision at commit; a review screen is not an authority token. A conflict displays current/proposed values and offers reload/reconciliation without silently overwriting another administrator.

Use specific labels such as “Save role”, “Activate source”, “Revoke sessions” and “Delete document”. Explain when a source is disconnected versus external data deleted. Report success only after the server confirms it, then refresh the affected detail/list and audit context. A temporary toast alone is insufficient for important long-running or partially failed work.

## 5. State, copy and trust

Each data-backed surface must cover initial loading, empty, populated, filtered-empty, refreshing, partial/stale, validation error, request error, permission denial, pending mutation, success and conflict where applicable. Show cached data with a stale marker during refresh failure; do not turn stale/unknown status into healthy status or zero usage. Omit inaccessible resource metadata rather than revealing it in an error.

Long operations distinguish queued/running/ready/failed/cancelled only where the backend supports those states. Show real progress or an honest indeterminate state, last update and recovery action. Retrying a failed stage must not repeat already completed creation. Do not invent progress percentages, successful syncs, delivery receipts or connection health.

Use business terms: Knowledge, Data, Saved Reports, Administration and Control Center. Explain internal classification as sharing within the company, never “public on the internet.” Explain roles by actual permissions; show a reason when a user can discover but cannot perform an action. Hide actions whose existence itself would disclose inaccessible resources. Avoid raw implementation codes or stack traces in normal flows; authorized diagnostics may expose a copyable request ID and technical detail.

An answer shows its scope, cited sources and relevant time/freshness limits. Partial coverage, inaccessible sources, stale schema and unavailable providers must remain visible. Policy documents in Knowledge are evidence; they do not replace the server's authorization rules. Retrieved instructions cannot grant permissions or override the application's execution policy.

## 6. Accessibility, responsiveness and performance

Target **WCAG 2.2 AA** for complete in-scope journeys; this document is not a conformance claim. Use the [W3C recommendation](https://www.w3.org/TR/2024/REC-WCAG22-20241212/) as the normative reference, particularly keyboard access, labels, focus, contrast, reflow and status messages. Automated scans supplement manual keyboard and assistive-technology review.

- Text contrast: at least 4.5:1 for normal text, 3:1 for qualifying large text; relevant non-text controls/focus indicators need 3:1 contrast. Status is not conveyed by color alone.
- Keyboard: all actions reachable and operable, logical focus order, skip navigation, visible unobscured focus; dialogs return focus to their trigger. Escape/close during pending actions must reflect the actual transaction state.
- Fields have associated labels/errors; tables have meaningful headers; tabs/menus/dialogs use correct semantics. Announce errors and completion without repeatedly announcing entire tables.
- Target size: satisfy WCAG 2.2's 24 CSS pixel minimum rule or its defined exceptions; prefer 44px for primary/touch actions as the OpenJM design default.
- Review at 1440×900 desktop, 768×1024 tablet and 390×844 mobile; verify reflow at 320 CSS pixels and at 200% zoom. Main content/actions remain reachable without page-level horizontal scrolling; inherently two-dimensional data tables may scroll inside a labeled region.
- Reduced-motion mode and long translated-length labels must not break the layout. Maintain the same task capability on mobile, with deliberate table/detail adaptations.
- Under the fixture profile in the [acceptance contract](../qa/ENTERPRISE_PRODUCT_ACCEPTANCE.md), interactions must acknowledge input within 200ms at p95, and a pending request must expose loading/busy state promptly. Measure network/provider work separately; no unmeasured server or model SLA is implied. Add pagination/bounded rendering before large catalogs freeze the page.

## 7. Complete surface inventory

The initial status of every row is **PLANNED / NOT RUN under this standard**. #109 provides selected behavior/layout evidence, not whole-application acceptance. When implemented, add the accepted candidate and visual/journey evidence to each row; do not remove a surface because it is difficult to review.

| ID | Surface and code entry | Consistency and practical completion checks | Package |
| --- | --- | --- | --- |
| UI-00 | Sign-in, auth loading/error, session expiry — `App.tsx`, `auth.ts` | Clear next step, retry/re-auth, accessible form/state, no redirect loop or lost recoverable context | EP-02 |
| UI-01 | Global shell, branding, desktop/mobile navigation — `App.tsx`, `branding.ts` | Tenant/provider context, active location, readable labels, drawer/focus, help/sign-out, no dead destinations | EP-01/02 |
| UI-02 | Chat, history, modes, composer — `App.tsx` | Scope and mode clarity, long/empty conversations, send/pending/error/retry, stable scrolling and preserved input | EP-02 |
| UI-03 | Citations/evidence, artifacts, exports — `App.tsx`, `ArtifactCard.tsx` | Source/provenance, partial/denied/stale states, permission-aware preview/download, usable result tables | EP-02 |
| UI-04 | Knowledge catalog, upload, detail and lifecycle — `App.tsx` | Coverage, filtering/paging, policy/audience, per-file progress/retry, safe replacement/deletion | EP-02/05/06 |
| UI-05 | Data catalog, connection, schema and lifecycle — `App.tsx` | Supported engine, draft connection/test/scope/policy, activation review, secrets masked, failure recovery | EP-02/05/08–12 |
| UI-06 | Saved Reports, runs, curation, sharing, scheduling and exports — `App.tsx` | Browse/detail/edit/run states, evidence currency, access revalidation, approval and deletion, honest scheduled-run status | EP-02 |
| UI-07 | Users, membership, roles, sessions — `AdminPanel.tsx` | Draft/review/commit, identity subject versus email, invitations only if implemented, offboarding and feedback | EP-03/07 |
| UI-08 | Departments, groups/members and steward scope — `AdminPanel.tsx` | Consistent list/detail/editor, explicit membership/scope change, counts and removal consequences | EP-03/04 |
| UI-09 | Data access and policy editor — `AdminPanel.tsx`, accepted #109 `PolicyEditor.tsx` | Audience explanation, preserved grants, dirty/conflict state, effective access without metadata leakage | EP-03/04 |
| UI-10 | Tenant usage/plan and recent activity — `AdminPanel.tsx` | Real configured plan/period/units, no fake zero, filter/paging as needed, event detail and refresh | EP-03/13 |
| UI-11 | Connectors, credentials, sync, resources — `ConnectorsPanel.tsx` | Source capability/state, guided configuration, rotation/revocation, sync recovery, real coverage | EP-03/09–12 |
| UI-12 | Operations — `OperationsPanel.tsx` | Health/failures/jobs or available operational controls, capability scope, actionable diagnostics and bounded retries | EP-03 |
| UI-13 | Control Center overview, tenants, usage, audit, diagnostics — `OperatorConsole.tsx` | Fleet navigation, tenant detail, paging/filtering, last refresh, stale/partial/error states, metadata-only baseline | EP-03/13 |
| UI-14 | Control Center security and releases — `OperatorConsole.tsx` | Distinguish observations from actionable controls, valid timestamps, unavailable states and governed release actions only where implemented | EP-03/13 |
| UI-15 | New provider provisioning, plans, operators/support, offboarding | Same components and reviewed actions; explicit tenant/capability, support scope/expiry, audit and recovery | EP-13 |
| UI-16 | New company setup and source/intake wizards | Reuse normal feature controls, resumable state, visible prerequisites and review before activation | EP-06–09 |

Any newly discovered screen, drawer or modal maps to one of these rows or gets a new row before acceptance. Shared overlays and notifications are reviewed through their owning journeys. A future Automations destination must have a real authorized workflow before becoming active navigation; report scheduling remains covered above.

## 8. UI definition of done

A UI change passes only when the reviewer can verify the task on the rendered screen, its API effects and its recovery path. Supply populated and adverse-state screenshots at applicable viewport sizes, behavior tests for consequential actions, keyboard results, accessibility findings and their disposition. Test actual save/refresh persistence, cancellation without writes, and authorization failure after a screen was opened.

No blocking defects in information hierarchy, clipped actions, unreadable text, broken navigation, misleading state or accidental mutations may remain. The product owner reviews reference views in EP-01 and complete cross-plane journeys in EP-14. Exceptions require a named owner, affected surface/journey, reason, compensating behavior and resolution milestone; an unchecked “premium UI” checkbox is not evidence.
