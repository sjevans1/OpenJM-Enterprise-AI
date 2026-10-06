/**
 * Browser identity integration tests.
 *
 * These assert the browser-side contract only. Authorization itself is the
 * server's job, so nothing here proves access: the tests prove the SPA carries
 * the right credential, reacts correctly to authentication outcomes, and never
 * invents an identity of its own.
 */
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import {
  authorizedFetch,
  beginLogin,
  bootstrapAuth,
  clearSession,
  completeCallback,
  exchangeProviderToken,
  hasUsableSession,
  logout,
  onAuthLost,
  readSession,
} from './auth'

const SESSION_KEY = 'openjm.session'
const PENDING_KEY = 'openjm.oidc.pending'

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
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

function storeSession(overrides: Record<string, unknown> = {}) {
  const session = {
    token: 'openjm-session-token',
    expiresAt: Date.now() + 3_600_000,
    principalId: 'principal-a',
    tenantId: 'tenant-a',
    role: 'owner',
    permissions: ['reports:read'],
    ...overrides,
  }
  window.sessionStorage.setItem(SESSION_KEY, JSON.stringify(session))
  return session
}

/** jsdom cannot navigate, so swap in a location object we can observe. */
function stubLocation(overrides: Record<string, unknown> = {}) {
  const original = window.location
  const assigned: string[] = []
  const replacement = {
    origin: 'http://localhost:5173',
    pathname: '/',
    search: '',
    assign: (url: string) => {
      assigned.push(url)
    },
    ...overrides,
  }
  Object.defineProperty(window, 'location', {
    value: replacement,
    writable: true,
    configurable: true,
  })
  return {
    assigned,
    restore: () =>
      Object.defineProperty(window, 'location', {
        value: original,
        writable: true,
        configurable: true,
      }),
  }
}

beforeEach(() => {
  window.sessionStorage.clear()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

// ---------------------------------------------------------------------------
// 1. Unauthenticated application state
// ---------------------------------------------------------------------------

test('an OIDC deployment with no session reports unauthenticated, not dev', async () => {
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(jsonResponse(oidcConfig()))))

  const state = await bootstrapAuth()

  expect(state.status).toBe('unauthenticated')
  expect(readSession()).toBeNull()
})

test('a session the server no longer honours reports unauthenticated', async () => {
  storeSession()
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    if (String(input) === '/api/auth/config') return Promise.resolve(jsonResponse(oidcConfig()))
    if (String(input) === '/api/auth/me') {
      return Promise.resolve(jsonResponse({ detail: 'Session has been revoked' }, 401))
    }
    throw new Error(`unexpected ${String(input)}`)
  })
  vi.stubGlobal('fetch', fetchMock)

  const state = await bootstrapAuth()

  expect(state.status).toBe('unauthenticated')
  // The dead credential is not kept around.
  expect(readSession()).toBeNull()
})

// ---------------------------------------------------------------------------
// 2. Login initiation
// ---------------------------------------------------------------------------

test('login initiation builds an authorization code + PKCE request and navigates', async () => {
  const fetchMock = vi.fn(() => Promise.resolve(jsonResponse(oidcConfig())))
  vi.stubGlobal('fetch', fetchMock)
  const location = stubLocation()
  try {
    await beginLogin()

    expect(location.assigned).toHaveLength(1)
    const url = new URL(location.assigned[0])
    expect(url.origin + url.pathname).toBe(
      'https://idp.example.test/realms/openjm/protocol/openid-connect/auth',
    )
    expect(url.searchParams.get('client_id')).toBe('openjm-enterprise-ai')
    expect(url.searchParams.get('response_type')).toBe('code')
    expect(url.searchParams.get('redirect_uri')).toBe('http://localhost:5173/auth/callback')
    expect(url.searchParams.get('code_challenge_method')).toBe('S256')
    expect(url.searchParams.get('code_challenge')).toBeTruthy()
    // The verifier itself must never travel in the front channel.
    expect(url.searchParams.get('code_challenge')).not.toBe(
      url.searchParams.get('code_verifier'),
    )
    expect(url.searchParams.get('code_verifier')).toBeNull()

    const pending = JSON.parse(window.sessionStorage.getItem(PENDING_KEY) || '{}')
    expect(pending.state).toBe(url.searchParams.get('state'))
    expect(pending.verifier).toBeTruthy()
  } finally {
    location.restore()
  }
})

test('login initiation refuses when the deployment is not in OIDC mode', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.resolve(jsonResponse(oidcConfig({ auth_mode: 'dev' })))),
  )

  await expect(beginLogin()).rejects.toThrow(/does not use OIDC/)
})

test('the callback exchange rejects a state that does not match', async () => {
  window.sessionStorage.setItem(
    PENDING_KEY,
    JSON.stringify({ state: 'expected-state', verifier: 'v', redirectUri: 'http://x/auth/callback' }),
  )
  const fetchMock = vi.fn(() => Promise.resolve(jsonResponse(oidcConfig())))
  vi.stubGlobal('fetch', fetchMock)

  await expect(completeCallback('?code=abc&state=forged-state')).rejects.toThrow(/state did not match/)
  // A forged callback must not produce either a session or a network exchange.
  expect(readSession()).toBeNull()
  expect(fetchMock).not.toHaveBeenCalled()
})

test('the callback exchange establishes an OpenJM session', async () => {
  window.sessionStorage.setItem(
    PENDING_KEY,
    JSON.stringify({ state: 'st', verifier: 'vf', redirectUri: 'http://localhost:5173/auth/callback' }),
  )
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    expect(String(input)).toBe('/api/auth/oidc/callback')
    const body = JSON.parse(String(init?.body))
    expect(body.code).toBe('the-code')
    expect(body.code_verifier).toBe('vf')
    return Promise.resolve(
      jsonResponse({
        token: 'minted-session',
        expires_in: 3600,
        principal_id: 'principal-a',
        tenant_id: 'tenant-a',
        role: 'member',
        permissions: [],
      }),
    )
  })
  vi.stubGlobal('fetch', fetchMock)

  const session = await completeCallback('?code=the-code&state=st')

  expect(session.token).toBe('minted-session')
  expect(readSession()?.tenantId).toBe('tenant-a')
  // The one-time request state is consumed.
  expect(window.sessionStorage.getItem(PENDING_KEY)).toBeNull()
})

test('the token exchange establishes a session and does not leak the provider token', async () => {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    expect(String(input)).toBe('/api/auth/token/exchange')
    expect(JSON.parse(String(init?.body)).token).toBe('provider-jwt')
    return Promise.resolve(
      jsonResponse({
        token: 'openjm-session',
        expires_in: 60,
        principal_id: 'p',
        tenant_id: 't',
        role: 'member',
        permissions: [],
      }),
    )
  })
  vi.stubGlobal('fetch', fetchMock)

  const session = await exchangeProviderToken('provider-jwt')

  expect(session.token).toBe('openjm-session')
  expect(JSON.stringify(readSession())).not.toContain('provider-jwt')
})

// ---------------------------------------------------------------------------
// 3. Authenticated request carries the credential
// ---------------------------------------------------------------------------

test('a protected request carries the OpenJM session credential and nothing else', async () => {
  storeSession()
  const fetchMock = vi.fn(
    (_input: RequestInfo | URL, _init?: RequestInit) => Promise.resolve(jsonResponse([])),
  )
  vi.stubGlobal('fetch', fetchMock)

  await authorizedFetch('/api/reports', { headers: { Accept: 'application/json' } })

  const init = fetchMock.mock.calls[0][1] as RequestInit
  const headers = new Headers(init.headers)
  expect(headers.get('Authorization')).toBe('Bearer openjm-session-token')
  // The browser never asserts its own tenant or role: the server derives both.
  expect(headers.get('X-OpenJM-Tenant')).toBeNull()
  expect(headers.get('X-OpenJM-Role')).toBeNull()
  expect(headers.get('X-OpenJM-Principal')).toBeNull()
  expect(new Set([...headers.keys()].map((key) => key.toLowerCase()))).toEqual(
    new Set(['authorization', 'accept']),
  )
})

test('an unauthenticated request is sent without a credential', async () => {
  const fetchMock = vi.fn(
    (_input: RequestInfo | URL, _init?: RequestInit) => Promise.resolve(jsonResponse([])),
  )
  vi.stubGlobal('fetch', fetchMock)

  await authorizedFetch('/api/reports')

  const headers = new Headers((fetchMock.mock.calls[0][1] as RequestInit).headers)
  expect(headers.get('Authorization')).toBeNull()
})

// ---------------------------------------------------------------------------
// 4. 401 handling
// ---------------------------------------------------------------------------

test('a 401 clears local authentication state and notifies the application', async () => {
  storeSession()
  const listener = vi.fn()
  const unsubscribe = onAuthLost(listener)
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.resolve(jsonResponse({ detail: 'Not authenticated' }, 401))),
  )

  const response = await authorizedFetch('/api/reports')

  expect(response.status).toBe(401)
  expect(readSession()).toBeNull()
  expect(listener).toHaveBeenCalledTimes(1)
  unsubscribe()
})

// ---------------------------------------------------------------------------
// 5. 403 is not an authentication outcome
// ---------------------------------------------------------------------------

test('a 403 is surfaced but does not clear the session or report auth loss', async () => {
  storeSession()
  const listener = vi.fn()
  const unsubscribe = onAuthLost(listener)
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.resolve(jsonResponse({ detail: 'Permission denied' }, 403))),
  )

  const response = await authorizedFetch('/api/reports')

  expect(response.status).toBe(403)
  // Authenticated, just not permitted: dropping the session here would be wrong.
  expect(readSession()).not.toBeNull()
  expect(listener).not.toHaveBeenCalled()
  unsubscribe()
})

// ---------------------------------------------------------------------------
// 6. Logout
// ---------------------------------------------------------------------------

test('logout revokes the session server side and clears client state', async () => {
  storeSession()
  const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
    const headers = new Headers(init?.headers)
    expect(headers.get('Authorization')).toBe('Bearer openjm-session-token')
    return Promise.resolve(jsonResponse({ logged_out: true }))
  })
  vi.stubGlobal('fetch', fetchMock)

  await logout()

  expect(String(fetchMock.mock.calls[0][0])).toBe('/api/auth/logout')
  expect(readSession()).toBeNull()
  expect(hasUsableSession()).toBe(false)
})

test('logout clears client state even when the revocation call fails', async () => {
  storeSession()
  vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new Error('network down'))))

  await logout()

  expect(readSession()).toBeNull()
})

test('clearing the session removes the credential and any pending login', () => {
  storeSession()
  window.sessionStorage.setItem(PENDING_KEY, JSON.stringify({ state: 's', verifier: 'v', redirectUri: 'r' }))

  clearSession()

  expect(window.sessionStorage.getItem(SESSION_KEY)).toBeNull()
  expect(window.sessionStorage.getItem(PENDING_KEY)).toBeNull()
})

// ---------------------------------------------------------------------------
// 7. Expired and revoked sessions cannot keep calling
// ---------------------------------------------------------------------------

test('an expired session is not presented to the server', async () => {
  storeSession({ expiresAt: Date.now() - 1000 })
  const listener = vi.fn()
  const unsubscribe = onAuthLost(listener)
  const fetchMock = vi.fn(
    (_input: RequestInfo | URL, _init?: RequestInit) => Promise.resolve(jsonResponse([])),
  )
  vi.stubGlobal('fetch', fetchMock)

  await authorizedFetch('/api/reports')

  const headers = new Headers((fetchMock.mock.calls[0][1] as RequestInit).headers)
  expect(headers.get('Authorization')).toBeNull()
  expect(readSession()).toBeNull()
  expect(listener).toHaveBeenCalled()
  unsubscribe()
})

test('a session inside the expiry skew is treated as expired', () => {
  storeSession({ expiresAt: Date.now() + 5_000 })
  expect(hasUsableSession()).toBe(false)
})

test('a session revoked mid-use stops being used for subsequent calls', async () => {
  storeSession()
  let revoked = false
  const seen: Array<string | null> = []
  const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
    const headers = new Headers(init?.headers)
    seen.push(headers.get('Authorization'))
    if (revoked) {
      return Promise.resolve(jsonResponse({ detail: 'Session has been revoked' }, 401))
    }
    return Promise.resolve(jsonResponse([]))
  })
  vi.stubGlobal('fetch', fetchMock)

  // Works while the server still honours the credential.
  expect((await authorizedFetch('/api/reports')).status).toBe(200)
  expect(seen[0]).toBe('Bearer openjm-session-token')

  revoked = true
  expect((await authorizedFetch('/api/reports')).status).toBe(401)

  // The revoked credential is dropped, so later calls carry no identity at all.
  expect(readSession()).toBeNull()
  expect((await authorizedFetch('/api/reports')).status).toBe(401)
  expect(seen[2]).toBeNull()
})

// ---------------------------------------------------------------------------
// 8. OIDC mode never silently falls back to a development identity
// ---------------------------------------------------------------------------

test('OIDC mode with an incomplete configuration does not fall back to dev', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.resolve(jsonResponse(oidcConfig({ oidc_configured: false })))),
  )

  const state = await bootstrapAuth()

  expect(state.status).toBe('unauthenticated')
  await expect(beginLogin()).rejects.toThrow(/not fully configured/)
})

test('an unreachable deployment fails closed rather than assuming dev', async () => {
  vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new Error('connection refused'))))

  const state = await bootstrapAuth()

  expect(state.status).toBe('error')
  expect(state.status).not.toBe('dev')
})

test('a non-JSON auth configuration fails closed rather than assuming dev', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(
      () =>
        new Response('<html>proxy error</html>', {
          status: 200,
          headers: { 'Content-Type': 'text/html' },
        }),
    ),
  )

  await expect(bootstrapAuth()).resolves.toMatchObject({ status: 'error' })
})

test('development mode is reported only when the server says so', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.resolve(jsonResponse(oidcConfig({ auth_mode: 'dev' })))),
  )

  const state = await bootstrapAuth()

  expect(state.status).toBe('dev')
})

// ---------------------------------------------------------------------------
// 9. Client-side tenant context cannot bypass server membership checks
// ---------------------------------------------------------------------------

test('the browser never derives a tenant claim from its stored session', async () => {
  storeSession({ tenantId: 'tenant-a' })
  const fetchMock = vi.fn(
    (_input: RequestInfo | URL, _init?: RequestInit) => Promise.resolve(jsonResponse([])),
  )
  vi.stubGlobal('fetch', fetchMock)

  await authorizedFetch('/api/conversations')

  const headers = new Headers((fetchMock.mock.calls[0][1] as RequestInit).headers)
  for (const name of [...headers.keys()]) {
    expect(name.toLowerCase()).not.toContain('tenant')
    expect(name.toLowerCase()).not.toContain('role')
    expect(name.toLowerCase()).not.toContain('permission')
  }
})

test('a stored session claiming a role grants nothing locally', async () => {
  // The SPA holds a role for display only. There is no client-side gate that
  // could be edited to widen access, so the only effect of tampering is a
  // rejected server call.
  storeSession({ role: 'owner', permissions: ['*'] })
  const fetchMock = vi.fn(
    (_input: RequestInfo | URL, _init?: RequestInit) => Promise.resolve(jsonResponse([])),
  )
  vi.stubGlobal('fetch', fetchMock)

  const response = await authorizedFetch('/api/reports')

  expect(response.status).toBe(200)
  const headers = new Headers((fetchMock.mock.calls[0][1] as RequestInit).headers)
  expect([...headers.keys()].map((key) => key.toLowerCase())).toEqual(['authorization'])
})
