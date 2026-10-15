/**
 * Dialog implementation B — Radix Primitives (@radix-ui/react-dialog@1.3.1).
 * Radix owns focus containment, scroll locking, Escape handling and focus
 * return; the busy guard prevents any dismissal while a request is in flight.
 */
import * as Dialog from '@radix-ui/react-dialog'
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
          onCloseAutoFocus={(event) => {
            // Radix cannot restore focus to a trigger we do not render (the
            // dialog is opened by ordinary buttons), so the opener is passed
            // in explicitly and focused here.
            const target = props.returnFocusTo
            if (target && target.isConnected) {
              event.preventDefault()
              target.focus()
            }
          }}
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
