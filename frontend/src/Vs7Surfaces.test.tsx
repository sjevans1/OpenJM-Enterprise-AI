/**
 * VS7 surfaces are mounted into the existing single-component App. This test
 * proves the panels are reachable from the sidebar and that their data is only
 * fetched when the surface is opened, so the rest of the workspace is unaffected.
 */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
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

const connector = {
  id: 'conn-1',
  name: 'Workspace prod',
  connector_type: 'workspace',
  connector_version: '1.0.0',
  connector_key: 'workspace@1.0.0',
  display_name: 'OpenJM Workspace',
  enabled: true,
  status: 'active',
  health_status: 'healthy',
  health_detail: null,
  config: {},
  has_credential: true,
  last_successful_connection_at: null,
  last_successful_sync_at: '2026-10-06T10:05:00Z',
  last_reconciliation_at: null,
  last_failure_category: null,
  last_failure_at: null,
  created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-06T10:05:00Z',
}

const schedule = {
  id: 'sched-1',
  name: 'Workspace incremental',
  schedule_type: 'interval',
  operation: 'connector.incremental_sync',
  status: 'active',
  enabled: true,
  timezone: 'UTC',
  interval_seconds: 900,
  next_run_at: null,
  last_run_at: null,
  last_result: null,
  last_failure_category: null,
  misfire_policy: 'skip',
  max_retries: 2,
}

beforeEach(() => {
  localStorage.clear()
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

test('connectors and operations surfaces mount from the sidebar and load on demand', async () => {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input)
    if (url === '/api/auth/config') return jsonResponse(DEV_AUTH_CONFIG)
    if (['/api/conversations', '/api/knowledge/documents', '/api/data/sources', '/api/reports'].includes(url)) {
      return jsonResponse([])
    }
    if (url === '/api/connectors/types') return jsonResponse({ types: [] })
    if (url === '/api/connectors') return jsonResponse({ connectors: [connector] })
    if (url === '/api/operations/schedules/operations') {
      return jsonResponse({ operations: ['connector.incremental_sync'] })
    }
    if (url === '/api/operations/schedules') return jsonResponse({ schedules: [schedule] })
    if (url === '/api/operations/notification-channels') {
      return jsonResponse({ channel_types: ['in_app'], channels: [] })
    }
    if (url === '/api/operations/notifications') return jsonResponse({ notifications: [] })
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)

  render(<App />)
  await screen.findByRole('button', { name: 'New conversation' })

  // The VS7 endpoints are not touched until their surface is opened.
  expect(fetchMock.mock.calls.some(([input]) => String(input).startsWith('/api/connectors'))).toBe(false)
  expect(fetchMock.mock.calls.some(([input]) => String(input).startsWith('/api/operations'))).toBe(false)

  fireEvent.click(screen.getByRole('button', { name: 'Connectors' }))
  expect(await screen.findByText('Workspace prod')).toBeTruthy()
  expect(screen.getByRole('heading', { name: 'Connectors' })).toBeTruthy()

  fireEvent.click(screen.getByRole('button', { name: 'Operations' }))
  expect(await screen.findByText('Workspace incremental')).toBeTruthy()
  expect(screen.getByLabelText('Operation')).toBeTruthy()
})