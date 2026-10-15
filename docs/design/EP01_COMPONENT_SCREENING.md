# EP-01A — component-library screening (research candidate)

**Status:** RESEARCH / INITIAL SCREENING ONLY. **ADR NOT APPROVED. PROTOTYPES NOT BUILT.**  
**Target:** [EP-01 issue #111](https://github.com/sjevans1/OpenJM-Enterprise-AI/issues/111). Screened October 2026 using external vendor/repository documentation and OpenJM `main@bc8f02e5662378a6c954871c042233c635e43e63`. These notes do not claim a working build, an accessibility certification, or a finished product UI.

## A. Current OpenJM compatibility boundary

Read from `frontend/package.json` and `frontend/src/styles.css` on the accepted baseline:

- React 18.3.1, React DOM 18.3.1, TypeScript, Vite 6, Vitest and Playwright; `lucide-react` already installed.
- CSS is project-owned; the navy/teal/light visual system uses custom CSS variables (`--navy`, `--teal`, `--surface`, etc.). **No Tailwind, Motion, shadcn registry, or React 19 dependency is installed.**
- [PR #109](https://github.com/sjevans1/OpenJM-Enterprise-AI/pull/109) merged `ConfirmAction.tsx` and `PolicyEditor.tsx`; preserve their reviewed-action, explicit-commit, duplicate-submit and error-recovery behavior before extracting generic components.
- `frontend/src/App.tsx`, `AdminPanel.tsx` and `OperatorConsole.tsx` have separate feature-specific interactions. Maintain the tenant/provider authority boundaries, working responsive navigation and release/offline build paths while developing a shared style.

**Conclusion from current dependency inventory:** adopting an entire new styling/runtime ecosystem as a prerequisite would increase release/QA and integration risk. Prefer a thin, incremental accessible-primitive layer plus selective source-owned patterns after validation. This is a working recommendation, not a decision gate pass.

## B. Discovery matrix — candidate facts vs. unverified work

| Candidate | Verified public characteristics | Potential OpenJM contribution | Specific risk / proof still required | Research status |
| --- | --- | --- | --- | --- |
| [Watermelon UI](https://ui.watermelon.sh/) [platform](https://github.com/WatermelonCorp/watermelon-platform) [installable registry](https://github.com/WatermelonCorp/watermellon-registry) | Free open-source React component/blocks/dashboards catalogue. The platform and installable registry advertise React 19, Tailwind CSS 4, Vite and Motion; registry distributes source-owned/copyable UI pieces, MIT. The installable registry repository is spelled **watermellon-registry**. | Useful premium dashboard layouts, density, microinteraction and concrete examples. Copy and adapt only assets that satisfy actual OpenJM workflows. | Full-stack integration would introduce Tailwind/React/Motion changes. Verify individual asset transitive dependencies, license notices and keyboard/focus/accessibility before copying. A demo card is not an acceptable reviewed-action controller. | SHORTLIST — visual/reference / selective source reuse, not a chosen core. |
| [Radix Primitives](https://www.radix-ui.com/primitives) [repo](https://github.com/radix-ui/primitives) | Unstyled, accessible primitives with WAI-ARIA focus/keyboard handling; MIT. | Dialog, dropdown, tooltip, tabs and accessible behavior underpinning existing CSS system. | Compare selected React-18-compatible versions, package scope, bundle/offline impact, focus and tested close-while-busy behavior. | SHORTLIST — likely behavioral foundation. |
| [React Aria Components](https://react-spectrum.adobe.com/react-aria/) [repo](https://github.com/adobe/react-spectrum) | Accessible React components with internationalization and focus/keyboard systems; Apache-2.0. | Forms, tables, accessible interaction primitives for complex data/admin tasks. | API complexity, CSS adaptation, exact package/version/notice obligations, REL1 offline bundles and comparison against Radix/Base UI. | SHORTLIST — alternative behavioral foundation. |
| [Base UI](https://base-ui.com/) [repo](https://github.com/mui/base-ui) | Unstyled accessible React components; MIT. | Alternative dialog, menu, form and composition primitives without a prescribed visual style. | Exact package/React support and state APIs must be checked; avoid mixed foundations without clear ADR. | SHORTLIST — alternative behavioral foundation. |
| [shadcn/ui](https://ui.shadcn.com/) [repo](https://github.com/shadcn-ui/ui) | Open-code, MIT component distribution. Its 2026 CLI offers Base UI, Radix and React Aria as selectable bases. | Ownable implementation examples, application composition and admin patterns; optional controlled source copy. | Default installation or Tailwind scaffolding is not automatically compatible with OpenJM CSS. Validate source selection, notices, dependencies and version locking. | SHORTLIST — source/template layer, not a package mandate. |
| Existing OpenJM React/CSS | Present at baseline; full local source ownership; existing tests and REL1 packaging. | Lowest-churn token foundation, reviewed-action semantics and product-specific authorization state. | Hand-rolled complex dialogs/tables may have keyboard/ARIA gaps and more maintenance than vetted primitives. | BASELINE — required comparator. |

External factual source links are in the table, not evidence that any dependency was built or tested in this repo.

## C. Provisional design direction — subject to prototype falsification

1. Preserve **OpenJM identity**: dark navy navigation, restrained teal for purposeful actions and simple light content surfaces. Do not copy dashboard templates wholesale or add decorative motion without task value.
2. Preserve the implementation in `ConfirmAction` as the known **business behavior**, but evaluate replacing its hand-built focus trap with a proven accessible primitive. Business validation, named old/new review, duplicate-request suppression and backend authority remain OpenJM-owned.
3. Test **one accessible primitive library** as the foundation before adding multiple competing dialog/menu frameworks. Compare Radix vs. React Aria vs. Base UI in executable probes, not by star counts or static demos.
4. Treat Watermelon as **optional source-owned reusable components or high-quality layout inspiration**. Check each copied file's license and all dependencies. Do not require a wholesale React19/Tailwind4/Motion migration unless there is exceptional measured benefit and explicit owner approval.
5. Start with the journeys the product must finish, not a decorative gallery. A component should enter `frontend/src/ui/` only after its real states and keyboard/mobile behavior are demonstrated.

## D. Required experimental packet before choosing any library

### Prototype P1 — governed role/policy change

Build an isolated rendered prototype on a reviewable branch, backed by synthetic request instrumentation:
- loaded current role/policy;
- dropdown/checkbox changes only the draft; **zero mutation requests**;
- old/new values, target identity and affected audience visible before Confirm;
- Cancel and Escape (when safe) submit zero writes;
- pending disables duplicate submit, errors keep draft and focus, success reconciles persisted state;
- 401/403/409, stale concurrent update and forbidden target all fail closed; no self/last-owner bypass.
- Compare existing in-repo dialog vs one candidate accessible primitive. Document measurable focus order and teardown.

### Prototype P2 — dense source/evidence catalog

Use 100+ synthetic sources, long names, 1000+ total metadata entries behind server-like pagination:
- search, stable row identity, sorting/filters, pagination and accurate total/coverage;
- normal/empty/loading/stale/error/denied state; refresh and retry;
- view at 1440x900, 768x1024, 390x844, 320px reflow, 200% zoom and reduced motion;
- keyboard navigation, control labeling, focus persistence, internal table overflow rather than page breakage.

**Evidence:** built app/commit/version and dependency locks; browser screenshots plus their states; Playwright/Vitest requests and focus assertions; accessibility violations and manual checks; relative bundle and CI/install cost; licensing/attribution disposition.

### Decision gate / stop boundary

Write an ADR comparing concrete tested implementations on: task completion, WCAG 2.2 AA target, focus and keyboard behavior, error recovery, code ownership, React 18 compatibility, CSS/token fit, maintenance, package/security/size, offline install/release and extension to three product planes. Request user/product owner review of two prototypes and ADR **before** EP-01B shared component adoption.

Do not widen roles, change backend auth or replace the UI shell to pass a visual test.

## E. Source references checked for this initial research

- [Watermelon Platform repository/stack](https://github.com/WatermelonCorp/watermelon-platform)
- [Watermelon installable registry/repository and MIT license](https://github.com/WatermelonCorp/watermellon-registry)
- [Watermelon UI free React component catalog](https://ui.watermelon.sh/free/react-components)
- [Radix accessibility overview](https://www.radix-ui.com/primitives/docs/overview/accessibility) and [MIT license](https://github.com/radix-ui/primitives/blob/main/LICENSE)
- [React Aria Components](https://react-spectrum.adobe.com/react-aria/) and [Apache-2.0 source](https://github.com/adobe/react-spectrum)
- [Base UI](https://base-ui.com/) and [MIT source](https://github.com/mui/base-ui)
- [shadcn/ui MIT source](https://github.com/shadcn-ui/ui), [2026 React Aria base support](https://ui.shadcn.com/docs/changelog/2026-07-react-aria)

**Remaining verification:** check exact upstream revisions, dependency tree, component licenses at chosen asset path, installed version compatibility, package build/security checks, both working prototypes, comparisons, screenshot review and independent reviewer/owner decision. Nothing in this document warrants declaring EP-01A accepted.
