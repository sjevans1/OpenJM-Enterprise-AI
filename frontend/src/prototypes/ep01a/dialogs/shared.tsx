/**
 * EP-01A — shared pieces for the four reviewed-action dialog implementations
 * that are compared in the component probes (existing OpenJM pattern vs
 * Radix Primitives vs React Aria Components vs Base UI).
 */
import { useEffect, useId } from 'react'
import type { ReactNode, Ref } from 'react'

export type ReviewedActionRow = {
  label: string
  value: string
  tone?: 'neutral' | 'emphasis'
}

export type ReviewedActionDialogProps = {
  open: boolean
  title: string
  description: string
  rows: ReviewedActionRow[]
  impact: string
  confirmLabel: string
  cancelLabel?: string
  busy: boolean
  error: string | null
  /** Extra content rendered inside the dialog below the error (e.g. conflict details). */
  extra?: ReactNode
  /**
   * Element that opened the dialog, used for focus return where the library
   * cannot infer a trigger (standalone controlled dialogs, no Dialog.Trigger).
   */
  returnFocusTo?: HTMLElement | null
  onConfirm: () => void
  onCancel: () => void
}

export function useDialogIds(): { titleId: string; descId: string } {
  const titleId = useId()
  const descId = useId()
  return { titleId, descId }
}

export function DialogHeading({ id, children }: { id: string; children: string }) {
  return (
    <h2 id={id} className="ep01a-dialog-title">
      {children}
    </h2>
  )
}

export function DialogText({ id, children }: { id?: string; children: string }) {
  return (
    <p id={id} className="ep01a-dialog-description">
      {children}
    </p>
  )
}

export function DialogRows({ rows }: { rows: ReviewedActionRow[] }) {
  return (
    <dl className="ep01a-dialog-rows">
      {rows.map((row) => (
        <div className="ep01a-dialog-row" key={row.label}>
          <dt>{row.label}</dt>
          <dd className={row.tone === 'emphasis' ? 'emphasis' : undefined}>{row.value}</dd>
        </div>
      ))}
    </dl>
  )
}

export function DialogImpact({ children }: { children: string }) {
  return <p className="ep01a-dialog-impact">{children}</p>
}

export function DialogError({ message }: { message: string | null }) {
  if (!message) return null
  return (
    <p className="ep01a-alert" role="alert">
      {message}
    </p>
  )
}

export function DialogButtons({
  busy,
  confirmLabel,
  cancelLabel = 'Cancel',
  cancelRef,
  onConfirm,
  onCancel,
}: {
  busy: boolean
  confirmLabel: string
  cancelLabel?: string
  cancelRef?: React.Ref<HTMLButtonElement>
  onConfirm: () => void
  onCancel: () => void
}) {
  return (
    <div className="ep01a-dialog-buttons">
      <button
        ref={cancelRef}
        type="button"
        className="ep01a-btn"
        disabled={busy}
        onClick={onCancel}
      >
        {cancelLabel}
      </button>
      <button
        type="button"
        className="ep01a-btn primary"
        disabled={busy}
        onClick={onConfirm}
      >
        {busy ? 'Saving…' : confirmLabel}
      </button>
    </div>
  )
}
