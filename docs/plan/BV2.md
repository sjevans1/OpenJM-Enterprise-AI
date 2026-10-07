# BV2: immediate user-journey hardening

Base: BV1-C local head `2564c73` (the preserved authoritative head while GitHub
object writes are unavailable). Package 3 of the #45 temporary autonomous work
package. Local branch: `feature/bv2-journey-hardening`.

This package is **frontend-only**; no backend, migration or authorization
behaviour changes.

## Delivered

### #41 Data source form alignment (structural)
`.source-form` used `align-items: end`; the Connection URI field carries a helper
`<small>`, so it was taller and its label/input shifted upward relative to the
other fields. Fixed structurally with a shared CSS variable:
`--field-label-row` reserves the same label row on every field
(`.source-form label { display: grid; grid-template-rows: var(--field-label-row) auto }`)
and `.source-submit` offsets by the same variable, so the three fields and the
button align on one baseline with no per-element pixel nudges. Responsive
breakpoints and the helper copy are unchanged, and credential handling/API
behaviour is untouched.

### #42 Knowledge ingestion blocking state
- A page-level overlay (`role="alertdialog"`, `aria-modal`, `aria-busy`) shows
  while a document is being ingested, with the filename and a clear status.
- Every upload entry point is disabled during ingestion (top-right action, the
  empty-state "Add first document", and destructive document actions).
- The overlay clears on completion or failure, so the page never stays locked;
  the backend's duplicate/indexing protection is unchanged.
- Server-authoritative lifecycle polling is not added in this increment (the
  upload endpoint is synchronous); the request duration drives the blocking
  state. Recorded as NOT RUN.

### Governed report naming (#44)
- "Save as report" now opens a dialog instead of saving immediately.
- The title is pre-filled from the originating user question (or the answer),
  trimmed to the server's 160-char bound, and is editable; save is disabled
  while the title is empty.
- The title is persisted through the existing backend `SaveReportRequest.title`
  field; `api.saveReport(messageId, title?)` sends it only when provided, so the
  previous behaviour and the idempotency contract are preserved.

### Navigation cleanup
- Raw **Connectors** and **Operations** are removed from ordinary customer
  navigation. The panels and their server APIs are unchanged.
- This is a navigation cleanup only. Server-side relocation into platform admin
  remains BV3-C and is **not** claimed here; hiding UI is not treated as a
  security control.

## Verification (local only)

- Frontend tests: `npm test -- --run` -> **10 files, 70 tests passed** (includes
  the new `src/Bv2Journey.test.tsx`: report-naming dialog sends the edited title
  and does not save on first click; ingestion overlay blocks the page, shows the
  filename, and disables the upload entry points).
- Typecheck + production build: `npm run build` -> `tsc --noEmit` clean, vite
  build succeeded.
- `src/Vs7Surfaces.test.tsx` updated to assert the control-plane surfaces are no
  longer in customer navigation (its endpoint-laziness assertions are preserved).
- Backend regression: full suite run on this head (see commit evidence).

## NOT RUN

- GitHub CI (fast/full) for this head: publication and CI dispatch are
  unavailable while GitHub object writes return HTTP 500.
- Server-side relocation/authorization of Connectors and Operations (BV3-C).
- Server-authoritative ingestion lifecycle polling for very long ingestions.
- `npm audit`: no dependencies changed in this package.
