/**
 * BV3-B client administration UX.
 *
 * Proves the admin surface is business-oriented (Users, Departments, Groups,
 * Data Stewards, Data Access, Usage / Plan), that it never renders raw
 * infrastructure concepts, that the navigation entry reflects tenant
 * permissions, and that the surface is only opened on demand.
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'

import AdminPanel from './AdminPanel'
import App, { canAdminister } from './App'

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

const member = {
  principal_id: 'p-owner',
  subject: 'oidc|owner',
  role: 'owner',
  status: 'active',
  email: 'owner@acme.test',
  display_name: 'Ada Owner',
  department_ids: [],
  group_ids: [],
}

const department = {
  id: 'd-hr',
  slug: 'human-resources',
  name: 'Human Resources',
  status: 'active',
}

const group = {
  id: 'g-hr',
  slug: 'hr-team',
  name: 'HR Team',
  status: 'active',
  department_id: 'd-hr',
}

function adminFetchMock(extra?: (url: string, init?: RequestInit) => Response | undefined) {
  let departments = [department]
  return vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const custom = extra?.(url, init)
    if (custom) return Promise.resolve(custom)
    if (url === '/api/admin/members') return jsonResponse([member])
    if (url === '/api/admin/departments') {
      if (init?.method === 'POST') {
        const body = JSON.parse(String(init.body)) as { slug: string; name: string }
        const created = { id: `d-${body.slug}`, slug: body.slug, name: body.name, status: 'active' }
        departments = [...departments, created]
        return jsonResponse(created)
      }
      return jsonResponse(departments)
    }
    if (url === '/api/admin/groups') return jsonResponse([group])
    if (url === '/api/admin/stewards') {
      return jsonResponse([
        { principal_id: 'p-owner', scope_type: 'department', scope_id: 'd-hr', status: 'active' },
      ])
    }
    if (url === '/api/admin/data-access') {
      return jsonResponse({
        sources: [
          { id: 's-1', name: 'Payroll', classification: 'confidential', department_id: 'd-hr' },
        ],
        documents: [{ id: 'doc-1', title: 'Handbook.pdf', classification: 'internal' }],
      })
    }
    if (url === '/api/admin/usage') {
      return jsonResponse({
        tenant_id: 'tnt-1',
        usage: { event_count: 3, input_tokens: 120, output_tokens: 80, total_tokens: 200 },
        plan: null,
        entitlements: null,
        note: 'Plan and entitlement data arrive with M2/M3.',
      })
    }
    if (
      ['/api/conversations', '/api/knowledge/documents', '/api/data/sources', '/api/reports'].includes(
        url,
      )
    ) {
      return jsonResponse([])
    }
    if (url === '/api/auth/config') return jsonResponse(DEV_AUTH_CONFIG)
    throw new Error(`Unexpected request: ${url}`)
  })
}

beforeEach(() => {
  localStorage.clear()
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

test('administration navigation reflects tenant permissions', () => {
  expect(canAdminister(null)).toBe(false)
  expect(canAdminister({ role: 'viewer', permissions: ['chat:use'] })).toBe(false)
  expect(canAdminister({ role: 'editor', permissions: ['chat:use', 'knowledge:write'] })).toBe(false)
  expect(canAdminister({ role: 'admin', permissions: ['tenant:admin'] })).toBe(true)
  expect(canAdminister({ role: 'owner', permissions: ['tenant:admin'] })).toBe(true)
  // Role fallback keeps the entry available if the permission list is opaque.
  expect(canAdminister({ role: 'admin', permissions: [] })).toBe(true)
})

test('admin surface presents business sections and no infrastructure internals', async () => {
  vi.stubGlobal('fetch', adminFetchMock())
  render(<AdminPanel />)

  await screen.findByRole('heading', { name: 'Administration' })
  for (const label of [
    'Users',
    'Departments',
    'Groups',
    'Data Stewards',
    'Data Access',
    'Usage / Plan',
  ]) {
    expect(screen.getByRole('button', { name: label })).toBeTruthy()
  }

  // The default section lists people by a human name, not an internal id.
  await screen.findByText('Ada Owner')

  const text = document.body.textContent || ''
  for (const forbidden of [
    'scheduler',
    'credential',
    'operation id',
    'platform:',
    'capability',
    'api_key',
  ]) {
    expect(text.toLowerCase()).not.toContain(forbidden)
  }
})

test('a client administrator can create a department from the surface', async () => {
  vi.stubGlobal('fetch', adminFetchMock())
  render(<AdminPanel />)

  await screen.findByRole('heading', { name: 'Administration' })
  fireEvent.click(screen.getByRole('button', { name: 'Departments' }))
  await screen.findByText('Human Resources')

  fireEvent.change(screen.getByLabelText('Department name'), {
    target: { value: 'Finance' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Add department' }))

  await screen.findByText('Finance')
})

test('usage section states that plan data is not yet available', async () => {
  vi.stubGlobal('fetch', adminFetchMock())
  render(<AdminPanel />)

  await screen.findByRole('heading', { name: 'Administration' })
  fireEvent.click(screen.getByRole('button', { name: 'Usage / Plan' }))

  await screen.findByText(/Plan and entitlement data arrive with M2\/M3/)
})

test('the workspace exposes Administration on demand and keeps raw surfaces out of the nav', async () => {
  const fetchMock = adminFetchMock()
  vi.stubGlobal('fetch', fetchMock)

  render(<App />)
  await screen.findByRole('button', { name: 'New conversation' })

  // The admin endpoints are not touched until the surface is opened.
  expect(fetchMock.mock.calls.some(([input]) => String(input).startsWith('/api/admin'))).toBe(
    false,
  )

  fireEvent.click(screen.getByRole('button', { name: 'Administration' }))
  await screen.findByRole('heading', { name: 'Administration' })
  await waitFor(() =>
    expect(fetchMock.mock.calls.some(([input]) => String(input) === '/api/admin/members')).toBe(
      true,
    ),
  )

  // BV2/BV3-C: raw control-plane surfaces stay out of ordinary navigation.
  expect(screen.queryByRole('button', { name: 'Connectors' })).toBeNull()
  expect(screen.queryByRole('button', { name: 'Operations' })).toBeNull()

  // The Chat/Knowledge/Data/Hybrid selector is untouched by this phase.
  expect(screen.getByRole('button', { name: /Knowledge/ })).toBeTruthy()
})
