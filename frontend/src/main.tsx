import React, { Suspense, lazy } from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import { applyBranding, loadPublicConfig } from './branding'
import './styles.css'

// Apply white-label/release metadata before first paint when it is available.
// The fetch is best-effort and never blocks rendering.
void loadPublicConfig().then((config) => applyBranding(config))

// EP-01A: isolated prototype surfaces for the component-reuse evaluation.
// `?prototype=ep01a` swaps the root render for a lazy-loaded prototype chunk
// that talks only to the in-page mock API. With the parameter absent, the
// production application renders exactly as before and no prototype code is
// loaded.
const PrototypeApp = lazy(() => import('./prototypes/ep01a/PrototypeApp'))

const params = new URLSearchParams(window.location.search)
const root = ReactDOM.createRoot(document.getElementById('root')!)

if (params.get('prototype') === 'ep01a') {
  root.render(
    <React.StrictMode>
      <Suspense fallback={<p className="ep01a-boot">Loading EP-01A prototype…</p>}>
        <PrototypeApp params={params} />
      </Suspense>
    </React.StrictMode>,
  )
} else {
  root.render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  )
}
