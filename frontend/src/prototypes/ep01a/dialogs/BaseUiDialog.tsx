/**
 * Dialog implementation D — Base UI (@base-ui/react@1.9.0, the renamed and
 * maintained successor of the deprecated @base-ui-components/react rc build).
 * Popup focus handling is Base UI's; the busy guard keeps the controlled
 * dialog open while a save is in flight.
 */
import { Dialog } from '@base-ui/react/dialog'
import {
  DialogButtons,
  DialogError,
  DialogImpact,
  DialogRows,
  useDialogIds,
  type ReviewedActionDialogProps,
} from './shared'

export default function BaseUiDialogImpl(props: ReviewedActionDialogProps) {
  const { open, title, description, rows, impact, confirmLabel, busy, error } = props
  const { titleId, descId } = useDialogIds()

  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next: boolean) => {
        if (!next && !busy) props.onCancel()
      }}
    >
      <Dialog.Portal>
        <Dialog.Backdrop className="ep01a-overlay" />
        <Dialog.Popup
          className="ep01a-dialog"
          aria-busy={busy}
          aria-labelledby={titleId}
          aria-describedby={descId}
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
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
