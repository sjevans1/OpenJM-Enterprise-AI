import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import { applyBranding, loadPublicConfig } from './branding'
import './styles.css'

// Apply white-label/release metadata before first paint when it is available.
// The fetch is best-effort and never blocks rendering.
void loadPublicConfig().then((config) => applyBranding(config))

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
