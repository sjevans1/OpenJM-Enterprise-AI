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

/** The application now reads its authentication mode from the server before
 * rendering, so every test has to declare the deployment mode. These tests were
 * written against the development-mode application, where no credential is
 * required; the identity tests live in auth.test.ts and AuthGate.test.tsx. */
const DEV_AUTH_CONFIG = {
  auth_mode: 'dev',
  oidc_configured: false,
  authorization_endpoint: null,
  issuer: null,
  client_id: null,
  tenant_header: 'X-OpenJM-Tenant',
}

function stubFetch(fetchMock: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>) {
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === '/api/auth/config') return jsonResponse(DEV_AUTH_CONFIG)
      return fetchMock(input, init)
    }),
  )
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolver) => { resolve = resolver })
  return { promise, resolve }
}

beforeEach(() => {
  localStorage.clear()
  window.sessionStorage.clear()
  window.history.replaceState({}, '', '/')
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
  stubFetch(fetchMock)

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
  stubFetch(fetchMock)

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
  stubFetch(fetchMock)

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
  stubFetch(fetchMock)
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
  let revoked = false
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (['/api/conversations', '/api/knowledge/documents', '/api/data/sources'].includes(url)) {
      return jsonResponse([])
    }
    if (url === '/api/reports') {
      return jsonResponse(revoked
        ? [{ ...reportA, available: false, title: 'Unavailable saved report' }]
        : [reportA])
    }
    if (url === '/api/reports/report-a') return jsonResponse(reportDetail)
    if (url === '/api/reports/report-a/rerun-preview') {
      revoked = true
      return jsonResponse({ detail: 'Report source is unavailable or no longer authorized' }, 409)
    }
    throw new Error(`Unexpected request: ${init?.method || 'GET'} ${url}`)
  })
  stubFetch(fetchMock)
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


test('opening a report loads definition/history but never executes a run', async () => {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method || 'GET'
    if (['/api/conversations', '/api/knowledge/documents', '/api/data/sources'].includes(url)) return jsonResponse([])
    if (url === '/api/reports') return jsonResponse([reportA])
    if (url === '/api/reports/report-a') return jsonResponse(reportDetail)
    if (url === '/api/reports/report-a/definitions') return jsonResponse([])
    if (url === '/api/reports/report-a/runs?offset=0&limit=20') return jsonResponse([])
    throw new Error(`Unexpected request: ${method} ${url}`)
  })
  stubFetch(fetchMock)

  render(<App />)
  fireEvent.click(await screen.findByRole('button', { name: /Reports/ }))
  fireEvent.click(await screen.findByRole('button', { name: /Quarterly review/ }))
  expect(await screen.findByText('Create pinned definition')).toBeTruthy()
  expect(fetchMock.mock.calls.filter(([input, init]) =>
    String(input).includes('/definitions/') && (init as RequestInit | undefined)?.method === 'POST'
  )).toHaveLength(0)
})

test('fresh report execution requires confirmation and submits one canonical intent', async () => {
  const definition = {
    id: 'definition-a', report_id: 'report-a', version: 1,
    question: 'What was the FY2025 policy and revenue?', mode: 'hybrid',
    pinned_document_ids: ['document-a'], pinned_source_tables: { 'source-a': ['revenue'] },
    created_at: '2026-10-05T00:00:00Z', executes_queries: false, runnable: true,
  }
  const run = {
    id: 'run-a', report_id: 'report-a', definition_version: 1, requested_mode: 'hybrid',
    status: 'succeeded', started_at: '2026-10-05T00:01:00Z', finished_at: '2026-10-05T00:01:02Z',
    failure_category: null, result_size_bytes: 100, trace_count: 2,
    result: { answer: 'Fresh answer', evidence: reportDetail.evidence, structured_result: {}, trace_ids: ['t1'] },
  }
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method || 'GET'
    if (['/api/conversations', '/api/knowledge/documents', '/api/data/sources'].includes(url)) return jsonResponse([])
    if (url === '/api/reports') return jsonResponse([reportA])
    if (url === '/api/reports/report-a') return jsonResponse(reportDetail)
    if (url === '/api/reports/report-a/definitions') return jsonResponse([definition])
    if (url === '/api/reports/report-a/runs?offset=0&limit=20') return jsonResponse(method === 'GET' ? [] : [])
    if (url === '/api/reports/report-a/definitions/1/runs' && method === 'POST') return jsonResponse(run, 202)
    throw new Error(`Unexpected request: ${method} ${url}`)
  })
  stubFetch(fetchMock)

  render(<App />)
  fireEvent.click(await screen.findByRole('button', { name: /Reports/ }))
  fireEvent.click(await screen.findByRole('button', { name: /Quarterly review/ }))
  const runButton = await screen.findByRole('button', { name: 'Run fresh report' })
  fireEvent.click(runButton)
  expect(fetchMock.mock.calls.filter(([input]) => String(input).endsWith('/definitions/1/runs'))).toHaveLength(0)
  fireEvent.click(screen.getByRole('button', { name: 'Confirm and run' }))

  await screen.findByText('Fresh answer')
  const posts = fetchMock.mock.calls.filter(([input, init]) =>
    String(input).endsWith('/definitions/1/runs') && (init as RequestInit | undefined)?.method === 'POST'
  )
  expect(posts).toHaveLength(1)
  const body = JSON.parse(String((posts[0][1] as RequestInit).body))
  expect(body.idempotency_key).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/)
})

test('uncertain run response preserves the same idempotency key for explicit retry', async () => {
  const definition = {
    id: 'definition-a', report_id: 'report-a', version: 1,
    question: 'What was the FY2025 policy and revenue?', mode: 'hybrid',
    pinned_document_ids: ['document-a'], pinned_source_tables: { 'source-a': ['revenue'] },
    created_at: '2026-10-05T00:00:00Z', executes_queries: false, runnable: true,
  }
  let attempts = 0
  const bodies: string[] = []
  const run = {
    id: 'run-a', report_id: 'report-a', definition_version: 1, requested_mode: 'hybrid',
    status: 'failed', started_at: '2026-10-05T00:01:00Z', finished_at: '2026-10-05T00:01:02Z',
    failure_category: 'model', result_size_bytes: null, trace_count: 0, result: null,
  }
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method || 'GET'
    if (['/api/conversations', '/api/knowledge/documents', '/api/data/sources'].includes(url)) return jsonResponse([])
    if (url === '/api/reports') return jsonResponse([reportA])
    if (url === '/api/reports/report-a') return jsonResponse(reportDetail)
    if (url === '/api/reports/report-a/definitions') return jsonResponse([definition])
    if (url === '/api/reports/report-a/runs?offset=0&limit=20') return jsonResponse([])
    if (url === '/api/reports/report-a/definitions/1/runs' && method === 'POST') {
      bodies.push(String(init?.body))
      attempts += 1
      return attempts === 1 ? Promise.reject(new TypeError('network interrupted')) : jsonResponse(run, 202)
    }
    throw new Error(`Unexpected request: ${method} ${url}`)
  })
  stubFetch(fetchMock)

  render(<App />)
  fireEvent.click(await screen.findByRole('button', { name: /Reports/ }))
  fireEvent.click(await screen.findByRole('button', { name: /Quarterly review/ }))
  fireEvent.click(await screen.findByRole('button', { name: 'Run fresh report' }))
  fireEvent.click(screen.getByRole('button', { name: 'Confirm and run' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Retry same run request' }))

  await waitFor(() => expect(bodies).toHaveLength(2))
  expect(JSON.parse(bodies[0]).idempotency_key).toBe(JSON.parse(bodies[1]).idempotency_key)
})


test('server-disabled pinned execution is visible but cannot be submitted', async () => {
  const definition = {
    id: 'definition-disabled', report_id: 'report-a', version: 1,
    question: 'Pinned question', mode: 'hybrid',
    pinned_document_ids: ['document-a'], pinned_source_tables: { 'source-a': ['revenue'] },
    created_at: '2026-10-05T00:00:00Z', executes_queries: false, runnable: false,
  }
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (['/api/conversations', '/api/knowledge/documents', '/api/data/sources'].includes(url)) return jsonResponse([])
    if (url === '/api/reports') return jsonResponse([reportA])
    if (url === '/api/reports/report-a') return jsonResponse(reportDetail)
    if (url === '/api/reports/report-a/definitions') return jsonResponse([definition])
    if (url === '/api/reports/report-a/runs?offset=0&limit=20') return jsonResponse([])
    throw new Error(`Unexpected request: ${init?.method || 'GET'} ${url}`)
  })
  stubFetch(fetchMock)

  render(<App />)
  fireEvent.click(await screen.findByRole('button', { name: /Reports/ }))
  fireEvent.click(await screen.findByRole('button', { name: /Quarterly review/ }))
  const runButton = await screen.findByRole('button', { name: 'Run fresh report' })
  expect((runButton as HTMLButtonElement).disabled).toBe(true)
  expect(screen.getByText(/Manual execution is not enabled/)).toBeTruthy()
  expect(fetchMock.mock.calls.filter(([input]) => String(input).includes('/definitions/1/runs'))).toHaveLength(0)
})

test('late run response is ignored after navigating away from the report', async () => {
  const definition = {
    id: 'definition-a', report_id: 'report-a', version: 1,
    question: 'Pinned question', mode: 'hybrid',
    pinned_document_ids: ['document-a'], pinned_source_tables: { 'source-a': ['revenue'] },
    created_at: '2026-10-05T00:00:00Z', executes_queries: false, runnable: true,
  }
  const runResponse = deferred<Response>()
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method || 'GET'
    if (['/api/conversations', '/api/knowledge/documents', '/api/data/sources'].includes(url)) return jsonResponse([])
    if (url === '/api/reports') return jsonResponse([reportA])
    if (url === '/api/reports/report-a') return jsonResponse(reportDetail)
    if (url === '/api/reports/report-a/definitions') return jsonResponse([definition])
    if (url === '/api/reports/report-a/runs?offset=0&limit=20') return jsonResponse([])
    if (url === '/api/reports/report-a/definitions/1/runs' && method === 'POST') return runResponse.promise
    throw new Error(`Unexpected request: ${method} ${url}`)
  })
  stubFetch(fetchMock)

  render(<App />)
  const reportsNav = await screen.findByRole('button', { name: /Reports/ })
  fireEvent.click(reportsNav)
  fireEvent.click(await screen.findByRole('button', { name: /Quarterly review/ }))
  fireEvent.click(await screen.findByRole('button', { name: 'Run fresh report' }))
  fireEvent.click(screen.getByRole('button', { name: 'Confirm and run' }))
  fireEvent.click(reportsNav)

  await act(async () => {
    runResponse.resolve(new Response(JSON.stringify({
      id: 'late-run', report_id: 'report-a', definition_version: 1, requested_mode: 'hybrid',
      status: 'succeeded', started_at: '2026-10-05T00:01:00Z', finished_at: '2026-10-05T00:01:02Z',
      failure_category: null, result_size_bytes: 100, trace_count: 1,
      result: { answer: 'LATE CONFIDENTIAL RESULT', evidence: [], structured_result: {}, trace_ids: ['t1'] },
    }), { status: 202, headers: { 'Content-Type': 'application/json' } }))
    await Promise.resolve()
  })

  expect(screen.queryByText('LATE CONFIDENTIAL RESULT')).toBeNull()
  expect(screen.queryByText('Historical confidential answer')).toBeNull()
})

test('revoked run history clears cached snapshot evidence', async () => {
  const definition = {
    id: 'definition-a', report_id: 'report-a', version: 1,
    question: 'Pinned question', mode: 'hybrid',
    pinned_document_ids: ['document-a'], pinned_source_tables: { 'source-a': ['revenue'] },
    created_at: '2026-10-05T00:00:00Z', executes_queries: false, runnable: true,
  }
  let revoked = false
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input)
    if (['/api/conversations', '/api/knowledge/documents', '/api/data/sources'].includes(url)) return jsonResponse([])
    if (url === '/api/reports') return jsonResponse(revoked ? [{ ...reportA, available: false }] : [reportA])
    if (url === '/api/reports/report-a') return jsonResponse(reportDetail)
    if (url === '/api/reports/report-a/definitions') return jsonResponse([definition])
    if (url === '/api/reports/report-a/runs?offset=0&limit=20') {
      revoked = true
      return jsonResponse({ detail: 'Report source is no longer available or authorized' }, 409)
    }
    throw new Error(`Unexpected request: GET ${url}`)
  })
  stubFetch(fetchMock)

  render(<App />)
  fireEvent.click(await screen.findByRole('button', { name: /Reports/ }))
  fireEvent.click(await screen.findByRole('button', { name: /Quarterly review/ }))

  await waitFor(() => expect(screen.getByText(/source is no longer available or authorized/i)).toBeTruthy())
  expect(screen.queryByText('Historical confidential answer')).toBeNull()
  expect(screen.queryByText('Historical policy passage')).toBeNull()
})


test('double confirmation click submits exactly one run intent before rerender', async () => {
  const definition = {
    id: 'definition-a', report_id: 'report-a', version: 1,
    question: 'Pinned question', mode: 'hybrid',
    pinned_document_ids: ['document-a'], pinned_source_tables: { 'source-a': ['revenue'] },
    created_at: '2026-10-05T00:00:00Z', executes_queries: false, runnable: true,
  }
  const deferredRun = deferred<Response>()
  const bodies: string[] = []
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method || 'GET'
    if (['/api/conversations', '/api/knowledge/documents', '/api/data/sources'].includes(url)) return jsonResponse([])
    if (url === '/api/reports') return jsonResponse([reportA])
    if (url === '/api/reports/report-a') return jsonResponse(reportDetail)
    if (url === '/api/reports/report-a/definitions') return jsonResponse([definition])
    if (url === '/api/reports/report-a/runs?offset=0&limit=20') return jsonResponse([])
    if (url === '/api/reports/report-a/definitions/1/runs' && method === 'POST') {
      bodies.push(String(init?.body))
      return deferredRun.promise
    }
    throw new Error(`Unexpected request: ${method} ${url}`)
  })
  stubFetch(fetchMock)

  render(<App />)
  fireEvent.click(await screen.findByRole('button', { name: /Reports/ }))
  fireEvent.click(await screen.findByRole('button', { name: /Quarterly review/ }))
  fireEvent.click(await screen.findByRole('button', { name: 'Run fresh report' }))
  const confirm = screen.getByRole('button', { name: 'Confirm and run' })
  fireEvent.click(confirm)
  fireEvent.click(confirm)

  await waitFor(() => expect(bodies).toHaveLength(1))
  deferredRun.resolve(new Response(JSON.stringify({
    id: 'run-double', report_id: 'report-a', definition_version: 1, requested_mode: 'hybrid',
    status: 'failed', started_at: '2026-10-05T00:01:00Z', finished_at: '2026-10-05T00:01:01Z',
    failure_category: 'model', result_size_bytes: null, trace_count: 0, result: null,
  }), { status: 202, headers: { 'Content-Type': 'application/json' } }))
})

test('terminal failure retry requires confirmation and uses a new idempotency key', async () => {
  const definition = {
    id: 'definition-a', report_id: 'report-a', version: 1,
    question: 'Pinned question', mode: 'hybrid',
    pinned_document_ids: ['document-a'], pinned_source_tables: { 'source-a': ['revenue'] },
    created_at: '2026-10-05T00:00:00Z', executes_queries: false, runnable: true,
  }
  const bodies: string[] = []
  let attempt = 0
  const failedRun = (id: string) => ({
    id, report_id: 'report-a', definition_version: 1, requested_mode: 'hybrid',
    status: 'failed', started_at: '2026-10-05T00:01:00Z', finished_at: '2026-10-05T00:01:01Z',
    failure_category: 'model', result_size_bytes: null, trace_count: 0, result: null,
  })
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method || 'GET'
    if (['/api/conversations', '/api/knowledge/documents', '/api/data/sources'].includes(url)) return jsonResponse([])
    if (url === '/api/reports') return jsonResponse([reportA])
    if (url === '/api/reports/report-a') return jsonResponse(reportDetail)
    if (url === '/api/reports/report-a/definitions') return jsonResponse([definition])
    if (url === '/api/reports/report-a/runs?offset=0&limit=20') return jsonResponse([])
    if (url === '/api/reports/report-a/definitions/1/runs' && method === 'POST') {
      bodies.push(String(init?.body))
      attempt += 1
      return jsonResponse(failedRun(`failed-${attempt}`), 202)
    }
    throw new Error(`Unexpected request: ${method} ${url}`)
  })
  stubFetch(fetchMock)

  render(<App />)
  fireEvent.click(await screen.findByRole('button', { name: /Reports/ }))
  fireEvent.click(await screen.findByRole('button', { name: /Quarterly review/ }))

  fireEvent.click(await screen.findByRole('button', { name: 'Run fresh report' }))
  fireEvent.click(screen.getByRole('button', { name: 'Confirm and run' }))
  await screen.findByText(/This run has no deliverable result/)

  // A terminal failure never auto-retries; a new intent requires another confirmation.
  expect(bodies).toHaveLength(1)
  fireEvent.click(screen.getByRole('button', { name: 'Run fresh report' }))
  expect(bodies).toHaveLength(1)
  fireEvent.click(screen.getByRole('button', { name: 'Confirm and run' }))
  await waitFor(() => expect(bodies).toHaveLength(2))

  expect(JSON.parse(bodies[0]).idempotency_key).not.toBe(JSON.parse(bodies[1]).idempotency_key)
})

test('run history loads older pages without duplicating existing runs', async () => {
  const definition = {
    id: 'definition-a', report_id: 'report-a', version: 1,
    question: 'Pinned question', mode: 'hybrid',
    pinned_document_ids: ['document-a'], pinned_source_tables: { 'source-a': ['revenue'] },
    created_at: '2026-10-05T00:00:00Z', executes_queries: false, runnable: true,
  }
  const runSummary = (index: number) => ({
    id: `run-${index}`, report_id: 'report-a', definition_version: 1, requested_mode: 'hybrid',
    status: 'succeeded', started_at: `2026-10-05T00:${String(index).padStart(2, '0')}:00Z`,
    finished_at: `2026-10-05T00:${String(index).padStart(2, '0')}:01Z`,
    failure_category: null, result_size_bytes: 100, trace_count: 1,
  })
  const firstPage = Array.from({ length: 20 }, (_, index) => runSummary(index))
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input)
    if (['/api/conversations', '/api/knowledge/documents', '/api/data/sources'].includes(url)) return jsonResponse([])
    if (url === '/api/reports') return jsonResponse([reportA])
    if (url === '/api/reports/report-a') return jsonResponse(reportDetail)
    if (url === '/api/reports/report-a/definitions') return jsonResponse([definition])
    if (url === '/api/reports/report-a/runs?offset=0&limit=20') return jsonResponse(firstPage)
    if (url === '/api/reports/report-a/runs?offset=20&limit=20') return jsonResponse([runSummary(20), runSummary(0)])
    throw new Error(`Unexpected request: GET ${url}`)
  })
  stubFetch(fetchMock)

  render(<App />)
  fireEvent.click(await screen.findByRole('button', { name: /Reports/ }))
  fireEvent.click(await screen.findByRole('button', { name: /Quarterly review/ }))
  expect(await screen.findByText('20 loaded')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Load older runs' }))
  expect(await screen.findByText('21 loaded')).toBeTruthy()
})


test('selecting another immutable definition changes the explicit run target without executing', async () => {
  const definitions = [
    {
      id: 'definition-v2', report_id: 'report-a', version: 2,
      question: 'Version two question', mode: 'data',
      pinned_document_ids: [], pinned_source_tables: { 'source-a': ['revenue'] },
      created_at: '2026-10-05T00:02:00Z', executes_queries: false, runnable: true,
    },
    {
      id: 'definition-v1', report_id: 'report-a', version: 1,
      question: 'Version one question', mode: 'hybrid',
      pinned_document_ids: ['document-a'], pinned_source_tables: { 'source-a': ['revenue'] },
      created_at: '2026-10-05T00:00:00Z', executes_queries: false, runnable: true,
    },
  ]
  const posts: string[] = []
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method || 'GET'
    if (['/api/conversations', '/api/knowledge/documents', '/api/data/sources'].includes(url)) return jsonResponse([])
    if (url === '/api/reports') return jsonResponse([reportA])
    if (url === '/api/reports/report-a') return jsonResponse(reportDetail)
    if (url === '/api/reports/report-a/definitions') return jsonResponse(definitions)
    if (url === '/api/reports/report-a/runs?offset=0&limit=20') return jsonResponse([])
    if (url === '/api/reports/report-a/definitions/1/runs' && method === 'POST') {
      posts.push(url)
      return jsonResponse({
        id: 'run-v1', report_id: 'report-a', definition_version: 1, requested_mode: 'hybrid',
        status: 'failed', started_at: '2026-10-05T00:03:00Z', finished_at: '2026-10-05T00:03:01Z',
        failure_category: 'model', result_size_bytes: null, trace_count: 0, result: null,
      }, 202)
    }
    throw new Error(`Unexpected request: ${method} ${url}`)
  })
  stubFetch(fetchMock)

  render(<App />)
  fireEvent.click(await screen.findByRole('button', { name: /Reports/ }))
  fireEvent.click(await screen.findByRole('button', { name: /Quarterly review/ }))

  const selector = await screen.findByLabelText('Definition version')
  expect((selector as HTMLSelectElement).value).toBe('definition-v2')
  fireEvent.change(selector, { target: { value: 'definition-v1' } })
  expect(await screen.findByText('Version one question')).toBeTruthy()
  expect(posts).toHaveLength(0)

  fireEvent.click(screen.getByRole('button', { name: 'Run fresh report' }))
  fireEvent.click(screen.getByRole('button', { name: 'Confirm and run' }))
  await waitFor(() => expect(posts).toEqual(['/api/reports/report-a/definitions/1/runs']))
})


test('successful run is not mislabeled uncertain when only history refresh fails', async () => {
  const definition = {
    id: 'definition-a', report_id: 'report-a', version: 1,
    question: 'Pinned question', mode: 'hybrid',
    pinned_document_ids: ['document-a'], pinned_source_tables: { 'source-a': ['revenue'] },
    created_at: '2026-10-05T00:00:00Z', executes_queries: false, runnable: true,
  }
  let historyReads = 0
  const run = {
    id: 'run-success', report_id: 'report-a', definition_version: 1, requested_mode: 'hybrid',
    status: 'succeeded', started_at: '2026-10-05T00:01:00Z', finished_at: '2026-10-05T00:01:02Z',
    failure_category: null, result_size_bytes: 100, trace_count: 1,
    result: { answer: 'Authoritative fresh answer', evidence: [], structured_result: {}, trace_ids: ['t1'] },
  }
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method || 'GET'
    if (['/api/conversations', '/api/knowledge/documents', '/api/data/sources'].includes(url)) return jsonResponse([])
    if (url === '/api/reports') return jsonResponse([reportA])
    if (url === '/api/reports/report-a') return jsonResponse(reportDetail)
    if (url === '/api/reports/report-a/definitions') return jsonResponse([definition])
    if (url === '/api/reports/report-a/runs?offset=0&limit=20') {
      historyReads += 1
      return historyReads === 1 ? jsonResponse([]) : Promise.reject(new TypeError('history network failure'))
    }
    if (url === '/api/reports/report-a/definitions/1/runs' && method === 'POST') return jsonResponse(run, 202)
    throw new Error(`Unexpected request: ${method} ${url}`)
  })
  stubFetch(fetchMock)

  render(<App />)
  fireEvent.click(await screen.findByRole('button', { name: /Reports/ }))
  fireEvent.click(await screen.findByRole('button', { name: /Quarterly review/ }))
  fireEvent.click(await screen.findByRole('button', { name: 'Run fresh report' }))
  fireEvent.click(screen.getByRole('button', { name: 'Confirm and run' }))

  expect(await screen.findByText('Authoritative fresh answer')).toBeTruthy()
  expect(await screen.findByText(/Run completed, but history could not refresh/)).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'Retry same run request' })).toBeNull()
})

test('revocation discovered while paging history clears all cached report content', async () => {
  const definition = {
    id: 'definition-a', report_id: 'report-a', version: 1,
    question: 'Pinned question', mode: 'hybrid',
    pinned_document_ids: ['document-a'], pinned_source_tables: { 'source-a': ['revenue'] },
    created_at: '2026-10-05T00:00:00Z', executes_queries: false, runnable: true,
  }
  const firstPage = Array.from({ length: 20 }, (_, index) => ({
    id: `run-page-${index}`, report_id: 'report-a', definition_version: 1, requested_mode: 'hybrid',
    status: 'succeeded', started_at: '2026-10-05T00:00:00Z', finished_at: '2026-10-05T00:00:01Z',
    failure_category: null, result_size_bytes: 100, trace_count: 1,
  }))
  let revoked = false
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input)
    if (['/api/conversations', '/api/knowledge/documents', '/api/data/sources'].includes(url)) return jsonResponse([])
    if (url === '/api/reports') return jsonResponse(revoked ? [{ ...reportA, available: false }] : [reportA])
    if (url === '/api/reports/report-a') return jsonResponse(reportDetail)
    if (url === '/api/reports/report-a/definitions') return jsonResponse([definition])
    if (url === '/api/reports/report-a/runs?offset=0&limit=20') return jsonResponse(firstPage)
    if (url === '/api/reports/report-a/runs?offset=20&limit=20') {
      revoked = true
      return jsonResponse({ detail: 'Report source is no longer available or authorized' }, 409)
    }
    throw new Error(`Unexpected request: GET ${url}`)
  })
  stubFetch(fetchMock)

  render(<App />)
  fireEvent.click(await screen.findByRole('button', { name: /Reports/ }))
  fireEvent.click(await screen.findByRole('button', { name: /Quarterly review/ }))
  expect(await screen.findByText('20 loaded')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Load older runs' }))

  await waitFor(() => expect(screen.getByText(/source is no longer available or authorized/i)).toBeTruthy())
  expect(screen.queryByText('Historical confidential answer')).toBeNull()
  expect(screen.queryByText('Historical policy passage')).toBeNull()
})

// ---------------------------------------------------------------------------
// BV4: Chat-first, permission-aware navigation.
//
// The navigation offers a governance or administration surface only when the
// server-resolved principal holds the capability for it. This is presentation
// only: the FastAPI backend re-authorizes every request, so hiding a surface
// is never used as the security boundary. These tests drive the OIDC path so
// the principal context is explicit rather than the development workspace.
// ---------------------------------------------------------------------------

const OIDC_AUTH_CONFIG = {
  auth_mode: 'oidc',
  oidc_configured: true,
  authorization_endpoint: 'https://idp.example.test/realms/openjm/protocol/openid-connect/auth',
  issuer: 'https://idp.example.test/realms/openjm',
  client_id: 'openjm-enterprise-ai',
  tenant_header: 'X-OpenJM-Tenant',
}

function principalContext(overrides: Record<string, unknown> = {}) {
  return {
    principal_id: 'principal-a',
    tenant_id: 'tenant-a',
    subject: 'subject-a',
    role: 'viewer',
    auth_method: 'oidc',
    email: 'viewer@example.test',
    display_name: 'Viewer Person',
    permissions: ['chat:use'],
    department_ids: [],
    group_ids: [],
    steward_scopes: [],
    platform_capabilities: [],
    ...overrides,
  }
}

function storeAuthenticatedSession() {
  window.sessionStorage.setItem(
    'openjm.session',
    JSON.stringify({
      token: 'openjm-session-token',
      expiresAt: Date.now() + 3_600_000,
      principalId: 'principal-a',
      tenantId: 'tenant-a',
      role: 'viewer',
      permissions: ['chat:use'],
    }),
  )
}

function stubAuthenticated(principal: Record<string, unknown>) {
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/auth/config') return jsonResponse(OIDC_AUTH_CONFIG)
      if (url === '/api/auth/me') return jsonResponse(principal)
      return jsonResponse([])
    }),
  )
}

/** The navigation entries the sidebar actually offers, without count pills. */
function navLabels(): string[] {
  const nav = document.querySelector('.primary-nav')
  return Array.from(nav?.querySelectorAll('button.nav-item') ?? []).map((element) => {
    const clone = element.cloneNode(true) as HTMLElement
    clone.querySelectorAll('.count-pill').forEach((pill) => pill.remove())
    return (clone.textContent || '').trim()
  })
}

test('an ordinary viewer is offered only Chat', async () => {
  storeAuthenticatedSession()
  stubAuthenticated(principalContext())
  render(<App />)

  await screen.findByRole('button', { name: 'New conversation' })
  await waitFor(() => expect(navLabels()).toEqual(['Chat']))
  expect(screen.queryByRole('button', { name: /Knowledge/ })).toBeNull()
  expect(screen.queryByRole('button', { name: /^Data/ })).toBeNull()
  expect(screen.queryByRole('button', { name: /Reports/ })).toBeNull()
  expect(screen.queryByRole('button', { name: /Administration/ })).toBeNull()
})

test('a viewer holding reports:read is additionally offered Reports', async () => {
  storeAuthenticatedSession()
  stubAuthenticated(principalContext({ permissions: ['chat:use', 'reports:read'] }))
  render(<App />)

  await screen.findByRole('button', { name: 'New conversation' })
  await waitFor(() => expect(navLabels()).toEqual(['Chat', 'Reports']))
})

test('a knowledge capability holder is offered Knowledge but not Data', async () => {
  storeAuthenticatedSession()
  stubAuthenticated(principalContext({ permissions: ['chat:use', 'knowledge:write'] }))
  render(<App />)

  await screen.findByRole('button', { name: 'New conversation' })
  await waitFor(() => expect(navLabels()).toEqual(['Chat', 'Knowledge']))
})

test('a data capability holder is offered Data but not Knowledge', async () => {
  storeAuthenticatedSession()
  stubAuthenticated(principalContext({ permissions: ['chat:use', 'data:write'] }))
  render(<App />)

  await screen.findByRole('button', { name: 'New conversation' })
  await waitFor(() => expect(navLabels()).toEqual(['Chat', 'Data']))
})

test('a stewardship scope opens the knowledge and data governance surfaces', async () => {
  storeAuthenticatedSession()
  stubAuthenticated(
    principalContext({ steward_scopes: [{ scope_type: 'group', scope_id: 'finance' }] }),
  )
  render(<App />)

  await screen.findByRole('button', { name: 'New conversation' })
  await waitFor(() => expect(navLabels()).toEqual(['Chat', 'Knowledge', 'Data']))
})

test('a client administrator is offered Administration', async () => {
  storeAuthenticatedSession()
  stubAuthenticated(principalContext({ role: 'admin', permissions: ['chat:use'] }))
  render(<App />)

  await screen.findByRole('button', { name: 'New conversation' })
  await waitFor(() => expect(navLabels()).toContain('Administration'))
  expect(navLabels()).toEqual(['Chat', 'Knowledge', 'Data', 'Administration'])
})

test('the explicit Chat/Knowledge/Data/Hybrid selector survives for ordinary users', async () => {
  storeAuthenticatedSession()
  stubAuthenticated(principalContext())
  const { container } = render(<App />)

  await screen.findByRole('button', { name: 'New conversation' })
  const selector = container.querySelector('.mode-selector') as HTMLElement | null
  expect(selector).toBeTruthy()
  expect(container.querySelector('.mode-label')?.textContent).toBe('Chat')

  fireEvent.click(selector as HTMLElement)
  const options = Array.from(container.querySelectorAll('.mode-dropdown .mode-option')).map(
    (element) => (element.textContent || '').trim(),
  )
  expect(options).toEqual(['Chat', 'Knowledge', 'Data', 'Hybrid'])
})

test('platform control-plane capabilities grant no ordinary tenant navigation', async () => {
  storeAuthenticatedSession()
  stubAuthenticated(
    principalContext({
      permissions: ['chat:use', 'reports:read'],
      platform_capabilities: ['platform:admin', 'platform:observability'],
    }),
  )
  render(<App />)

  await screen.findByRole('button', { name: 'New conversation' })
  await waitFor(() => expect(navLabels()).toEqual(['Chat', 'Reports']))
  const nav = document.querySelector('.primary-nav') as HTMLElement
  expect(nav.textContent || '').not.toMatch(/platform|control plane/i)
})

// ---------------------------------------------------------------------------
// Saving a snapshot as a governed report is a write capability.
//
// The server requires `reports:write` to create a Saved Report. Before this
// guard the affordance was offered to every principal, so a plain viewer could
// open the naming modal and every save failed closed with
// 403 "lacks required permission 'reports:write'". The affordance is now shown
// only where it can succeed; the backend still re-authorizes the POST.
// ---------------------------------------------------------------------------

const EVIDENCE_ANSWER = {
  conversation_id: 'conversation-a',
  message_id: 'message-a',
  answer: 'Grounded answer.',
  execution_class: 'knowledge',
  mode: 'knowledge',
  evidence: [
    {
      source_type: 'document',
      source_id: 'document-a',
      title: 'Employee Handbook',
      passage: 'Probation is three months.',
      metadata: {},
    },
  ],
  artifacts: [],
}

function stubChatWithEvidence(principal: Record<string, unknown>) {
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method || 'GET'
      if (url === '/api/auth/config') return jsonResponse(OIDC_AUTH_CONFIG)
      if (url === '/api/auth/me') return jsonResponse(principal)
      if (url === '/api/chat' && method === 'POST') return jsonResponse(EVIDENCE_ANSWER)
      return jsonResponse([])
    }),
  )
}

async function askOneGroundedQuestion() {
  const composer = await screen.findByPlaceholderText('Ask OpenJM about your work...')
  fireEvent.change(composer, {
    target: { value: 'What does the handbook say about probation?' },
  })
  fireEvent.keyDown(composer, { key: 'Enter', shiftKey: false })
  await screen.findByText('Grounded answer.')
}

test('a viewer without reports:write is not offered Save as report', async () => {
  storeAuthenticatedSession()
  stubChatWithEvidence(principalContext({ permissions: ['chat:use', 'reports:read'] }))
  render(<App />)

  await askOneGroundedQuestion()
  expect(screen.queryByRole('button', { name: /save as report/i })).toBeNull()
})

test('a principal holding reports:write is offered Save as report', async () => {
  storeAuthenticatedSession()
  stubChatWithEvidence(
    principalContext({ permissions: ['chat:use', 'reports:read', 'reports:write'] }),
  )
  render(<App />)

  await askOneGroundedQuestion()
  expect(await screen.findByRole('button', { name: /save as report/i })).toBeTruthy()
})
