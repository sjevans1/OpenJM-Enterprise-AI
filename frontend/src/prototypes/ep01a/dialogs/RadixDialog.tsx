/**
 * Dialog implementation B — Radix Primitives (@radix-ui/react-dialog@1.3.1).
 * Radix owns focus containment, scroll locking, Escape handling and focus
 * return; the busy guard prevents any dismissal while a request is in flight.
 */
import * as Dialog from '@radix-ui/react-dialog'
import { useEffect, useRef } from 'react'
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

export default function RadixDialog(props: ReviewedActionDialogProps) {
  const { open, title, description, rows, impact, confirmLabel, busy, error } = props
  const { titleId, descId } = useDialogIds()

  // Deterministic focus return for a triggerless, controlled Radix dialog.
  // Radix's focus-scope teardown has no trigger to restore to, and relying on
  // `onCloseAutoFocus` alone raced the unmount in CI (focus occasionally
  // settled on `body`). Instead: after the dialog closes, restore focus on a
  // macrotask so it happens after every Radix unmount effect has settled —
  // the last writer wins, every time.
  const wasOpen = useRef(false)
  useEffect(() => {
    if (open) {
      wasOpen.current = true
      return
    }
    if (!wasOpen.current) return
    wasOpen.current = false
    const target = props.returnFocusTo
    if (!target) return
    const handle = window.setTimeout(() => {
      if (target.isConnected) target.focus()
    }, 0)
    return () => window.clearTimeout(handle)
    // returnFocusTo is captured per close; adding it would refocus spuriously.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next) => {
        if (!next && !busy) props.onCancel()
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="ep01a-overlay" />
        <Dialog.Content
          className="ep01a-dialog"
          aria-busy={busy}
          aria-labelledby={titleId}
          aria-describedby={descId}
          onEscapeKeyDown={(event) => {
            if (busy) event.preventDefault()
          }}
          onPointerDownOutside={(event) => {
            if (busy) event.preventDefault()
          }}
        >
          <Dialog.Title id={titleId} className="ep01a-dialog-title">
            {title}
          </Dialog.Title>
          <Dialog.Description id={descId} className="ep01a-dialog-description">
            {description}
          </Dialog.Description>
          <DialogRows rows={rows} />
          <DialogImpact>{impact}</DialogImpact>
          <DialogError message={error} />
          {props.extra}
          <DialogButtons
            busy={busy}
            confirmLabel={confirmLabel}
            onConfirm={props.onConfirm}
            onCancel={props.onCancel}
          />
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
