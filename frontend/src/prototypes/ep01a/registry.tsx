/**
 * EP-01A — reviewed-action dialog registry.
 *
 * The three candidate library dialogs are lazily imported so each library
 * lands in its own build chunk. That keeps the production entry bundle free
 * of prototype code AND makes the per-library bundle cost directly measurable
 * from the production build output (see the evidence documents).
 */
import { Suspense, lazy } from 'react'
import ExistingPatternDialog from './dialogs/ExistingPatternDialog'
import type { ReviewedActionDialogProps } from './dialogs/shared'

export type DialogImplId = 'existing' | 'radix' | 'aria' | 'baseui'

export type DialogImplInfo = {
  id: DialogImplId
  label: string
  pkg: string
  license: string
  notes: string
}

export const DIALOG_IMPLS: DialogImplInfo[] = [
  {
    id: 'existing',
    label: 'OpenJM pattern (current)',
    pkg: 'in-repo · modelled on ConfirmAction.tsx (PR #109)',
    license: 'project-owned',
    notes: 'Hand-built portal, focus trap and Escape handling shipped today.',
  },
  {
    id: 'radix',
    label: 'Radix Primitives',
    pkg: '@radix-ui/react-dialog 1.3.1',
    license: 'MIT',
    notes: 'Unstyled accessible primitive; dialog, focus scope and dismissal built in.',
  },
  {
    id: 'aria',
    label: 'React Aria Components',
    pkg: 'react-aria-components 1.22.1',
    license: 'Apache-2.0',
    notes: 'Adobe accessibility library with ModalOverlay/Modal/Dialog composition.',
  },
  {
    id: 'baseui',
    label: 'Base UI',
    pkg: '@base-ui/react 1.9.0',
    license: 'MIT',
    notes: 'Maintained successor of the deprecated @base-ui-components/react rc build.',
  },
]

const LazyRadix = lazy(() => import('./dialogs/RadixDialog'))
const LazyAria = lazy(() => import('./dialogs/AriaDialog'))
const LazyBaseUi = lazy(() => import('./dialogs/BaseUiDialog'))

export function ReviewedActionDialog({
  impl,
  ...props
}: ReviewedActionDialogProps & { impl: DialogImplId }) {
  if (impl === 'radix') {
    return (
      <Suspense fallback={null}>
        <LazyRadix {...props} />
      </Suspense>
    )
  }
  if (impl === 'aria') {
    return (
      <Suspense fallback={null}>
        <LazyAria {...props} />
      </Suspense>
    )
  }
  if (impl === 'baseui') {
    return (
      <Suspense fallback={null}>
        <LazyBaseUi {...props} />
      </Suspense>
    )
  }
  return <ExistingPatternDialog {...props} />
}
