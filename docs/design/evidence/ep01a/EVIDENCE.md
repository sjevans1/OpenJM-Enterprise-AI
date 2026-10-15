# EP-01A evidence summary

Candidate: branch `ep01a/premium-ui-prototypes`, rebased onto accepted
`main@8e09ab5` (the #110 plan merge after the original `bc8f02e` baseline).
The exact candidate head and every CI run are recorded in the pull request
body and on issue #111; this document ships inside the candidate commits.

## Corrections applied after owner review of the first head

1. **STANDARD failure diagnosis (run 38688970029, Frontend / Node 22).** The
   failing test (`load failure and permission denial render recoverable
   panels`) died with "element was detached from the DOM" on the Retry button
   and then timed out. Root cause found from the retained trace and local
   reproduction: a parallel Playwright worker's first load of
   `react-aria-components` / `@base-ui/react` triggered Vite dependency
   re-optimization, which full-reloads every connected page; the reload reset
   the in-page mock to `ok`, so the error panel (and the element under test)
   never returned. Fix: pre-bundle those libraries at dev-server start
   (`optimizeDeps.include` in `vite.config.ts`, dev-only) plus a bounded retry
   for the prototype's dynamic import. No test timeouts were increased and no
   assertions were weakened. The failure consumed repair cycle 1 of 2 in the
   workflow controller (recorded honestly in state and on the issue).
2. **390px People & Roles identity.** The member-name column could be left
   behind the table's internal horizontal scroll after selecting a row. At
   `≤720px` the two secondary columns (Department, Last active) are dropped
   from the table view (the values remain in the editor), the name cell wraps,
   and selecting a member resets the container's horizontal scroll and brings
   the selected row back into view. The Playwright test now asserts the
   selected name cell's text **and** its viewport visibility
   (`toBeInViewport({ ratio: 0.6 })`), not merely page overflow.
3. **axe incomplete items resolved/qualified.** `th-has-data-cells` (P2 empty
   state) is resolved: the catalog no longer renders an empty table; the empty
   state is a message panel with a clear-filters action. `aria-hidden-focus`
   (P1 review dialog) is **explicitly qualified**: the flagged nodes are
   Radix's focus guards and the modal-hidden background, which are deliberate
   — compensating evidence is the focus-containment and focus-return
   assertions in `ep01a-probes.spec.ts` (Tab cycles stay inside the dialog;
   Escape returns focus to the opener) and the P1 keyboard tests. The axe
   records now carry the full incomplete detail for review.
4. **Production exclusion gate.** `?prototype=ep01a` is now behind
   `import.meta.env.DEV`: the mount branch, the lazy import and the mock API
   are compile-time dead in production. Verified two ways by
   `scripts/ep01a-production-gate.mjs`: the built graph contains **no
   prototype chunk and no `ep01a` token**, and the production preview server
   serves the normal application for `/?prototype=ep01a` (0 banners, no
   `window.__ep01a`). Evidence: `production-gate.json` +
   `screenshots/ep01a-production-gate.png`. Production entry impact versus
   the `bc8f02e` baseline: +0.06 kB raw / +0.04 kB gzip; production CSS
   byte-identical.
5. **Main reconciliation.** `origin/main` (#110) merged into the branch; no
   conflicts; all checks re-run on the merged head.

## What was delivered (unchanged from the reviewed packet)

- **P1 governed user/role administration** and **P2 resource catalog**
  prototypes behind `?prototype=ep01a` (dev/test only), with the
  zero-write/single-reviewed-write interaction contract and the
  loading/empty/stale/failed/denied recovery states.
- **Component probes** for the current OpenJM pattern, Radix Primitives
  1.3.1, React Aria Components 1.22.1 and Base UI 1.9.0 — see `probe-*.json`
  and `docs/design/EP01A_COMPONENT_ADOPTION_ADR.md` (recommendation: Radix;
  provisionally endorsed by the owner, final adoption pending acceptance).

## Commands and results (corrected head, local)

| Check | Command | Result |
| --- | --- | --- |
| TypeScript | `npx tsc --noEmit` | clean |
| Unit/interaction | `npm test -- --run` | **141 passed** (114 existing + 27 new) |
| Browser suite, cold Vite cache | `npx playwright test` | **22 passed** (19 ep01a + 3 existing admin-workflows) |
| Production build | `npm run build` | Vite 6.4.3, 1588 modules, prototype chunks absent |
| Production exclusion | `node scripts/ep01a-production-gate.mjs` | **PASSED** (see JSON) |
| Accessibility | axe via `ep01a-accessibility.spec.ts` | 0 violations in 5 states; 2 incompletes resolved/qualified above |
| Install weight | clean-room npm measurement | Radix +25 pkgs/+3 MB; React Aria +11 pkgs/+64 MB; Base UI +8 pkgs/+24 MB |

Raw records: `build-report.json`, `production-gate.json`,
`p1-interaction.json`, `p2-pagination.json`,
`probe-{existing,radix,aria,baseui}.json`, `axe-*.json`.

## Screenshots (committed, refreshed on the corrected head)

P1: `ep01a-p1-roles-1440`, `ep01a-p1-review-dialog-1440`, `ep01a-p1-success-1440`,
`ep01a-p1-denied-1440`, `ep01a-p1-conflict-1440`, `ep01a-p1-load-failed`,
`ep01a-p1-roles-768`, `ep01a-p1-roles-390` (corrected identity column),
`ep01a-p1-review-dialog-390`.

P2: `ep01a-p2-catalog-1440`, `ep01a-p2-detail-1440`, `ep01a-p2-stale-1440`,
`ep01a-p2-load-failed`, `ep01a-p2-catalog-768`, `ep01a-p2-catalog-390`,
`ep01a-p2-catalog-320`.

Probes + gate: `ep01a-probe-{existing,radix,aria,baseui}`,
`ep01a-production-gate`.

The same Playwright specs run in the OpenJM CI frontend job (Chromium
installed in CI since #109); its artifact `admin-workflow-browser-evidence`
carries the CI-side screenshots and traces.

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
   proves live SSO, real PostgreSQL/source integration, or answer quality.
   The mock is compile-time excluded from production (gate above).
2. **REL1 offline/registry-blocked gates not run** for this packet. Under the
   owner's risk-based acceptance policy (framework issue #10, delivered as a
   separate framework package), FULL remains reserved for release/frozen
   qualification; no FULL run was dispatched for EP-01A.
3. **Chromium only.** No Firefox/WebKit or real screen-reader session. The
   `aria-hidden-focus` incomplete is qualified above; both current incomplete
   items carry their full detail in `axe-*.json` for review.
4. **200% zoom method**: verified as a 720px CSS viewport equivalent at a
   1440px physical width, not a browser-zoom session.
5. **Radix focus-return wiring** (`onCloseAutoFocus` for triggerless dialogs)
   and **React Aria initial-focus wiring** are documented in the ADR and
   implemented in the prototypes.
6. **Tab-switch draft guard**: the P1 draft warns on unload but does not yet
   intercept prototype tab switches (production needs the #109 discard flow).
7. **Not an adoption decision executed**: the ADR is a proposal; the owner's
   visual acceptance is the gate. The resolved deflection also states the
   acceptance chain (independent Haiku acceptance, fresh Luna review, owner
   approval) is recorded separately on the issue/PR.

## Integrity notes

- No backend, authorization, migration, secret or release-packaging change is
  part of this packet; production components and their tests are untouched;
  historical evidence files are untouched.
- CI: FAST (PR) green on the first head; the first STANDARD failure was
  root-caused and consumed repair cycle 1 of 2 (single shared budget);
  corrected-head FAST/STANDARD runs are recorded in the PR and on issue #111.
