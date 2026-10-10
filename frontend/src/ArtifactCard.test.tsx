import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import ArtifactCard from './ArtifactCard'
import type { ChatArtifact } from './api'

const artifact: ChatArtifact = {
  id: 'art-1',
  title: 'Weekly summary',
  filename: 'weekly-summary.md',
  mime_type: 'text/markdown',
  artifact_format: 'markdown',
  size_bytes: 1234,
  state: 'active',
  is_evidence_backed: false,
  conversation_id: 'conversation-1',
  message_id: 'message-1',
  created_at: '2026-01-01T00:00:00Z',
}

function fileResponse(body: string, filename: string) {
  return Promise.resolve(
    new Response(body, {
      status: 200,
      headers: { 'Content-Disposition': `attachment; filename="${filename}"` },
    }),
  )
}

beforeEach(() => {
  // jsdom does not implement object URLs; the download helper depends on them.
  vi.stubGlobal('URL', {
    ...URL,
    createObjectURL: vi.fn(() => 'blob:mock'),
    revokeObjectURL: vi.fn(),
  })
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

test('renders server metadata and the disclaimer, never file content', () => {
  render(<ArtifactCard artifact={artifact} />)

  expect(screen.getByText('weekly-summary.md')).toBeTruthy()
  expect(screen.getByText(/Markdown/)).toBeTruthy()
  expect(screen.getByText(/text\/markdown/)).toBeTruthy()
  expect(screen.getByText(/1\.2 KB/)).toBeTruthy()
  expect(screen.getByText(/not a governed report/)).toBeTruthy()
  // The card is a Chat artifact, never presented as approved/authoritative.
  expect(screen.queryByText(/approved|authoritative/i)).toBeNull()
})

test('downloads the stored artifact through the authorized endpoint', async () => {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    expect(String(input)).toBe('/api/artifacts/art-1/download')
    return fileResponse('file-body', 'weekly-summary.md')
  })
  vi.stubGlobal('fetch', fetchMock)

  render(<ArtifactCard artifact={artifact} />)
  fireEvent.click(screen.getByRole('button', { name: 'Download weekly-summary.md' }))

  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
})

test('offers PDF and DOCX render downloads', async () => {
  const calls: string[] = []
  vi.stubGlobal('fetch', (input: RequestInfo | URL) => {
    calls.push(String(input))
    return fileResponse('%PDF', 'weekly-summary.pdf')
  })

  render(<ArtifactCard artifact={artifact} />)
  fireEvent.click(screen.getByRole('button', { name: 'Download PDF of weekly-summary.md' }))
  await waitFor(() => expect(calls).toContain('/api/artifacts/art-1/render/pdf'))

  fireEvent.click(screen.getByRole('button', { name: 'Download DOCX of weekly-summary.md' }))
  await waitFor(() => expect(calls).toContain('/api/artifacts/art-1/render/docx'))
})

test('removing the artifact calls the server then reports retirement', async () => {
  const onDeleted = vi.fn()
  const methods: string[] = []
  vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) => {
    methods.push(init?.method || 'GET')
    return Promise.resolve(
      new Response(JSON.stringify({ ...artifact, state: 'deleted' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
  })

  render(<ArtifactCard artifact={artifact} onDeleted={onDeleted} />)
  fireEvent.click(screen.getByRole('button', { name: 'Remove weekly-summary.md' }))

  await waitFor(() => expect(onDeleted).toHaveBeenCalledWith('art-1'))
  expect(methods).toContain('DELETE')
})

test('a refused removal surfaces a bounded error and keeps the card', async () => {
  const onDeleted = vi.fn()
  vi.stubGlobal('fetch', () =>
    Promise.resolve(
      new Response(JSON.stringify({ detail: 'Artifact not found' }), {
        status: 404,
        headers: { 'Content-Type': 'application/json' },
      }),
    ),
  )

  render(<ArtifactCard artifact={artifact} onDeleted={onDeleted} />)
  fireEvent.click(screen.getByRole('button', { name: 'Remove weekly-summary.md' }))

  expect(await screen.findByRole('alert')).toBeTruthy()
  expect(screen.getByRole('alert').textContent).toMatch(/Artifact not found/)
  expect(onDeleted).not.toHaveBeenCalled()
})
