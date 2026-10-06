import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, test, vi } from 'vitest'
import { ExportControls, structuredEvidenceIndices } from './App'
import type { Evidence } from './api'

afterEach(cleanup)

const documentEvidence: Evidence = {
  source_type: 'document',
  source_id: 'doc-1',
  title: 'Policy',
  passage: 'policy text',
  metadata: {},
}

const structuredEvidence = (id: string): Evidence => ({
  source_type: 'structured_query',
  source_id: id,
  title: 'Finance',
  passage: '{"columns":["a"],"rows":[[1]],"row_count":1}',
  metadata: {},
})

describe('structuredEvidenceIndices', () => {
  test('returns the evidence positions of structured results only', () => {
    const evidence = [documentEvidence, structuredEvidence('s1'), structuredEvidence('s2')]
    expect(structuredEvidenceIndices(evidence)).toEqual([1, 2])
    expect(structuredEvidenceIndices([documentEvidence])).toEqual([])
  })
})

describe('ExportControls', () => {
  test('disables CSV and explains why when no structured rows were persisted', () => {
    render(<ExportControls structuredIndices={[]} onExport={async () => {}} />)
    expect((screen.getByRole('button', { name: 'Export CSV' }) as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByRole('button', { name: 'Export HTML / print' }) as HTMLButtonElement).disabled).toBe(false)
    expect(screen.getByText(/CSV unavailable/i)).toBeTruthy()
  })

  test('exports CSV using the evidence index of the single structured result', async () => {
    const onExport = vi.fn().mockResolvedValue(undefined)
    render(<ExportControls structuredIndices={[3]} onExport={onExport} />)
    fireEvent.click(screen.getByRole('button', { name: 'Export CSV' }))
    await waitFor(() => expect(onExport).toHaveBeenCalledWith('csv', 3))
  })

  test('requires an explicit selection and maps it to the evidence index', async () => {
    const onExport = vi.fn().mockResolvedValue(undefined)
    render(<ExportControls structuredIndices={[1, 4]} onExport={onExport} />)

    const select = screen.getByRole('combobox')
    fireEvent.change(select, { target: { value: '1' } })
    fireEvent.click(screen.getByRole('button', { name: 'Export CSV' }))
    await waitFor(() => expect(onExport).toHaveBeenCalledWith('csv', 4))

    // HTML export never needs a result selection
    fireEvent.click(screen.getByRole('button', { name: 'Export HTML / print' }))
    await waitFor(() => expect(onExport).toHaveBeenCalledWith('html', undefined))
  })

  test('clears the stale selection and surfaces the failure after a denied export', async () => {
    const onExport = vi
      .fn()
      .mockRejectedValue(new Error('Saved report source is no longer available or authorized'))
    render(<ExportControls structuredIndices={[1, 4]} onExport={onExport} />)

    const select = screen.getByRole('combobox') as HTMLSelectElement
    fireEvent.change(select, { target: { value: '1' } })
    expect(select.value).toBe('1')

    fireEvent.click(screen.getByRole('button', { name: 'Export CSV' }))
    await waitFor(() => expect(screen.getByRole('alert')).toBeTruthy())
    expect(screen.getByRole('alert').textContent).toMatch(/no longer available or authorized/i)
    await waitFor(() => expect((screen.getByRole('combobox') as HTMLSelectElement).value).toBe('0'))
  })
})
