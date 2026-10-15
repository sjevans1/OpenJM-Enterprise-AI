# EP-01A component adoption ADR

Status: **PROPOSED — awaiting owner visual acceptance; no adoption executed.**
Date: 15 October 2026. Baseline: `main@bc8f02e5662378a6c954871c042233c635e43e63`
(accepted #109 merge). Tracks [EP-01 issue #111](https://github.com/sjevans1/OpenJM-Enterprise-AI/issues/111).
Prior research: [PR #112](https://github.com/sjevans1/OpenJM-Enterprise-AI/pull/112)
(initial screening, `docs/design/EP01_COMPONENT_SCREENING.md`).

This ADR answers the EP-01 component reuse decision gate with executable
evidence from two rendered prototypes (P1 governed role administration, P2
resource catalog) and four implementations of the same reviewed-action dialog
(current OpenJM pattern, Radix Primitives, React Aria Components, Base UI).
Every number below was measured in this repository; file evidence is committed
under `docs/design/evidence/ep01a/`.

## 1. What was tested

| Candidate | Verified package/asset | Version in the prototype | License |
| --- | --- | --- | --- |
| Radix Primitives | `@radix-ui/react-dialog` | 1.3.1 (exact) | MIT |
| React Aria Components | `react-aria-components` | 1.22.1 (exact) | Apache-2.0 |
| Base UI | `@base-ui/react` | 1.9.0 (exact) | MIT |
| shadcn/ui | no package installed | — (source/CLI distribution over the primitives above) | MIT (when adopted as source) |
| Watermelon UI / platform / registry | `WatermelonCorp/watermelon-platform`, `WatermelonCorp/watermellon-registry` (note registry spelling) | not installed | MIT |
| Existing OpenJM pattern | `frontend/src/ConfirmAction.tsx` (PR #109) and the prototype copy | in-repo | project-owned |
| Accessibility scanner | `@axe-core/playwright` (dev only) | 4.13.0 (exact) | MPL-2.0 |

Contributor-process facts, captured 2026-10-15 via the GitHub API and the npm
registry:

| Project | Stars | Open issues | Last push | Weekly npm downloads of the tested package |
| --- | --- | --- | --- | --- |
| radix-ui/primitives | 19,396 | 226 | 2026-10-14 | 73,536,988 (`@radix-ui/react-dialog`) |
| adobe/react-spectrum | 15,944 | 619 | 2026-10-15 | 4,871,556 (`react-aria-components`) |
| mui/base-ui | 11,150 | 483 | 2026-10-15 | 17,375,856 (`@base-ui/react`) |
| shadcn-ui/ui | 125,630 | 1,790 | 2026-10-14 | — (not an installed package) |
| WatermelonCorp/watermelon-platform | 39 | 2 | 2026-10-14 | — |
| WatermelonCorp/watermellon-registry | 102 | 1 | 2026-09-30 | — |

Package-rename finding: `@base-ui-components/react` (1.0.0-rc.0) is marked
deprecated on the registry ("Package was renamed to `@base-ui/react`"); the
maintained successor `@base-ui/react@1.9.0` was tested instead. Adopting the
old name would pin an abandoned line.

## 2. Method (executable probes, not screenshots of demos)

- The same reviewed-action dialog (title, description, old→new rows, impact
  note, Cancel/Confirm, busy state) is implemented four times over one shared
  content layer; `frontend/e2e/ep01a-probes.spec.ts` opens each in Chromium and
  records initial focus, the Tab cycle, Shift+Tab wrap, Escape, focus return to
  the opener, busy-state dismissal blocking and the ARIA wiring
  (`probe-*.json`).
- P1/P2 interactions assert real effects: zero requests on selection/dropdown
  change, Cancel and Escape; exactly one POST with `expected_revision` on
  Confirm; denied/conflict/failed/stale recovery; pagination identity. Requests
  are read from an instrumented in-page mock log, and no production endpoint
  or authorization path is touched.
- Bundle cost is measured from the production Vite build: each library dialog
  is a lazy chunk, so the entry bundle and per-library chunk sizes are exact
  (`build-report.json`). Install cost was measured in a clean room
  (`npm init` + React 18 baseline, then per-library deltas).
- Accessibility: five axe scans across key states (`axe-*.json`); keyboard,
  focus and reflow behaviour asserted in the specs; contrast pairs chosen for
  WCAG 2.2 AA in the prototype stylesheet.

Limitations of the method: Chromium only; fixture data; no screen-reader
session; windows at 1440x900 / 768x1024 / 390x844 plus 320px reflow and a
200%-zoom-equivalent (720px) pass, not a full zoom-with-layout study.

## 3. Measured results

### 3.1 Dialog probe contract (recorded per implementation)

| Measurement | Existing pattern | Radix 1.3.1 | React Aria 1.22.1 | Base UI 1.9.0 |
| --- | --- | --- | --- | --- |
| Initial focus on open | Cancel | Cancel | **stays on the opener** (no initial focus wiring) | Cancel |
| Tab cycle / Shift+Tab wrap | contained | contained | contained after manual first Tab | contained |
| Escape closes when idle | yes | yes | yes | yes |
| Focus returns to opener | yes | yes, with explicit wiring (see below) | yes | yes |
| Escape ignored + buttons disabled while busy | yes | yes | yes | yes |
| `role=dialog` + `aria-labelledby` resolved | yes | yes | yes | yes |
| `aria-modal` attribute present | yes | **absent** (Radix uses aria-hidden/pointer-events modality) | **absent** | **absent** |
| Rendered content element | `div` | `div` | `section` | `div` |

Integration findings that change real effort:

- **Radix available without a `Dialog.Trigger` does not restore focus** (focus
  fell to `body` in the probe). The wrapper required an explicit
  `onCloseAutoFocus` + opener reference (committed in
  `dialogs/RadixDialog.tsx`) — one small, documented wiring cost for any
  standalone reviewed-action use.
- **React Aria Components left focus on the opener** after opening; producing
  the accepted OpenJM contract (safe initial focus on Cancel) needs explicit
  autofocus wiring per usage, on top of the largest install and bundle costs
  measured here.
- All three libraries correctly refuse dismissal while busy once the busy
  guard is wired (Radix `onEscapeKeyDown`/`onPointerDownOutside`, RAC
  `isKeyboardDismissDisabled`, Base UI controlled `onOpenChange`).

### 3.2 Installed weight (clean room, React 18.3.1 baseline)

| Package | Packages added | node_modules delta |
| --- | --- | --- |
| `@radix-ui/react-dialog@1.3.1` | 25 | +3 MB |
| `react-aria-components@1.22.1` | 11 | +64 MB |
| `@base-ui/react@1.9.0` | 8 | +24 MB |

### 3.3 Production bundle (Vite 6.4.3, same toolchain as CI)

| Chunk | Raw | Gzip |
| --- | --- | --- |
| Entry `index-*.js` — this branch | 303.29 kB | 85.40 kB |
| Entry `index-*.js` — baseline `bc8f02e` | 301.16 kB | 84.48 kB |
| Entry delta | **+2.13 kB** | **+0.92 kB** |
| Entry `index-*.css` | 44.11 kB — byte-identical to baseline | 9.34 kB |
| `PrototypeApp-*.js` (lazy, prototype-only) | 44.18 kB | 13.54 kB |
| `PrototypeApp-*.css` (lazy) | 10.86 kB | 2.86 kB |
| `RadixDialog-*.js` (lazy) | 41.05 kB | 13.89 kB |
| `AriaDialog-*.js` (lazy) | 59.14 kB | 19.92 kB |
| `BaseUiDialog-*.js` (lazy) | 59.20 kB | 20.46 kB |

The only production-bundle growth is the +2.13 kB (+0.92 kB gzip) lazy mount
hook in `main.tsx`; all prototype and library code sits in chunks that are
never fetched without `?prototype=ep01a`. Production CSS is byte-identical.

### 3.4 Accessibility scans (axe-core)

Five states scanned (P1 directory, P1 review dialog, P2 catalog, P2 detail, P2
empty). **Zero serious or critical violations**; after the landmark/h1
corrections the actionable findings are resolved (see `axe-*.json` for the
exact per-state records and any remaining minor/best-practice notes).
Contrast-sensitive pairs (chips, alerts, buttons, muted text) were chosen at
≥4.5:1 for their sizes.

## 4. Decision

**Recommendation: ADOPT Radix Primitives as the single accessible primitive
foundation for EP-01B, starting with dialog/drawer (reviewed actions), dropdown
menu and tooltip. Build the OpenJM component layer (tokens, fields, tables,
status/notice, pagination, review-summary) on top of it and on the accepted
#109 behaviours. Do not adopt a full visual framework, Tailwind, Motion or
React 19 as part of this decision.**

Why Radix:

1. Best measured fit for the contract: same focus/tab/escape behaviour as the
   accepted pattern, smallest install (+25 packages / +3 MB), smallest chunk
   (41.05 kB raw / 13.89 kB gzip) and the largest maintained ecosystem
   (19.4k stars, 73.5M weekly downloads for the dialog package alone).
2. MIT with no attribution burden beyond license retention; unstyled, so the
   OpenJM token system and navy/teal identity stay ours.
3. The one integration cost found (focus return for triggerless dialogs) is a
   known, small, documented wiring, already proven in the prototype.

Runner-up: **Base UI 1.9.0** — MIT, healthy, close behavioural parity, but
~2.4x install delta, ~1.5x chunk size and a younger API surface still settling
post-rename. Keep it as the fallback if Radix direction changes.

Rejected for this gate:

- **React Aria Components** — technically sound and Apache-2.0, but the
  measured initial-focus gap for our open-from-button pattern means more
  wrapper work per usage, plus the largest install (64 MB) and chunk (59.14 kB
  raw). Re-evaluate only if internationalization/complex widgets become a
  first-class need.
- **shadcn/ui as a dependency** — it is a source distribution over these same
  primitives; copying its patterns is compatible with the Radix decision, but
  its default Tailwind scaffolding conflicts with the OpenJM CSS token system.
  Use as implementation reference at most.
- **Watermelon UI as a core input** — small contributor base (39/102 stars),
  React 19 + Tailwind 4 + Motion stack incompatible with the frozen React 18
  architecture; polished visual reference only. Any copied asset must be
  source-owned, license-checked and accessibility-tested on its own merits;
  the current screening found none that meets our bar without rework.
- **Adopting more than one primitive library now** — rejected; two competing
  dialog/menu foundations create exactly the drift this gate exists to avoid.

## 5. License obligations on adoption (EP-01B)

- MIT (Radix, Base UI, shadcn/ui sources, Watermelon assets): retain the
  license text in the installed/source-owned artifacts; no runtime obligations.
- Apache-2.0 (React Aria, if retained for any widget later): include the
  NOTICE file content in distributions and state changes; compatible with the
  current proprietary application code.
- MPL-2.0 (axe-core, dev/test-only): file-level copyleft; stays out of
  production bundles.

## 6. Offline/REL1 and packaging

Exact versions are pinned (no ranges) and the lockfile is committed; the
prototype libraries are runtime dependencies of lazy chunks only. REL1's
offline npm cache and registry-blocked gate were **not run** for this packet
(FULL tier is reserved for a frozen candidate); before any release that ships
the adopted primitive, regenerate the npm offline cache and run the REL1
offline acceptance on the frozen lock. No runtime CDN or registry fetch is
introduced by any candidate.

## 7. Consequences and next steps (EP-01B, after owner acceptance)

1. Keep the P1/P2 prototype routes to demonstrate tokens/components until the
   owner accepts; then port their behaviour (not their code wholesale) into
   `frontend/src/ui/` with the Radix dialog as the reviewed-action base.
2. Reuse the prototype CSS structure as the seed for the semantic token layer,
   mapped from the current `styles.css` variables.
3. Update `ConfirmAction`/`PolicyEditor` consumers incrementally; the
   business semantics (draft → review → confirm, duplicate-write suppression,
   audit copy, fail-closed behaviour) stay OpenJM-owned and tested.
4. Prune the two non-selected libraries from `package.json` in EP-01B and
   record their removal in the lockfile diff; keep the probe spec as a
   regression harness until the chosen base is fully adopted.
5. Any adoption beyond this ADR requires the owner's explicit acceptance of
   this candidate (visual review of the prototypes and screenshots).

## 8. Explicit stop boundary

This packet stops at component selection and visual acceptance. No production
surface, authorization behaviour, backend route or release artifact was
changed. The prototype surfaces are inert without `?prototype=ep01a`, are not
navigable from the product, and must be deleted or gated before any release
cut that does not list them as an approved developer surface.
