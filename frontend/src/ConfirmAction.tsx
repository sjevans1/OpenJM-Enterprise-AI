import { useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

type Confirmation = {
  title: string
  description: string
  confirmLabel: string
  onConfirm: () => Promise<void>
}

/** One reviewed action, one request. Failed actions remain open for correction/retry. */
export function useConfirmation() {
  const [pending, setPending] = useState<Confirmation | null>(null)
  const request = (action: Confirmation) =>
    setPending((current) => current ?? action)
  return {
    request,
    isOpen: pending !== null,
    dialog: pending ? (
      <ConfirmationDialog action={pending} onClose={() => setPending(null)} />
    ) : null,
  }
}

function ConfirmationDialog({
  action,
  onClose,
}: {
  action: Confirmation
  onClose: () => void
}) {
  const titleId = useId()
  const descriptionId = useId()
  const panel = useRef<HTMLDivElement>(null)
  const cancel = useRef<HTMLButtonElement>(null)
  const inFlight = useRef(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    cancel.current?.focus()
    return () => {
      if (previous?.isConnected) previous.focus()
    }
  }, [])

  async function confirm() {
    if (inFlight.current) return
    inFlight.current = true
    setBusy(true)
    setError(null)
    try {
      await action.onConfirm()
      onClose()
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : 'The change could not be saved. Try again.',
      )
    } finally {
      inFlight.current = false
      setBusy(false)
    }
  }

  return createPortal(
    <div className="confirm-overlay">
      <div
        ref={panel}
        className="confirm-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        aria-busy={busy}
        tabIndex={-1}
        onKeyDown={(event) => {
          if (event.key === 'Escape') {
            event.preventDefault()
            if (!inFlight.current) onClose()
          }
          if (event.key === 'Tab') {
            const items = panel.current?.querySelectorAll<HTMLElement>(
              'button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), [tabindex="0"]',
            )
            if (!items?.length) {
              event.preventDefault()
              panel.current?.focus()
              return
            }
            const first = items[0]
            const last = items[items.length - 1]
            if (
              event.shiftKey &&
              (document.activeElement === first ||
                document.activeElement === panel.current)
            ) {
              event.preventDefault()
              last.focus()
            } else if (
              !event.shiftKey &&
              (document.activeElement === last ||
                document.activeElement === panel.current)
            ) {
              event.preventDefault()
              first.focus()
            }
          }
        }}
      >
        <h2 id={titleId}>{action.title}</h2>
        <p id={descriptionId} className="confirm-description">
          {action.description}
        </p>
        {error && (
          <p className="auth-error" role="alert">
            {error}
          </p>
        )}
        <div className="confirm-buttons">
          <button ref={cancel} type="button" disabled={busy} onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="primary-action"
            disabled={busy}
            onClick={() => void confirm()}
          >
            {busy ? 'Saving…' : action.confirmLabel}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  )
}
