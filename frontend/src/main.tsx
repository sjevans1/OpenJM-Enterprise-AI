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

// Bounded retry: a transient dev-server response (e.g. a re-optimization or an
// aborted module request under parallel test workers) must not leave the
// loading fallback up forever.
async function loadPrototype(attempt = 1): Promise<void> {
  try {
    const { default: PrototypeApp } = await import('./prototypes/ep01a/PrototypeApp')
    root.render(
      <React.StrictMode>
        <PrototypeApp params={params} />
      </React.StrictMode>,
    )
  } catch (error) {
    if (attempt < 3) {
      window.setTimeout(() => void loadPrototype(attempt + 1), 350 * attempt)
      return
    }
    root.render(
      <p className="ep01a-boot">
        The EP-01A prototype could not be loaded. Refresh the page to retry.
      </p>,
    )
  }
}

if (import.meta.env.DEV && params.get('prototype') === 'ep01a') {
  root.render(<p className="ep01a-boot">Loading EP-01A prototype…</p>)
  void loadPrototype()
} else {
  root.render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  )
}
