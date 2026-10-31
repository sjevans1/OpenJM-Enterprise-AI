/**
 * Dialog implementation C — React Aria Components (react-aria-components@1.22.1).
 * The ModalOverlay/Modal pair owns focus containment and dismissal; the busy
 * guard disables keyboard dismissal and outside-click closing during a save.
 */
import { Dialog as AriaDialog, Modal, ModalOverlay } from 'react-aria-components'
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

export default function AriaDialogImpl(props: ReviewedActionDialogProps) {
  const { open, title, description, rows, impact, confirmLabel, busy, error } = props
  const { titleId, descId } = useDialogIds()

  return (
    <ModalOverlay
      className="ep01a-overlay"
      isOpen={open}
      isDismissable={!busy}
      isKeyboardDismissDisabled={busy}
      onOpenChange={(next: boolean) => {
        if (!next && !busy) props.onCancel()
      }}
    >
      <Modal className="ep01a-dialog">
        <AriaDialog
          aria-labelledby={titleId}
          aria-describedby={descId}
          className="ep01a-dialog-body"
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
            onConfirm={props.onConfirm}
            onCancel={props.onCancel}
          />
        </AriaDialog>
      </Modal>
    </ModalOverlay>
  )
}
