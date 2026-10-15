/**
 * Dialog implementation A — the existing OpenJM pattern (as shipped in
 * frontend/src/ConfirmAction.tsx after PR #109): a hand-built portal with an
 * explicit focus trap, Escape handling and focus restore. Reproduced here with
 * identical behaviour so the probes compare like for like.
 */
import { useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import {
  DialogButtons,
  DialogError,
  DialogHeading,
  DialogImpact,
  DialogRows,
  DialogText,
  useDialogIds,
  type ReviewedActionDialogProps,
} from './shared'

export default function ExistingPatternDialog(props: ReviewedActionDialogProps) {
  const { open, title, description, rows, impact, confirmLabel, busy, error } = props
  const { titleId, descId } = useDialogIds()
  const panel = useRef<HTMLDivElement>(null)
  const cancel = useRef<HTMLButtonElement>(null)
  const busyRef = useRef(busy)
  busyRef.current = busy

  useEffect(() => {
    if (!open) return
    const previous = document.activeElement as HTMLElement | null
    cancel.current?.focus()
    return () => {
      if (previous?.isConnected) previous.focus()
    }
  }, [open])

  if (!open) return null

  return createPortal(
    <div className="ep01a-overlay">
      <div
        ref={panel}
        className="ep01a-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descId}
        aria-busy={busy}
        tabIndex={-1}
        onKeyDown={(event) => {
          if (event.key === 'Escape') {
            event.preventDefault()
            if (!busyRef.current) props.onCancel()
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
              (document.activeElement === first || document.activeElement === panel.current)
            ) {
              event.preventDefault()
              last.focus()
            } else if (
              !event.shiftKey &&
              (document.activeElement === last || document.activeElement === panel.current)
            ) {
              event.preventDefault()
              first.focus()
            }
          }
        }}
      >
        <DialogHeading id={titleId}>{title}</DialogHeading>
        <DialogText id={descId}>{description}</DialogText>
        <DialogRows rows={rows} />
        <DialogImpact>{impact}</DialogImpact>
        <DialogError message={error} />
        {props.extra}
        <DialogButtons
          busy={busy}
          confirmLabel={confirmLabel}
          cancelRef={cancel}
          onConfirm={props.onConfirm}
          onCancel={props.onCancel}
        />
      </div>
    </div>,
    document.body,
  )
}
