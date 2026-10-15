import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import { applyBranding, loadPublicConfig } from './branding'
import './styles.css'

// Apply white-label/release metadata before first paint when it is available.
// The fetch is best-effort and never blocks rendering.
void loadPublicConfig().then((config) => applyBranding(config))

// EP-01A prototype surfaces are a development/test-only tool. `import.meta.env.DEV`
// is statically false in a production build, so this whole branch — including
// the dynamic import, the prototype chunks and the in-page mock API — is
// compiled away: a release build cannot expose the prototype even when
// `?prototype=ep01a` is requested. scripts/ep01a-production-gate.mjs verifies
// this against the built output and the production preview server.
const params = new URLSearchParams(window.location.search)
const root = ReactDOM.createRoot(document.getElementById('root')!)

if (import.meta.env.DEV && params.get('prototype') === 'ep01a') {
  root.render(<p className="ep01a-boot">Loading EP-01A prototype…</p>)
  void import('./prototypes/ep01a/PrototypeApp').then(({ default: PrototypeApp }) => {
    root.render(
      <React.StrictMode>
        <PrototypeApp params={params} />
      </React.StrictMode>,
    )
  })
} else {
  root.render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  )
}
