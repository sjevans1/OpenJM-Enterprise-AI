# EP-01A prototypes (component reuse evaluation)

Two isolated, rendered prototypes and a component-probe surface used to
evaluate reusable component implementations for the EP-01 component reuse
decision gate. Nothing here is part of the production UI: the surfaces are
lazy-loaded, reachable only with `?prototype=ep01a`, are not linked from any
production navigation and talk exclusively to an in-page mock API
(`/api/ep01a/**`). No OpenJM endpoint, authorization path or database is
touched.

## Run

```bash
cd frontend
npm run dev
```

The prototype surfaces are **development/test-only**. `main.tsx` gates the
entire branch behind `import.meta.env.DEV`, so production release builds
contain no prototype code, no mock API and no `ep01a` tokens: requesting
`?prototype=ep01a` against a release build serves the normal application.
`node scripts/ep01a-production-gate.mjs` verifies both the built asset graph
and the production preview server and writes
`docs/design/evidence/ep01a/production-gate.json`.

- `http://127.0.0.1:5173/?prototype=ep01a&view=roles` — P1: governed user and
  role administration (draft → review → confirm; zero writes on selection,
  cancel or Escape).
- `?prototype=ep01a&view=catalog` — P2: dense resource catalog (search,
  filters, server-like pagination, 168 synthetic resources / 1000+ metadata
  entries; loading/empty/stale/failed/denied states).
- `?prototype=ep01a&view=probes` — the same reviewed-action dialog rendered by
  four implementations (current OpenJM pattern, Radix Primitives, React Aria
  Components, Base UI) with a live focus log.

Extra query parameters (all optional):

| Parameter | Values | Purpose |
| --- | --- | --- |
| `dialog` | `existing`, `radix`, `aria`, `baseui` | Dialog implementation used by P1 (default `radix`, the candidate under evaluation). |
| `mock` | `ok`, `fail_load`, `denied_load`, `denied`, `error`, `conflict`, `stale` | Injects failure behaviours deterministically. |
| `latency` | milliseconds | Mock response latency (default 140). |

The in-page request log and behaviour switch are exposed on
`window.__ep01a` (`requests`, `clear()`, `setBehavior()`, `setLatency()`,
`catalogSummary()`); the automated tests assert against this log rather than
UI state alone.

## Tests

```bash
npm test                      # Vitest (unit + jsdom interaction, runs in CI)
npx playwright test ep01a     # Chromium interaction, screenshots, axe scans
```

The Playwright specs (`frontend/e2e/ep01a-*.spec.ts`) cover zero-write
cancellation, the single reviewed write, denied/conflict/failed recovery,
pagination identity, responsive reflow (390 / 768 / 320 / 200%-zoom
equivalent), reduced motion and axe scans. Run them with
`EP01A_EVIDENCE_DIR=../docs/design/evidence/ep01a` to write the committed
screenshots and JSON records; in CI the same artifacts land in
`frontend/test-results/` and are uploaded as the workflow artifact.

## Known limits

- Synthetic data only; nothing here proves live SSO, model or source quality.
- The mock server is an in-page fixture; it is not a security boundary and
  must never be extended into production paths.
- `beforeunload` warns about a dirty role draft; prototype-level tab switching
  does not intercept the draft (production needs the discard flow used by
  `AdminPanel`).
