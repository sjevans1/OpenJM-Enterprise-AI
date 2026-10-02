import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import App from './App'

const reportA = {
  id: 'report-a',
  title: 'Quarterly review',
  conversation_id: 'conversation-a',
  message_id: 'message-a',
  execution_class: 'hybrid',
  snapshot_as_of: '2026-01-02T03:04:05Z',
  created_at: '2026-01-02T03:05:05Z',
  source_count: 2,
  available: true,
}

const reportB = {
  ...reportA,
  id: 'report-b',
  title: 'Other report',
  message_id: 'message-b',
}

const reportDetail = {
  ...reportA,
  answer: 'Historical confidential answer',
  is_live: false as const,
  evidence: [
    {
      source_type: 'document',
      source_id: 'document-a',
      title: 'Revenue policy',
      passage: 'Historical policy passage',
      metadata: {},
    },
    {
      source_type: 'structured_query',
      source_id: 'source-a',
      title: 'Revenue database',
      passage: '{"columns":["customer"],"rows":[["Blue Mountain Cafe"]],"row_count":1}',
      processing_location: 'local',
      metadata: {
        sql: 'SELECT customer FROM revenue',
        row_count: 1,
        tables: ['revenue'],
      },
    },
  ],
}

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  }))
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolver) => { resolve = resolver })
  return { promise, resolve }
}

beforeEach(() => {
  localStorage.clear()
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

test('revoked report content is cleared after a list refresh', async () => {
  let deleted = false
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method || 'GET'
    if (url === '/api/conversations' || url === '/api/knowledge/documents' || url === '/api/data/sources') {
      return jsonResponse([])
    }
    if (url === '/api/reports' && method === 'GET') {
      return jsonResponse(deleted
        ? [{ ...reportA, title: 'Unavailable saved report', available: false, source_count: 0 }]
        : [reportA, reportB])
    }
    if (url === '/api/reports/report-a') return jsonResponse(reportDetail)
    if (url === '/api/reports/report-b' && method === 'DELETE') {
      deleted = true
      return Promise.resolve(new Response(null, { status: 204 }))
    }
    throw new Error(`Unexpected request: ${method} ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)

  render(<App />)
  fireEvent.click(await screen.findByRole('button', { name: /Reports/ }))
  fireEvent.click(await screen.findByRole('button', { name: /Quarterly review/ }))

  expect(await screen.findByText('Historical confidential answer')).toBeTruthy()
  expect(screen.getByText(/Not live/)).toBeTruthy()
  expect(screen.getByText('[DOC 1]')).toBeTruthy()
  expect(screen.getByText('[DATA 1]')).toBeTruthy()

  const deleteButtons = screen.getAllByRole('button', { name: 'Delete report snapshot' })
  fireEvent.click(deleteButtons[1])

  await waitFor(() => expect(screen.getByText('Source unavailable')).toBeTruthy())
  expect(screen.queryByText('Historical confidential answer')).toBeNull()
  expect(screen.queryByText('Historical policy passage')).toBeNull()
  expect(screen.queryByText('SELECT customer FROM revenue')).toBeNull()
})

test('a 409 while reopening never leaves cached snapshot evidence visible', async () => {
  let detailRequests = 0
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url === '/api/conversations' || url === '/api/knowledge/documents' || url === '/api/data/sources') {
      return jsonResponse([])
    }
    if (url === '/api/reports') return jsonResponse([reportA])
    if (url === '/api/reports/report-a') {
      detailRequests += 1
      return detailRequests === 1
        ? jsonResponse(reportDetail)
        : jsonResponse({ detail: 'Saved report source is no longer available or authorized' }, 409)
    }
    throw new Error(`Unexpected request: ${init?.method || 'GET'} ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)

  render(<App />)
  const reportsNav = await screen.findByRole('button', { name: /Reports/ })
  fireEvent.click(reportsNav)
  fireEvent.click(await screen.findByRole('button', { name: /Quarterly review/ }))
  expect(await screen.findByText('Historical confidential answer')).toBeTruthy()

  fireEvent.click(reportsNav)
  fireEvent.click(await screen.findByRole('button', { name: /Quarterly review/ }))

  expect(await screen.findByText('Saved report source is no longer available or authorized')).toBeTruthy()
  expect(screen.queryByText('Historical confidential answer')).toBeNull()
  expect(screen.queryByText('Historical policy passage')).toBeNull()
})

test('deleting one report does not cancel opening another report', async () => {
  const deleteResponse = deferred<Response>()
  const detailResponse = deferred<Response>()
  const reportC = { ...reportA, id: 'report-c', title: 'Concurrent report', message_id: 'message-c' }
  const detailC = { ...reportDetail, ...reportC, answer: 'Concurrent historical answer' }
  let deleted = false
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method || 'GET'
    if (url === '/api/conversations' || url === '/api/knowledge/documents' || url === '/api/data/sources') {
      return jsonResponse([])
    }
    if (url === '/api/reports' && method === 'GET') {
      return jsonResponse(deleted ? [reportA, reportC] : [reportA, reportB, reportC])
    }
    if (url === '/api/reports/report-b' && method === 'DELETE') return deleteResponse.promise
    if (url === '/api/reports/report-c') return detailResponse.promise
    throw new Error(`Unexpected request: ${method} ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)

  render(<App />)
  fireEvent.click(await screen.findByRole('button', { name: /Reports/ }))
  await screen.findByRole('button', { name: /Concurrent report/ })
  const deleteButtons = screen.getAllByRole('button', { name: 'Delete report snapshot' })
  fireEvent.click(deleteButtons[1])
  fireEvent.click(screen.getByRole('button', { name: /Concurrent report/ }))

  await act(async () => {
    deleted = true
    deleteResponse.resolve(new Response(null, { status: 204 }))
    await Promise.resolve()
  })
  await waitFor(() => expect(screen.queryByRole('button', { name: /Other report/ })).toBeNull())
  await act(async () => {
    detailResponse.resolve(new Response(JSON.stringify(detailC), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    await Promise.resolve()
  })

  expect(await screen.findByText('Concurrent historical answer')).toBeTruthy()
})


test('preparing a rerun fills a new Chat composer without executing it', async () => {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method || 'GET'
    if (['/api/conversations', '/api/knowledge/documents', '/api/data/sources'].includes(url)) {
      return jsonResponse([])
    }
    if (url === '/api/reports') return jsonResponse([reportA])
    if (url === '/api/reports/report-a') return jsonResponse(reportDetail)
    if (url === '/api/reports/report-a/rerun-preview') {
      return jsonResponse({
        report_id: 'report-a',
        source_message_id: 'message-a',
        original_question: 'What was the FY2025 policy and revenue?',
        mode: 'hybrid',
        snapshot_as_of: reportA.snapshot_as_of,
        original_source_count: 2,
        requires_explicit_send: true,
        executes_queries: false,
      })
    }
    if (url === '/api/chat') throw new Error('Chat must not auto-execute on preflight')
    throw new Error(`Unexpected request: ${method} ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  render(<App />)
  fireEvent.click(await screen.findByRole('button', { name: /Reports/ }))
  fireEvent.click(await screen.findByRole('button', { name: /Quarterly review/ }))
  fireEvent.click(await screen.findByRole('button', { name: /Prepare rerun in Chat/ }))

  const composer = await screen.findByPlaceholderText('Ask OpenJM about your work...')
  await waitFor(() => expect((composer as HTMLTextAreaElement).value).toBe(
    'What was the FY2025 policy and revenue?'
  ))
  expect(screen.getByText(/Review this historical report question/)).toBeTruthy()
  expect(screen.getByText('Hybrid')).toBeTruthy()
  expect(fetchMock.mock.calls.filter((args) => String(args[0]) === '/api/chat')).toHaveLength(0)
})


test('preflight refusal clears stale report instead of putting leaked text in Chat', async () => {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (['/api/conversations', '/api/knowledge/documents', '/api/data/sources'].includes(url)) {
      return jsonResponse([])
    }
    if (url === '/api/reports') {
      return jsonResponse([{ ...reportA, available: false, title: 'Unavailable saved report' }])
    }
    if (url === '/api/reports/report-a') return jsonResponse(reportDetail)
    if (url === '/api/reports/report-a/rerun-preview') {
      return jsonResponse({ detail: 'Report source is unavailable or no longer authorized' }, 409)
    }
    throw new Error(`Unexpected request: ${init?.method || 'GET'} ${url}`)
  })
  // Start with a visible report, then revoke when the user requests preflight.
  let listed = false
  const original = fetchMock.getMockImplementation()!
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input) === '/api/reports') {
      const result = jsonResponse(
        listed ? [{ ...reportA, available: false, title: 'Unavailable saved report' }] : [reportA]
      )
      listed = true
      return result
    }
    return original(input, init)
  }))
  render(<App />)
  fireEvent.click(await screen.findByRole('button', { name: /Reports/ }))
  fireEvent.click(await screen.findByRole('button', { name: /Quarterly review/ }))
  expect(await screen.findByText('Historical confidential answer')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: /Prepare rerun in Chat/ }))
  expect(await screen.findByText('Report source is unavailable or no longer authorized')).toBeTruthy()
  expect(screen.queryByText('Historical confidential answer')).toBeNull()
  expect(screen.queryByText('Historical policy passage')).toBeNull()
  expect(screen.queryByText(/Review this historical report question/)).toBeNull()
})
