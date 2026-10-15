# EP-01A evidence summary

Candidate: branch `ep01a/premium-ui-prototypes`, based on accepted
`main@bc8f02e5662378a6c954871c042233c635e43e63`. The exact candidate head and
the OpenJM CI run are recorded in the pull request body and on issue #111; this
document ships inside the candidate commit.

## What was delivered

- **P1 — governed user/role administration** (`?prototype=ep01a&view=roles`):
  searchable 26-member directory; draft role/status editor; explicit
  review dialog naming the target, old → new values and the access
  consequence; single confirmed write with `expected_revision`; success
  reconciles from persisted state; denied (403), conflict (409, with reload),
  failure (500, draft kept, retry) and load-failure/denied-load panels;
  last-owner guard enforced by the mock (attempting to demote the sole owner
  returns a 409 naming the rule).
- **P2 — enterprise resource catalog** (`?prototype=ep01a&view=catalog`):
  168 synthetic resources (Knowledge documents + PostgreSQL sources), 1,344
  metadata entries total, server-like pagination (10/25/50, truthful ranges),
  search/filters with counts taken from responses, stable row identity across
  pages, detail drawer with metadata, loading/refreshing/empty/stale/failed/
  denied states with recovery, internal table scroll on narrow screens,
  320px reflow and 200%-zoom-equivalent checks, reduced-motion skeleton.
- **Component probes** (`?prototype=ep01a&view=probes`): the same dialog from
  the current OpenJM pattern, `@radix-ui/react-dialog@1.3.1`,
  `react-aria-components@1.22.1` and `@base-ui/react@1.9.0`, with a live focus
  log. See `docs/design/EP01A_COMPONENT_ADOPTION_ADR.md` for the decision and
  `probe-*.json` for the raw measurements.

## Commands and results (local, 15 Oct 2026)

| Check | Command | Result |
| --- | --- | --- |
| TypeScript | `npx tsc --noEmit` | clean |
| Unit/interaction (CI-tier) | `npm test -- --run` | **141 passed** (114 existing + 27 new) |
| Browser interaction | `EP01A_EVIDENCE_DIR=../docs/design/evidence/ep01a npx playwright test ep01a` | **19 passed** (~20s) |
| Accessibility | axe-core via `ep01a-accessibility.spec.ts` | **0 violations** in 5 states (2 incomplete items pending manual review) |
| Build | `npm run build` | Vite 6.4.3; entry +2.13 kB raw / +0.92 kB gzip vs `bc8f02e`; production CSS byte-identical |
| Install weight | clean-room npm measurement | Radix +25 pkgs/+3 MB; React Aria +11 pkgs/+64 MB; Base UI +8 pkgs/+24 MB |

Raw records: `build-report.json`, `p1-interaction.json`, `p2-pagination.json`,
`probe-{existing,radix,aria,baseui}.json`, `axe-*.json`.

## Screenshots (committed)

P1: `ep01a-p1-roles-1440`, `ep01a-p1-review-dialog-1440`, `ep01a-p1-success-1440`,
`ep01a-p1-denied-1440`, `ep01a-p1-conflict-1440`, `ep01a-p1-load-failed`,
`ep01a-p1-roles-768`, `ep01a-p1-roles-390`, `ep01a-p1-review-dialog-390`.

P2: `ep01a-p2-catalog-1440`, `ep01a-p2-detail-1440`, `ep01a-p2-stale-1440`,
`ep01a-p2-load-failed`, `ep01a-p2-catalog-768`, `ep01a-p2-catalog-390`,
`ep01a-p2-catalog-320`.

Probes: `ep01a-probe-{existing,radix,aria,baseui}`.

The same Playwright specs run in the OpenJM CI frontend job (Chromium
installed in CI since #109); its artifact `admin-workflow-browser-evidence`
carries the CI-side screenshots and traces for this change as well.

## Interaction/effect guarantees proven

- Selecting a member, changing the role/status dropdowns, Cancel and Escape
  issue **zero** requests (asserted against the instrumented request log, in
  unit and browser tiers).
- Confirm issues **exactly one** POST with the expected revision, then a GET
  reconciliation; success copy appears only after the server-confirmed write.
- Conflict (409) shows the server's current value and revision and reconciles
  through an explicit reload with no silent overwrite; no further writes.
- Denied/failed writes keep the draft, keep focus in the dialog and offer
  retry; load failures offer retry; denied loads explain without a directory.
- Catalog totals and ranges equal the mock's own totals; page 2 rows differ
  from page 1 and the original page's first row is stable on return.

## Remaining limitations (not acceptance)

1. **Fixture-only proof.** All data comes from an in-page mock; nothing here
   proves live SSO, real PostgreSQL/source integration, or answer quality. The
   mock is a prototype device and must never be extended into production
   paths.
2. **REL1 offline/registry-blocked gates not run** (FULL tier is reserved for
   a frozen candidate). Before shipping any adopted primitive, regenerate the
   npm offline cache and run the REL1 offline acceptance on the frozen lock.
3. **Chromium only.** No Firefox/WebKit or real screen-reader session; the 2
   axe "incomplete" colour-contrast items (overlay contexts) still need a
   manual check.
4. **200% zoom method**: verified as a 720px CSS viewport equivalent at a
   1440px physical width, not a browser-zoom session.
5. **Radix focus-return wiring**: standalone (triggerless) Radix dialogs need
   the documented `onCloseAutoFocus` wiring; React Aria Components needs
   explicit initial-focus wiring for the open-from-button pattern (both
   measured in `probe-*.json`, both solved in the prototypes).
6. **Tab-switch draft guard**: the P1 draft warns on unload but does not yet
   intercept prototype tab switches (production needs the #109 discard flow).
7. **Not an adoption decision executed**: the ADR is a proposal; the owner's
   visual acceptance is the gate. No production surface was modified beyond
   the lazy `?prototype=ep01a` mount hook in `main.tsx`.

## Integrity notes

- No backend, authorization, migration, secret or release-packaging change is
  part of this packet; `git diff --stat` against `bc8f02e` shows frontend
  prototype code, tests, tools and documentation only.
- Repair budget for this package: 0 of 2 implementation cycles used at the
  time of writing.
- Historical evidence files were not touched.
