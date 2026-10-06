import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import OperationsPanel from './OperationsPanel'

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    }),
  )
}

function stubFetch(fetchMock: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>) {
  vi.stubGlobal('fetch', vi.fn(fetchMock))
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

const OPERATIONS = [
  'connector.incremental_sync',
  'connector.reconcile',
  'connector.sync',
  'notification.retry',
]

const schedule = {
  id: 'sched-1',
  name: 'Workspace incremental',
  schedule_type: 'interval',
  operation: 'connector.incremental_sync',
  status: 'active',
  enabled: true,
  timezone: 'UTC',
  interval_seconds: 900,
  next_run_at: '2026-10-06T12:00:00Z',
  last_run_at: '2026-10-06T11:45:00Z',
  last_result: 'succeeded',
  last_failure_category: null as string | null,
  misfire_policy: 'skip',
  max_retries: 2,
}

const channel = {
  id: 'chan-1',
  name: 'In-app alerts',
  channel_type: 'in_app',
  status: 'active',
  enabled: true,
  last_delivery_at: '2026-10-06T11:00:00Z',
  last_failure_category: null as string | null,
}

const notification = {
  id: 'note-1',
  category: 'connector_failure',
  subject: 'Workspace sync failed',
  status: 'pending',
  attempts: 1,
  max_attempts: 3,
  resource_type: 'connector_resource',
  resource_id: 'conn-1:page-1',
  failure_category: null as string | null,
  created_at: '2026-10-06T11:00:00Z',
}

function baseMock(overrides: Partial<Record<string, (init?: RequestInit) => Promise<Response>>> = {}) {
  return (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method || 'GET'
    const key = `${method} ${url}`
    if (overrides[key]) return overrides[key]!(init)
    if (url === '/api/operations/schedules/operations') return jsonResponse({ operations: OPERATIONS })
    if (url === '/api/operations/schedules' && method === 'GET') {
      return jsonResponse({ schedules: [schedule] })
    }
    if (url === '/api/operations/notification-channels' && method === 'GET') {
      return jsonResponse({ channel_types: ['in_app'], channels: [channel] })
    }
    if (url === '/api/operations/notifications') return jsonResponse({ notifications: [notification] })
    throw new Error(`Unexpected request: ${method} ${url}`)
  }
}

test('lists schedules from the registered vocabulary and toggles state', async () => {
  let schedules = [schedule]
  let posted: Record<string, unknown> | null = null
  stubFetch(
    baseMock({
      'GET /api/operations/schedules': () => jsonResponse({ schedules }),
      'POST /api/operations/schedules/sched-1/state': (init) => {
        posted = JSON.parse(String(init?.body))
        schedules = [{ ...schedule, enabled: false, status: 'paused' }]
        return jsonResponse(schedules[0])
      },
    }),
  )

  render(<OperationsPanel />)

  expect(await screen.findByText('Workspace incremental')).toBeTruthy()
  expect(screen.getByText('connector.incremental_sync · every 900s · UTC')).toBeTruthy()
  expect(screen.getByText('Last result: succeeded')).toBeTruthy()

  const operationSelect = screen.getByLabelText('Operation') as HTMLSelectElement
  expect(Array.from(operationSelect.options).map((option) => option.value)).toEqual(OPERATIONS)

  fireEvent.click(screen.getByRole('button', { name: 'Disable' }))

  await waitFor(() => expect(posted).toEqual({ enabled: false, status: null }))
  expect(await screen.findByText('Disabled')).toBeTruthy()
})

test('creates a schedule against the constrained operation vocabulary', async () => {
  let posted: Record<string, unknown> | null = null
  stubFetch(
    baseMock({
      'POST /api/operations/schedules': (init) => {
        posted = JSON.parse(String(init?.body))
        return jsonResponse({ ...schedule, id: 'sched-2', name: 'Reconcile sweep' }, 201)
      },
      'GET /api/operations/schedules': () =>
        jsonResponse({ schedules: [schedule, { ...schedule, id: 'sched-2', name: 'Reconcile sweep' }] }),
    }),
  )

  render(<OperationsPanel />)
  await screen.findByText('Workspace incremental')

  fireEvent.change(screen.getByLabelText('Schedule name'), { target: { value: 'Reconcile sweep' } })
  fireEvent.change(screen.getByLabelText('Operation'), {
    target: { value: 'connector.reconcile' },
  })
  fireEvent.change(screen.getByLabelText('Interval (seconds)'), { target: { value: '120' } })
  fireEvent.change(screen.getByLabelText('Max retries'), { target: { value: '1' } })
  fireEvent.click(screen.getByRole('button', { name: 'Create schedule' }))

  await waitFor(() => expect(posted).not.toBeNull())
  expect(posted).toMatchObject({
    name: 'Reconcile sweep',
    schedule_type: 'interval',
    operation: 'connector.reconcile',
    interval_seconds: 120,
    timezone_name: 'UTC',
    misfire_policy: 'skip',
    max_retries: 1,
    target: null,
  })
  expect(await screen.findByText('Schedule "Reconcile sweep" created')).toBeTruthy()
})

test('creates a notification channel and lists notifications', async () => {
  let posted: Record<string, unknown> | null = null
  stubFetch(
    baseMock({
      'POST /api/operations/notification-channels': (init) => {
        posted = JSON.parse(String(init?.body))
        return jsonResponse({ id: 'chan-2', name: 'Ops alerts', channel_type: 'in_app' }, 201)
      },
      'GET /api/operations/notification-channels': () =>
        jsonResponse({
          channel_types: ['in_app'],
          channels: [channel, { ...channel, id: 'chan-2', name: 'Ops alerts' }],
        }),
    }),
  )

  render(<OperationsPanel />)
  await screen.findByText('In-app alerts')

  expect(screen.getByText('Workspace sync failed')).toBeTruthy()
  expect(screen.getByText('1/3')).toBeTruthy()

  fireEvent.change(screen.getByLabelText('Channel name'), { target: { value: 'Ops alerts' } })
  fireEvent.click(screen.getByRole('button', { name: 'Create channel' }))

  await waitFor(() =>
    expect(posted).toEqual({ name: 'Ops alerts', channel_type: 'in_app', config: {} }),
  )
  expect(await screen.findByText('Notification channel created')).toBeTruthy()
  expect(await screen.findByText('Ops alerts')).toBeTruthy()
})