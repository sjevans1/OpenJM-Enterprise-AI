/**
 * BV2 immediate user-journey hardening:
 *  - governed report naming flow (#44): a dialog suggests a title, does not save
 *    on first click, and sends the edited title through the existing backend
 *    field;
 *  - Knowledge ingestion blocking state (#42): a page-level overlay shows the
 *    filename and every upload entry point is disabled while indexing.
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import App from './App'

const DEV_AUTH_CONFIG = {
  auth_mode: 'dev',
  oidc_configured: false,
  authorization_endpoint: null,
  issuer: null,
  client_id: null,
  tenant_header: 'X-OpenJM-Tenant',
}

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    }),
  )
}

beforeEach(() => {
  localStorage.clear()
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

test('Save as report opens a naming dialog and sends the edited title', async () => {
  const reportPosts: Array<Record<string, unknown>> = []
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = (init?.method || 'GET').toUpperCase()
    if (url === '/api/auth/config') return jsonResponse(DEV_AUTH_CONFIG)
    if (url === '/api/conversations' && method === 'GET') {
      return jsonResponse([
        { id: 'c1', title: 'Revenue policy', created_at: '2026-10-07T00:00:00Z' },
      ])
    }
    if (url === '/api/conversations/c1') {
      return jsonResponse({
        id: 'c1',
        title: 'Revenue policy',
        created_at: '2026-10-07T00:00:00Z',
        messages: [
          {
            id: 'm1',
            role: 'user',
            content: 'What is the FY2025 revenue policy threshold?',
            created_at: '2026-10-07T00:00:01Z',
          },
          {
            id: 'm2',
            role: 'assistant',
            content: 'The FY2025 threshold is USD 300.',
            execution_class: 'knowledge',
            requested_mode: 'knowledge',
            created_at: '2026-10-07T00:00:02Z',
            evidence: [
              {
                source_type: 'document',
                source_id: 'd1',
                title: 'Revenue policy',
                passage: 'FY2025 threshold USD 300',
              },
            ],
          },
        ],
      })
    }
    if (url === '/api/reports' && method === 'POST') {
      reportPosts.push(JSON.parse(String(init?.body)))
      return jsonResponse({
        id: 'r1',
        title: String(reportPosts[0].title || 'Saved report'),
        conversation_id: 'c1',
        message_id: 'm2',
        execution_class: 'knowledge',
        snapshot_as_of: '2026-10-07T00:00:02Z',
        created_at: '2026-10-07T00:00:03Z',
        source_count: 1,
        available: true,
        answer: 'x',
        evidence: [],
        is_live: false,
      })
    }
    if (['/api/knowledge/documents', '/api/data/sources', '/api/reports'].includes(url)) {
      return jsonResponse([])
    }
    throw new Error(`Unexpected request: ${method} ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)

  render(<App />)
  await screen.findByRole('button', { name: 'New conversation' })
  fireEvent.click(await screen.findByRole('button', { name: /Revenue policy/ }))

  fireEvent.click(await screen.findByRole('button', { name: 'Save as report' }))

  // A dialog appears and nothing is saved on the first click.
  expect(await screen.findByRole('dialog', { name: 'Save as report' })).toBeTruthy()
  expect(reportPosts.length).toBe(0)

  const titleInput = screen.getByLabelText('Report title') as HTMLInputElement
  expect(titleInput.value).toContain('FY2025 revenue policy')

  fireEvent.change(titleInput, { target: { value: 'FY2025 Threshold Policy' } })
  const dialog = screen.getByRole('dialog', { name: 'Save as report' })
  fireEvent.submit(dialog.querySelector('form') as HTMLFormElement)

  await waitFor(() => expect(reportPosts.length).toBe(1))
  expect(reportPosts[0]).toMatchObject({
    message_id: 'm2',
    title: 'FY2025 Threshold Policy',
  })
})

test('Knowledge ingestion blocks the page and shows the processing state', async () => {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = (init?.method || 'GET').toUpperCase()
    if (url === '/api/auth/config') return jsonResponse(DEV_AUTH_CONFIG)
    if (['/api/conversations', '/api/data/sources', '/api/reports'].includes(url)) {
      return jsonResponse([])
    }
    if (url === '/api/knowledge/documents' && method === 'GET') return jsonResponse([])
    if (url === '/api/knowledge/documents' && method === 'POST') {
      // Never resolves: the ingestion stays in flight for the assertion window.
      return new Promise<Response>(() => {})
    }
    throw new Error(`Unexpected request: ${method} ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)

  const { container } = render(<App />)
  await screen.findByRole('button', { name: 'New conversation' })
  fireEvent.click(screen.getByRole('button', { name: /Knowledge/ }))

  const addButton = await screen.findByRole('button', { name: /Add document/ })
  const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement
  expect(fileInput).toBeTruthy()

  const file = new File(['hello'], 'handbook.pdf', { type: 'application/pdf' })
  fireEvent.change(fileInput, { target: { files: [file] } })

  const overlay = await screen.findByRole('alertdialog', { name: 'Indexing document' })
  expect(overlay).toBeTruthy()
  expect(screen.getByText('handbook.pdf')).toBeTruthy()

  // Every upload entry point is disabled while indexing.
  const blocked = await screen.findByRole('button', { name: /Indexing/ })
  expect((blocked as HTMLButtonElement).disabled).toBe(true)
  expect((addButton as HTMLButtonElement).disabled).toBe(true)
})
