import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import ConnectorsPanel from './ConnectorsPanel'

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

const workspaceType = {
  type_id: 'workspace',
  version: '1.0.0',
  key: 'workspace@1.0.0',
  display_name: 'OpenJM Workspace',
  description: 'Read-only access to Workspace documents and events.',
  capabilities: ['documents', 'events'],
  authorization_behavior: 'provider_current_state',
  requires_user_mapping: true,
  credential_kind: 'bearer_token',
  credential_fields: ['token'],
  credential_rotation: 'replace',
  event_support: true,
  reconciliation_support: true,
  incremental_support: true,
  supports_test_connection: true,
  timeout_seconds: 30,
  max_retries: 3,
  initial_sync_limit: 500,
  operations: [],
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
  health_detail: null as string | null,
  config: { base_url: 'https://workspace.example.test' },
  has_credential: true,
  last_successful_connection_at: '2026-10-06T10:00:00Z',
  last_successful_sync_at: '2026-10-06T10:05:00Z',
  last_reconciliation_at: null as string | null,
  last_failure_category: null as string | null,
  last_failure_at: null as string | null,
  created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-06T10:05:00Z',
  runs: [] as unknown[],
}

function baseMock(overrides: Partial<Record<string, (init?: RequestInit) => Promise<Response>>> = {}) {
  return (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method || 'GET'
    const key = `${method} ${url}`
    if (overrides[key]) return overrides[key]!(init)
    if (url === '/api/connectors/types') return jsonResponse({ types: [workspaceType] })
    if (url === '/api/connectors' && method === 'GET') return jsonResponse({ connectors: [connector] })
    if (url === '/api/connectors/conn-1' && method === 'GET') return jsonResponse(connector)
    if (url === '/api/connectors/conn-1/resources') return jsonResponse({ resources: [] })
    if (url === '/api/connectors/conn-1/mappings') return jsonResponse({ mappings: [] })
    throw new Error(`Unexpected request: ${method} ${url}`)
  }
}

test('lists connector state and surfaces a failed connection test', async () => {
  const failing = { ...connector, health_status: 'error', last_failure_category: 'auth' }
  stubFetch(
    baseMock({
      'GET /api/connectors': () => jsonResponse({ connectors: [failing] }),
      'GET /api/connectors/conn-1': () => jsonResponse(failing),
      'POST /api/connectors/conn-1/test': () =>
        jsonResponse({ ok: false, detail: 'Provider rejected the credential (401)', connector: failing }),
    }),
  )

  render(<ConnectorsPanel />)

  expect(await screen.findByText('Workspace prod')).toBeTruthy()
  expect(screen.getByText('OpenJM Workspace · v1.0.0')).toBeTruthy()
  expect(screen.getByText('Enabled')).toBeTruthy()
  expect(screen.getByText('error')).toBeTruthy()
  expect(screen.getByText(/Last failure: auth/)).toBeTruthy()
  expect(screen.getByText(/Last successful sync:/)).toBeTruthy()

  fireEvent.click(screen.getByRole('button', { name: /Workspace prod/ }))
  fireEvent.click(await screen.findByRole('button', { name: 'Test connection' }))

  expect(await screen.findByText('Connection failed')).toBeTruthy()
  expect(screen.getByText('Provider rejected the credential (401)')).toBeTruthy()
})

test('create submits non-secret config and a credential, then clears the credential', async () => {
  const created = { ...connector, id: 'conn-2', name: 'Workspace staging' }
  let present: Array<typeof connector> = [connector]
  let posted: Record<string, unknown> | null = null
  stubFetch(
    baseMock({
      'GET /api/connectors': () => jsonResponse({ connectors: present }),
      'POST /api/connectors': (init) => {
        posted = JSON.parse(String(init?.body))
        present = [connector, created]
        return jsonResponse(created, 201)
      },
      'GET /api/connectors/conn-2': () => jsonResponse(created),
      'GET /api/connectors/conn-2/resources': () => jsonResponse({ resources: [] }),
      'GET /api/connectors/conn-2/mappings': () => jsonResponse({ mappings: [] }),
    }),
  )

  render(<ConnectorsPanel />)
  await screen.findByText('Workspace prod')

  fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Workspace staging' } })
  fireEvent.change(screen.getByLabelText('Config key 1'), { target: { value: 'base_url' } })
  fireEvent.change(screen.getByLabelText('Config value 1'), {
    target: { value: 'https://staging.example.test' },
  })
  const tokenInput = screen.getByLabelText('token') as HTMLInputElement
  fireEvent.change(tokenInput, { target: { value: 'super-secret-token' } })
  fireEvent.click(screen.getByRole('button', { name: 'Create connector' }))

  await waitFor(() => expect(posted).not.toBeNull())
  expect(posted).toMatchObject({
    name: 'Workspace staging',
    connector_type: 'workspace',
    version: '1.0.0',
    config: { base_url: 'https://staging.example.test' },
    credential: { token: 'super-secret-token' },
    credential_label: 'primary',
  })

  // The credential field is cleared the moment the request completes, and no
  // part of it is retained in the document.
  await waitFor(() => expect((screen.getByLabelText('token') as HTMLInputElement).value).toBe(''))
  expect(document.body.innerHTML).not.toContain('super-secret-token')
  expect(Object.values(localStorage)).not.toContain('super-secret-token')
})

test('sync posts the chosen run type and shows counters plus failure category', async () => {
  const run = {
    id: 'run-1',
    run_type: 'reconcile',
    status: 'failed',
    started_at: '2026-10-06T11:00:00Z',
    finished_at: '2026-10-06T11:00:02Z',
    items_scanned: 12,
    items_created: 0,
    items_updated: 3,
    items_deleted: 0,
    items_quarantined: 1,
    items_skipped: 8,
    failure_category: 'network',
    detail: 'timed out',
  }
  let posted: Record<string, unknown> | null = null
  stubFetch(
    baseMock({
      'GET /api/connectors/conn-1': () => jsonResponse({ ...connector, runs: [run] }),
      'POST /api/connectors/conn-1/sync': (init) => {
        posted = JSON.parse(String(init?.body))
        return jsonResponse(run)
      },
    }),
  )

  render(<ConnectorsPanel />)
  fireEvent.click(await screen.findByRole('button', { name: /Workspace prod/ }))
  await screen.findByRole('button', { name: 'Sync' })

  fireEvent.change(screen.getByLabelText('Run type'), { target: { value: 'reconcile' } })
  fireEvent.change(screen.getByLabelText('Limit (optional)'), { target: { value: '50' } })
  fireEvent.click(screen.getByRole('button', { name: 'Sync' }))

  await waitFor(() => expect(posted).toEqual({ run_type: 'reconcile', limit: 50 }))
  expect(await screen.findByText('Sync run reconcile failed (network)')).toBeTruthy()
  expect(screen.getByText('network')).toBeTruthy()
  expect(screen.getByText('12')).toBeTruthy()
})

test('shows resources and manages user mappings', async () => {
  const resource = {
    id: 'res-1',
    external_id: 'page-42',
    resource_type: 'page',
    title: 'Quarterly plan',
    external_revision: 'rev-2',
    lifecycle_state: 'active',
    permission_state: 'restricted',
    quarantine_reason: 'permission_revoked',
    document_id: 'doc-9',
    last_observed_at: '2026-10-06T09:00:00Z',
    last_synced_at: '2026-10-06T09:01:00Z',
    last_reconciled_at: null,
    provenance: {},
  }
  let mappings: unknown[] = [
    {
      id: 'map-1',
      principal_id: 'principal-a',
      external_user_id: 'ext-1',
      status: 'active',
      created_at: '2026-10-05T00:00:00Z',
      revoked_at: null,
    },
  ]
  let postedMapping: Record<string, unknown> | null = null
  stubFetch(
    baseMock({
      'GET /api/connectors/conn-1/resources': () => jsonResponse({ resources: [resource] }),
      'GET /api/connectors/conn-1/mappings': () => jsonResponse({ mappings }),
      'POST /api/connectors/conn-1/mappings': (init) => {
        postedMapping = JSON.parse(String(init?.body))
        mappings = [
          ...mappings,
          {
            id: 'map-2',
            principal_id: 'principal-b',
            external_user_id: 'ext-2',
            status: 'active',
            created_at: '2026-10-06T12:00:00Z',
            revoked_at: null,
          },
        ]
        return jsonResponse(mappings[mappings.length - 1], 201)
      },
    }),
  )

  render(<ConnectorsPanel />)
  fireEvent.click(await screen.findByRole('button', { name: /Workspace prod/ }))

  expect(await screen.findByText('page-42')).toBeTruthy()
  expect(screen.getByText('Quarterly plan')).toBeTruthy()
  expect(screen.getAllByText('active').length).toBeGreaterThan(0)
  expect(screen.getByText('restricted')).toBeTruthy()
  expect(screen.getByText('permission_revoked')).toBeTruthy()
  expect(screen.getByText('ext-1')).toBeTruthy()

  fireEvent.change(screen.getByLabelText('OpenJM principal id'), {
    target: { value: 'principal-b' },
  })
  fireEvent.change(screen.getByLabelText('External user id'), { target: { value: 'ext-2' } })
  fireEvent.click(screen.getByRole('button', { name: 'Add mapping' }))

  await waitFor(() =>
    expect(postedMapping).toEqual({ principal_id: 'principal-b', external_user_id: 'ext-2' }),
  )
  expect(await screen.findByText('ext-2')).toBeTruthy()
})

test('rotates a credential without retaining the replacement value', async () => {
  let posted: Record<string, unknown> | null = null
  const rotated = { ...connector, has_credential: true }
  stubFetch(
    baseMock({
      'POST /api/connectors/conn-1/credentials/rotate': (init) => {
        posted = JSON.parse(String(init?.body))
        return jsonResponse({ rotated: true, credential_id: 'cred-2', version: 2, connector: rotated })
      },
    }),
  )

  render(<ConnectorsPanel />)
  fireEvent.click(await screen.findByRole('button', { name: /Workspace prod/ }))

  const replacement = await screen.findByLabelText('Replacement token')
  fireEvent.change(replacement, { target: { value: 'replacement-secret' } })
  fireEvent.click(screen.getByRole('button', { name: 'Rotate credential' }))

  await waitFor(() =>
    expect(posted).toEqual({ credential: { token: 'replacement-secret' }, label: 'rotated' }),
  )
  expect(await screen.findByText('Credential rotated to version 2')).toBeTruthy()
  await waitFor(() =>
    expect((screen.getByLabelText('Replacement token') as HTMLInputElement).value).toBe(''),
  )
  expect(document.body.innerHTML).not.toContain('replacement-secret')
})
