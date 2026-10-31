/**
 * EP-01A prototype shell.
 *
 * Reached only via `?prototype=ep01a` — the production application does not
 * link here and the module is a lazy chunk, so the production entry bundle
 * does not carry prototype code. Installs the in-page mock API before any
 * prototype surface renders.
 */
import { useEffect, useMemo, useState } from 'react'
import AdminRolesPrototype from './AdminRolesPrototype'
import ResourceCatalogPrototype from './ResourceCatalogPrototype'
import ProbesPrototype from './ProbesPrototype'
import { installMockServer, type Behavior } from './mockServer'
import { DIALOG_IMPLS, type DialogImplId } from './registry'
import './prototype.css'

// Install the in-page mock API at module import time — before React renders
// any child, so the first data load can never race past the shim into the
// real /api proxy.
installMockServer()

type ViewId = 'roles' | 'catalog' | 'probes'

const VIEWS: { id: ViewId; label: string }[] = [
  { id: 'roles', label: 'People & roles (P1)' },
  { id: 'catalog', label: 'Resource catalog (P2)' },
  { id: 'probes', label: 'Component probes' },
]

const BEHAVIORS: Behavior[] = [
  'ok',
  'fail_load',
  'denied_load',
  'denied',
  'error',
  'conflict',
  'stale',
]

export default function PrototypeApp({ params }: { params: URLSearchParams }) {
  const initialView = (params.get('view') as ViewId) || 'roles'
  const [view, setView] = useState<ViewId>(
    VIEWS.some((entry) => entry.id === initialView) ? initialView : 'roles',
  )
  const [dialogImpl, setDialogImpl] = useState<DialogImplId>(() => {
    const requested = params.get('dialog') as DialogImplId | null
    return requested && DIALOG_IMPLS.some((impl) => impl.id === requested)
      ? requested
      : 'radix'
  })
  const [mockInfo, setMockInfo] = useState('')

  useEffect(() => {
    const handle = installMockServer()
    const behavior = params.get('mock') as Behavior | null
    if (behavior && BEHAVIORS.includes(behavior)) {
      handle.setBehavior(behavior)
    }
    const latency = Number(params.get('latency'))
    if (Number.isFinite(latency) && latency >= 0) {
      handle.setLatency(latency)
    }
    setMockInfo(
      `mock api · behavior ${handle.getBehavior()} · latency ${Number.isFinite(latency) && params.get('latency') !== null ? latency : 140} ms`,
    )
  }, [params])

  const summary = useMemo(() => {
    if (!mockInfo) return 'starting…'
    return mockInfo
  }, [mockInfo])

  function selectView(next: ViewId) {
    setView(next)
    const nextParams = new URLSearchParams(window.location.search)
    nextParams.set('prototype', 'ep01a')
    nextParams.set('view', next)
    history.replaceState(null, '', `?${nextParams.toString()}`)
  }

  return (
    <div className="ep01a-root">
      <header className="ep01a-banner">
        <strong>EP-01A prototype</strong>
        <span>
          Fixture data only — not connected to OpenJM services or customer
          content. Dialog implementation: {dialogImpl}.
        </span>
        <span className="ep01a-banner-mock" data-testid="mock-state">
          {summary}
        </span>
      </header>
      <div className="ep01a-shell">
        <nav className="ep01a-tabs" aria-label="Prototype surfaces">
          {VIEWS.map((entry) => (
            <button
              key={entry.id}
              type="button"
              className={view === entry.id ? 'active' : ''}
              aria-current={view === entry.id ? 'page' : undefined}
              onClick={() => selectView(entry.id)}
            >
              {entry.label}
            </button>
          ))}
        </nav>
        <main className="ep01a-main">
          <div className="ep01a-page-head">
            <h1>EP-01A: component reuse prototypes</h1>
          </div>
          {view === 'roles' && <AdminRolesPrototype dialogImpl={dialogImpl} />}
          {view === 'catalog' && <ResourceCatalogPrototype />}
          {view === 'probes' && (
            <ProbesPrototype dialogImpl={dialogImpl} onDialogImpl={setDialogImpl} />
          )}
        </main>
      </div>
    </div>
  )
}
