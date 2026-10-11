/**
 * OPS1 OpenJM Control Center (platform operator plane).
 *
 * Proves the operator surface is gated on a server-resolved platform
 * capability (never on a tenant role), that it renders an operator
 * presentation rather than raw JSON, and that the browser never receives the
 * monitoring `OPENJM_OPS_TOKEN` in any form.
 */
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'

import OperatorConsole from './OperatorConsole'
import { canOperatePlatform, navVisibilityFor } from './App'
import type { Principal } from './auth'

// Vite's `import.meta.glob` is provided by the bundler at build/test time. The
// project's tsconfig does not pull in `vite/client`, so the shape used here is
// declared locally rather than widening the app's global types.
declare global {
  interface ImportMeta {
    glob: (
      pattern: string,
      options?: { query?: string; import?: string; eager?: boolean },
    ) => Record<string, string>
  }
}

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    }),
  )
}

function principal(overrides: Partial<Principal> = {}): Principal {
  return {
    principal_id: 'p-1',
    tenant_id: 'tnt-1',
    subject: 'oidc|1',
    role: 'owner',
    auth_method: 'oidc',
    email: null,
    display_name: null,
    permissions: ['chat:use', 'tenant:admin'],
    department_ids: [],
    group_ids: [],
    steward_scopes: [],
    platform_capabilities: [],
    ...overrides,
  } as Principal
}

const OVERVIEW = {
  release: { product: 'OpenJM Enterprise AI', version: '0.2.0', release_train: 'REL1', release_id: 'rel1-qa1' },
  auth_mode: 'oidc',
  profile: 'production',
  ready: true,
  components: {
    database: { status: 'ok', latency_ms: 3 },
    migrations: { status: 'ok', revision: '0021_support_content_scope', head: '0021_support_content_scope', drift: false },
    storage: { status: 'ok', writable: true, free_mb: 4096 },
    knowledge: { status: 'ok', backend: 'DB-GPT', vector_writable: true },
    model_provider: { status: 'ok' },
    scheduler: { status: 'ok', enabled: true, active_schedules: 0 },
    connectors: { status: 'ok', instances: 0 },
    notifications: { status: 'ok', pending: 0 },
  },
  model: { provider_mode: 'openai_compatible', model_name: 'glm-5.3-flash', fallback_policy: 'none', configured: true, health: 'ok' },
  fleet: { tenants: 2, suspended: 0, operators: 1, active_content_delegations: 0 },
  alerts: [],
}

function platformFetchMock() {
  return vi.fn((input: RequestInfo | URL) => {
    const url = typeof input === 'string' ? input : input.toString()
    if (url.startsWith('/api/platform/operations/overview')) return jsonResponse(OVERVIEW)
    if (url.startsWith('/api/platform/operations/tenants'))
      return jsonResponse({ tenants: [], total: 0, limit: 50, offset: 0, has_more: false })
    if (url.startsWith('/api/platform/operations/usage')) return jsonResponse({ tenants: [] })
    if (url.startsWith('/api/platform/audit/summary'))
      return jsonResponse({ total: 0, denied: 0, by_family: {}, recent: [] })
    if (url.startsWith('/api/platform/audit'))
      return jsonResponse({ events: [], total: 0, limit: 50, offset: 0, has_more: false })
    if (url.startsWith('/api/platform/security'))
      return jsonResponse({
        operators: [],
        operator_count: 0,
        support_delegations: [],
        active_content_delegations: 0,
        expiring_delegations: 0,
        recent_privileged_actions: [],
        denied_categories: [],
        suspended_tenants: 0,
      })
    if (url.startsWith('/api/platform/operations/releases'))
      return jsonResponse({ release: OVERVIEW.release, deployments: [], deployment_count: 1 })
    if (url.startsWith('/api/platform/diagnostics'))
      return jsonResponse({
        components: OVERVIEW.components,
        ready: true,
        traces: [],
        traces_total: 0,
        limit: 50,
        offset: 0,
        failure_categories: [],
        operational_logs: {
          persistence: 'stream',
          format: 'json',
          location: 'process stdout',
          aggregated_into_metadata_db: false,
          external_aggregation: 'optional',
          guidance: 'Use the audit trail and traces in this console.',
        },
      })
    throw new Error(`Unexpected request: ${url}`)
  })
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

// ---------------------------------------------------------------------------
// Navigation gating
// ---------------------------------------------------------------------------

test('operator navigation requires an explicit platform capability', () => {
  expect(canOperatePlatform(null)).toBe(false)

  // A tenant owner/admin holds tenant authority but no platform capability.
  const tenantOwner = principal({ role: 'owner', permissions: ['tenant:admin'] })
  expect(canOperatePlatform(tenantOwner)).toBe(false)
  expect(navVisibilityFor(tenantOwner).platform).toBe(false)
  // Their tenant administration entry is unaffected.
  expect(navVisibilityFor(tenantOwner).administration).toBe(true)

  const tenantAdmin = principal({ role: 'admin', permissions: ['tenant:admin'] })
  expect(navVisibilityFor(tenantAdmin).platform).toBe(false)

  // A metadata observer sees the control center and no tenant administration entry.
  const observer = principal({
    role: 'viewer',
    permissions: ['chat:use'],
    platform_capabilities: ['platform:metadata:read'],
  })
  expect(canOperatePlatform(observer)).toBe(true)
  expect(navVisibilityFor(observer).platform).toBe(true)
})

test('platform capability never widens a tenant entry', () => {
  const observer = principal({
    role: 'viewer',
    permissions: ['chat:use'],
    platform_capabilities: ['platform:metadata:read'],
  })
  const nav = navVisibilityFor(observer)
  expect(nav.administration).toBe(false)
  expect(nav.knowledge).toBe(false)
  expect(nav.data).toBe(false)
})

// ---------------------------------------------------------------------------
// Surface
// ---------------------------------------------------------------------------

test('control center renders an operator presentation, not raw JSON', async () => {
  vi.stubGlobal('fetch', platformFetchMock())
  render(<OperatorConsole />)

  await screen.findByRole('heading', { name: 'Control Center' })
  for (const label of ['Overview', 'Tenants', 'Usage', 'Audit', 'Diagnostics', 'Security', 'Releases']) {
    expect(screen.getByRole('button', { name: label })).toBeTruthy()
  }

  await waitFor(() => expect(screen.getByText('glm-5.3-flash')).toBeTruthy())
  expect(screen.getByText('Core readiness')).toBeTruthy()
  expect(screen.getByText(/REL1/)).toBeTruthy()

  // A rendered presentation, never a raw payload dump.
  expect(document.body.textContent).not.toContain('"components":')
  expect(document.body.textContent).not.toContain('{"release"')
})

test('the operator surface never renders the monitoring ops token', async () => {
  vi.stubGlobal('fetch', platformFetchMock())
  render(<OperatorConsole />)
  await screen.findByRole('heading', { name: 'Control Center' })
  const needle = ['OPENJM', 'OPS', 'TOKEN'].join('_')
  expect(document.documentElement.innerHTML).not.toContain(needle)
})

// ---------------------------------------------------------------------------
// The browser must never receive the ops token
// ---------------------------------------------------------------------------

/**
 * The needle is assembled at runtime so this test file does not itself contain
 * the literal it searches for. Every frontend source module is scanned; the
 * built bundle is checked separately during OPS1 live acceptance.
 */
test('no frontend source module references the OPS monitoring token', () => {
  const needle = ['OPENJM', 'OPS', 'TOKEN'].join('_')
  const lowercaseNeedle = ['ops', 'token'].join('_')

  const modules = import.meta.glob('./*.{ts,tsx}', {
    query: '?raw',
    import: 'default',
    eager: true,
  }) as Record<string, string>

  const offenders = Object.entries(modules)
    .filter(([, text]) => text.includes(needle) || text.toLowerCase().includes(lowercaseNeedle))
    .map(([file]) => file)

  expect(offenders).toEqual([])
})
