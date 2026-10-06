/**
 * Application-level authentication state.
 *
 * These assert what the user actually gets: the application renders only when
 * this deployment is either in development mode or holds a live OpenJM session,
 * and the login state is reachable and honest when it is not.
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import App from './App'

const SESSION_KEY = 'openjm.session'

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    }),
  )
}

function oidcConfig(overrides: Record<string, unknown> = {}) {
  return {
    auth_mode: 'oidc',
    oidc_configured: true,
    authorization_endpoint: 'https://idp.example.test/realms/openjm/protocol/openid-connect/auth',
    issuer: 'https://idp.example.test/realms/openjm',
    client_id: 'openjm-enterprise-ai',
    tenant_header: 'X-OpenJM-Tenant',
    ...overrides,
  }
}

const principal = {
  principal_id: 'principal-a',
  tenant_id: 'tenant-a',
  subject: 'subject-a',
  role: 'owner',
  auth_method: 'oidc',
  email: 'owner@example.test',
  display_name: 'Owner Person',
  permissions: ['reports:read'],
}

function storeSession() {
  window.sessionStorage.setItem(
    SESSION_KEY,
    JSON.stringify({
      token: 'openjm-session-token',
      expiresAt: Date.now() + 3_600_000,
      principalId: 'principal-a',
      tenantId: 'tenant-a',
      role: 'owner',
      permissions: ['reports:read'],
    }),
  )
}

/** Serve the auth endpoints plus the workspace list endpoints the app calls. */
function workspaceFetch(overrides: { me?: () => Promise<Response> } = {}) {
  return vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url === '/api/auth/config') return jsonResponse(oidcConfig())
    if (url === '/api/auth/me') {
      return overrides.me ? overrides.me() : jsonResponse(principal)
    }
    if (url === '/api/conversations' || url === '/api/knowledge/documents' || url === '/api/data/sources') {
      expect(new Headers(init?.headers).get('Authorization')).toBe('Bearer openjm-session-token')
      return jsonResponse([])
    }
    return jsonResponse([])
  })
}

beforeEach(() => {
  window.sessionStorage.clear()
  window.history.replaceState({}, '', '/')
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

test('an OIDC deployment without a session shows sign-in and not the workspace', async () => {
  vi.stubGlobal('fetch', workspaceFetch())

  render(<App />)

  expect(await screen.findByRole('button', { name: 'Sign in' })).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'New conversation' })).toBeNull()
})

test('a live session renders the workspace and carries the credential', async () => {
  storeSession()
  const fetchMock = workspaceFetch()
  vi.stubGlobal('fetch', fetchMock)

  render(<App />)

  expect(await screen.findByRole('button', { name: 'New conversation' })).toBeTruthy()
  expect(screen.getByText('Owner Person')).toBeTruthy()
  // Role and tenant are shown for orientation only.
  expect(screen.getByText('owner · tenant-a')).toBeTruthy()
  await waitFor(() =>
    expect(fetchMock.mock.calls.some(([url]) => String(url) === '/api/conversations')).toBe(true),
  )
})

test('a 401 on a protected call returns the user to sign-in', async () => {
  // The credential is honoured for /me but has been revoked by the time the
  // first data call is made: the app must leave the workspace immediately.
  storeSession()
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/auth/config') return jsonResponse(oidcConfig())
      if (url === '/api/auth/me') return jsonResponse(principal)
      return jsonResponse({ detail: 'Not authenticated' }, 401)
    }),
  )

  render(<App />)

  expect(await screen.findByRole('button', { name: 'Sign in' })).toBeTruthy()
  expect(window.sessionStorage.getItem(SESSION_KEY)).toBeNull()
  expect(screen.getByText(/session has expired/i)).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'New conversation' })).toBeNull()
})

test('logout revokes the session and returns to sign-in', async () => {
  storeSession()
  const calls: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      calls.push(url)
      if (url === '/api/auth/config') return jsonResponse(oidcConfig())
      if (url === '/api/auth/me') return jsonResponse(principal)
      if (url === '/api/auth/logout') {
        expect(new Headers(init?.headers).get('Authorization')).toBe('Bearer openjm-session-token')
        return jsonResponse({ logged_out: true })
      }
      return jsonResponse([])
    }),
  )

  render(<App />)
  expect(await screen.findByRole('button', { name: 'New conversation' })).toBeTruthy()

  fireEvent.click(screen.getByRole('button', { name: 'Sign out' }))

  expect(await screen.findByRole('button', { name: 'Sign in' })).toBeTruthy()
  expect(calls).toContain('/api/auth/logout')
  expect(window.sessionStorage.getItem(SESSION_KEY)).toBeNull()
})

test('development mode renders the workspace with no credential', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/auth/config') return jsonResponse(oidcConfig({ auth_mode: 'dev' }))
      if (url === '/api/auth/me') throw new Error('dev mode must not ask for a principal')
      return jsonResponse([])
    }),
  )

  render(<App />)

  expect(await screen.findByRole('button', { name: 'New conversation' })).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'Sign in' })).toBeNull()
  // Development mode keeps the original local workspace readout.
  expect(screen.getByText('Local workspace')).toBeTruthy()
})

test('an unreachable deployment refuses access instead of assuming dev', async () => {
  vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new Error('connection refused'))))

  render(<App />)

  expect(await screen.findByRole('alert')).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'New conversation' })).toBeNull()
  expect(screen.queryByText('Local workspace')).toBeNull()
})

test('a session revoked server side cannot render the workspace', async () => {
  storeSession()
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/auth/config') return jsonResponse(oidcConfig())
      if (url === '/api/auth/me') return jsonResponse({ detail: 'Session has been revoked' }, 401)
      return jsonResponse([])
    }),
  )

  render(<App />)

  expect(await screen.findByRole('button', { name: 'Sign in' })).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'New conversation' })).toBeNull()
  expect(window.sessionStorage.getItem(SESSION_KEY)).toBeNull()
})

test('an incomplete OIDC configuration never renders the workspace', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/auth/config') return jsonResponse(oidcConfig({ oidc_configured: false }))
      return jsonResponse([])
    }),
  )

  render(<App />)

  expect(await screen.findByRole('button', { name: 'Sign in' })).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'New conversation' })).toBeNull()
})
