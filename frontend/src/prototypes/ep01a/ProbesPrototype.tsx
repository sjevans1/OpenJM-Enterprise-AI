/**
 * EP-01A — component probes.
 *
 * The same reviewed-action dialog rendered by each candidate implementation,
 * with a live focus-order log so the comparison is inspectable by a human in
 * the browser (and re-measured by the Playwright probe spec).
 *
 * Probe content is intentionally identical across implementations so the
 * measurements compare like for like.
 */
import { useEffect, useRef, useState } from 'react'
import { DIALOG_IMPLS, ReviewedActionDialog, type DialogImplId } from './registry'

type ProbeState = {
  open: boolean
  busy: boolean
  lastAction: 'confirmed' | 'cancelled' | null
  focusLog: string[]
}

function describeActiveElement(): string {
  const element = document.activeElement as HTMLElement | null
  if (!element) return '(none)'
  const label =
    element.getAttribute('aria-label') ||
    element.textContent?.trim().slice(0, 60) ||
    element.tagName.toLowerCase()
  return `${element.tagName.toLowerCase()}${element.getAttribute('role') ? `[role=${element.getAttribute('role')}]` : ''}: ${label}`
}

export default function ProbesPrototype({ dialogImpl, onDialogImpl }: { dialogImpl: DialogImplId; onDialogImpl: (next: DialogImplId) => void }) {
  const [probes, setProbes] = useState<Record<string, ProbeState>>(() =>
    Object.fromEntries(
      DIALOG_IMPLS.map((impl) => [impl.id, { open: false, busy: false, lastAction: null, focusLog: [] }]),
    ),
  )
  const activeProbe = useRef<string | null>(null)
  const openers = useRef<Record<string, HTMLElement | null>>({})

  useEffect(() => {
    const handler = (event: FocusEvent) => {
      const id = activeProbe.current
      if (!id) return
      const target = event.target as HTMLElement | null
      if (!target) return
      setProbes((current) => {
        const probe = current[id]
        if (!probe) return current
        const entry = describeActiveElement()
        if (probe.focusLog[probe.focusLog.length - 1] === entry) return current
        return { ...current, [id]: { ...probe, focusLog: [...probe.focusLog, entry] } }
      })
    }
    document.addEventListener('focusin', handler)
    return () => document.removeEventListener('focusin', handler)
  }, [])

  function update(id: string, patch: Partial<ProbeState>) {
    setProbes((current) => ({ ...current, [id]: { ...current[id], ...patch } }))
  }

  function openProbe(id: string, opener?: HTMLElement | null) {
    if (opener) openers.current[id] = opener
    activeProbe.current = id
    update(id, { open: true, busy: false, lastAction: null, focusLog: [] })
  }

  function closeProbe(id: string, action: 'confirmed' | 'cancelled') {
    activeProbe.current = null
    update(id, { open: false, lastAction: action })
  }

  function confirmProbe(id: string) {
    update(id, { busy: true })
    window.setTimeout(() => {
      update(id, { busy: false })
      closeProbe(id, 'confirmed')
    }, 700)
  }

  return (
    <section className="ep01a-panel" aria-label="Component probes">
      <header className="ep01a-section-head">
        <div>
          <h2>Reviewed-action dialog probes</h2>
          <p className="ep01a-help">
            Four implementations of the same dialog, measured with identical
            content: focus order, Escape, focus return, busy-state dismissal
            blocking and ARIA wiring. The live focus log under each card shows
            the sequence observed in this browser.
          </p>
        </div>
      </header>

      <div className="ep01a-segmented" role="radiogroup" aria-label="Default dialog implementation for prototype flows">
        {DIALOG_IMPLS.map((impl) => (
          <label key={impl.id} className={dialogImpl === impl.id ? 'active' : ''}>
            <input
              type="radio"
              name="dialog-impl"
              value={impl.id}
              checked={dialogImpl === impl.id}
              onChange={() => onDialogImpl(impl.id)}
            />
            {impl.label}
          </label>
        ))}
      </div>
      <p className="ep01a-help">
        The selection above is the implementation used by the People &amp; roles
        flow. Choose it after comparing the probe results.
      </p>

      <div className="ep01a-probe-grid">
        {DIALOG_IMPLS.map((impl) => {
          const probe = probes[impl.id]
          return (
            <article className="ep01a-probe-card" key={impl.id}>
              <h3>{impl.label}</h3>
              <p className="ep01a-package">{impl.pkg}</p>
              <p className="ep01a-license">License: {impl.license}</p>
              <p className="ep01a-help">{impl.notes}</p>
              <button
                type="button"
                className="ep01a-btn"
                onClick={(event) => openProbe(impl.id, event.currentTarget)}
              >
                Open {impl.label} probe
              </button>
              {probe.lastAction && (
                <p className="ep01a-probe-result" role="status">
                  Last probe: {probe.lastAction}
                </p>
              )}
              {!!probe.focusLog.length && (
                <div>
                  <h4>Focus log</h4>
                  <ol className="ep01a-focus-log">
                    {probe.focusLog.map((entry, index) => (
                      <li key={`${entry}-${index}`}>{entry}</li>
                    ))}
                  </ol>
                </div>
              )}
              <ReviewedActionDialog
                impl={impl.id}
                open={probe.open}
                title="Probe: reviewed action"
                description="Standard probe content for the dialog comparison."
                rows={[
                  { label: 'Target', value: 'Payroll register — Q3 2026 (res-0007)' },
                  { label: 'Change', value: 'Confidential → Highly restricted', tone: 'emphasis' },
                ]}
                impact="This is the probe impact sentence used identically across implementations."
                confirmLabel="Confirm probe action"
                busy={probe.busy}
                error={null}
                returnFocusTo={openers.current[impl.id]}
                onConfirm={() => confirmProbe(impl.id)}
                onCancel={() => {
                  if (probe.busy) return
                  closeProbe(impl.id, 'cancelled')
                }}
              />
            </article>
          )
        })}
      </div>
    </section>
  )
}
