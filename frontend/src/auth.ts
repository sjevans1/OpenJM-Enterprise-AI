/**
 * OpenJM browser authentication.
 *
 * The backend owns identity. This module only drives the browser side of the
 * OpenJM-native OIDC flow and carries the resulting OpenJM session credential
 * onto protected requests:
 *
 *   /api/auth/config          public discovery, tells the SPA which mode is on
 *   /api/auth/oidc/callback   authorization code exchange, mints the session
 *   /api/auth/token/exchange  provider token for a session, for token clients
 *   /api/auth/me              server-resolved principal
 *   /api/auth/logout          session revocation
 *
 * It deliberately contains no authorization logic. Roles and permissions are
 * carried for display only; every protected decision is re-made by the server
 * from its own membership database on every request.
 */

export type AuthMode = 'dev' | 'oidc'

export type AuthConfig = {
  auth_mode: AuthMode | string
  oidc_configured: boolean
  authorization_endpoint: string | null
  issuer: string | null
  client_id: string | null
  tenant_header: string
}

export type Principal = {
  principal_id: string
  tenant_id: string
  subject: string
  role: string
  auth_method: string
  email: string | null
  display_name: string | null
  permissions: string[]
}

export type StoredSession = {
  token: string
  /** Epoch milliseconds. Derived from the server's expires_in. */
  expiresAt: number
  principalId: string
  tenantId: string
  role: string
  permissions: string[]
}

export class AuthError extends Error {
  status: number
  code: string

  constructor(message: string, status: number, code = 'auth_error') {
    super(message)
    this.name = 'AuthError'
    this.status = status
    this.code = code
  }
}

const SESSION_KEY = 'openjm.session'
const PENDING_KEY = 'openjm.oidc.pending'
const CALLBACK_PATH = '/auth/callback'
/** Treat a session as gone slightly before the server would, to avoid a race. */
const EXPIRY_SKEW_MS = 15_000

// ---------------------------------------------------------------------------
// Storage
// ---------------------------------------------------------------------------

/**
 * Sessions live in sessionStorage: the backend issues a bearer token rather
 * than an httpOnly cookie, so the credential has to be readable by this origin.
 * sessionStorage over localStorage keeps it out of long-lived persistence and
 * scopes it to one tab. Cleared on logout and on any 401.
 */
function storage(): Storage | null {
  try {
    return window.sessionStorage
  } catch {
    return null
  }
}

export function readSession(): StoredSession | null {
  const store = storage()
  if (!store) return null
  const raw = store.getItem(SESSION_KEY)
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as StoredSession
    if (!parsed?.token) return null
    return parsed
  } catch {
    // A corrupt entry is not a session.
    store.removeItem(SESSION_KEY)
    return null
  }
}

/** A session is usable only if it exists and has not expired. */
export function hasUsableSession(now = Date.now()): boolean {
  const session = readSession()
  if (!session) return false
  return session.expiresAt - EXPIRY_SKEW_MS > now
}

export function clearSession(): void {
  const store = storage()
  store?.removeItem(SESSION_KEY)
  store?.removeItem(PENDING_KEY)
}

function writeSession(payload: SessionPayload): StoredSession {
  const session: StoredSession = {
    token: payload.token,
    expiresAt: Date.now() + Math.max(payload.expires_in, 1) * 1000,
    principalId: payload.principal_id,
    tenantId: payload.tenant_id,
    role: payload.role,
    permissions: payload.permissions ?? [],
  }
  storage()?.setItem(SESSION_KEY, JSON.stringify(session))
  return session
}

type SessionPayload = {
  token: string
  expires_in: number
  principal_id: string
  tenant_id: string
  role: string
  permissions: string[]
}

// ---------------------------------------------------------------------------
// Auth-lost notification
// ---------------------------------------------------------------------------

type Listener = () => void
const listeners = new Set<Listener>()

/** Subscribe to "the credential is gone" so the UI can show the login state. */
export function onAuthLost(listener: Listener): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

function announceAuthLost(): void {
  clearSession()
  for (const listener of listeners) {
    try {
      listener()
    } catch {
      // A failing listener must not break the request path.
    }
  }
}

// ---------------------------------------------------------------------------
// PKCE
// ---------------------------------------------------------------------------

function base64Url(bytes: Uint8Array): string {
  let binary = ''
  for (const byte of bytes) binary += String.fromCharCode(byte)
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

function randomUrlSafe(byteLength: number): string {
  const bytes = new Uint8Array(byteLength)
  crypto.getRandomValues(bytes)
  return base64Url(bytes)
}

async function s256Challenge(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier))
  return base64Url(new Uint8Array(digest))
}

type PendingLogin = { state: string; verifier: string; redirectUri: string }

function writePending(pending: PendingLogin): void {
  storage()?.setItem(PENDING_KEY, JSON.stringify(pending))
}

function readPending(): PendingLogin | null {
  const store = storage()
  const raw = store?.getItem(PENDING_KEY)
  if (!raw) return null
  try {
    return JSON.parse(raw) as PendingLogin
  } catch {
    return null
  }
}

// ---------------------------------------------------------------------------
// Low-level requests
// ---------------------------------------------------------------------------

async function readDetail(response: Response, fallback: string): Promise<string> {
  try {
    const body = await response.json()
    if (body && typeof body.detail === 'string') return body.detail
  } catch {
    // keep the fallback
  }
  return fallback
}

export function callbackUrl(): string {
  return `${window.location.origin}${CALLBACK_PATH}`
}

export async function fetchConfig(): Promise<AuthConfig> {
  const response = await fetch('/api/auth/config', { headers: { Accept: 'application/json' } })
  if (!response.ok) {
    throw new AuthError(
      await readDetail(response, `Auth configuration unavailable (${response.status})`),
      response.status,
      'config_unavailable',
    )
  }
  return (await response.json()) as AuthConfig
}

/**
 * Attach the OpenJM session credential to a protected request.
 *
 * On 401 the credential is dead (expired or revoked server side), so local
 * authentication state is cleared and listeners are told. The response is still
 * returned, leaving the caller to report the failure.
 *
 * A 403 is NOT an authentication outcome. The credential is still valid, the
 * principal simply may not perform the operation, so the session is retained
 * and no auth-lost event fires.
 */
export async function authorizedFetch(input: string, init: RequestInit = {}): Promise<Response> {
  const session = readSession()
  const headers = new Headers(init.headers)
  if (session && hasUsableSession()) {
    headers.set('Authorization', `Bearer ${session.token}`)
  } else if (session) {
    // Locally expired: never send a credential we already know is dead.
    announceAuthLost()
  }
  const response = await fetch(input, { ...init, headers })
  if (response.status === 401) {
    announceAuthLost()
  }
  return response
}

// ---------------------------------------------------------------------------
// Login, callback, logout
// ---------------------------------------------------------------------------

/**
 * Begin the authorization code + PKCE flow.
 *
 * Requires the deployment to be in OIDC mode and configured. If it is not, this
 * refuses rather than falling back to a development identity: an unconfigured
 * deployment must not be reachable as an authenticated user by accident.
 */
export async function beginLogin(): Promise<void> {
  const config = await fetchConfig()
  if (config.auth_mode !== 'oidc') {
    throw new AuthError(
      'This deployment does not use OIDC sign-in',
      400,
      'oidc_not_enabled',
    )
  }
  if (!config.oidc_configured || !config.authorization_endpoint || !config.client_id) {
    throw new AuthError(
      'OIDC sign-in is not fully configured on this deployment',
      503,
      'oidc_not_configured',
    )
  }

  const verifier = randomUrlSafe(32)
  const state = randomUrlSafe(16)
  const redirectUri = callbackUrl()
  writePending({ state, verifier, redirectUri })

  const url = new URL(config.authorization_endpoint)
  url.searchParams.set('client_id', config.client_id)
  url.searchParams.set('redirect_uri', redirectUri)
  url.searchParams.set('response_type', 'code')
  url.searchParams.set('scope', 'openid profile email')
  url.searchParams.set('state', state)
  url.searchParams.set('code_challenge', await s256Challenge(verifier))
  url.searchParams.set('code_challenge_method', 'S256')
  window.location.assign(url.toString())
}

/**
 * Complete the callback: validate the CSRF state, then exchange the code.
 *
 * Returns the established session. Throws without establishing anything when
 * the state does not match, which is the standard authorization-code guard
 * against a forged callback.
 */
export async function completeCallback(search: string): Promise<StoredSession> {
  const params = new URLSearchParams(search)
  const code = params.get('code')
  const state = params.get('state')
  const pending = readPending()

  if (!code) {
    throw new AuthError('OIDC callback carried no authorization code', 400, 'missing_code')
  }
  if (!pending || !state || state !== pending.state) {
    throw new AuthError('OIDC callback state did not match', 400, 'state_mismatch')
  }

  const response = await fetch('/api/auth/oidc/callback', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      code,
      code_verifier: pending.verifier,
      redirect_uri: pending.redirectUri,
    }),
  })
  if (!response.ok) {
    storage()?.removeItem(PENDING_KEY)
    throw new AuthError(
      await readDetail(response, `Sign-in failed (${response.status})`),
      response.status,
      'callback_failed',
    )
  }
  const payload = (await response.json()) as SessionPayload
  storage()?.removeItem(PENDING_KEY)
  return writeSession(payload)
}

/** Exchange a provider-issued token for an OpenJM session. */
export async function exchangeProviderToken(token: string): Promise<StoredSession> {
  const response = await fetch('/api/auth/token/exchange', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token }),
  })
  if (!response.ok) {
    throw new AuthError(
      await readDetail(response, `Token exchange failed (${response.status})`),
      response.status,
      'exchange_failed',
    )
  }
  return writeSession((await response.json()) as SessionPayload)
}

/**
 * Ask the server who the caller is. This is also how a revoked session is
 * detected on load: a token the server no longer honours answers 401.
 */
export async function fetchPrincipal(): Promise<Principal> {
  const response = await authorizedFetch('/api/auth/me')
  if (!response.ok) {
    throw new AuthError(
      await readDetail(response, `Not authenticated (${response.status})`),
      response.status,
      response.status === 401 ? 'unauthenticated' : 'principal_unavailable',
    )
  }
  return (await response.json()) as Principal
}

/** Revoke the session server side, then drop local state. */
export async function logout(): Promise<void> {
  const session = readSession()
  try {
    if (session) {
      await fetch('/api/auth/logout', {
        method: 'POST',
        headers: { Authorization: `Bearer ${session.token}` },
      })
    }
  } catch {
    // Revocation is best effort; local state is cleared regardless.
  } finally {
    clearSession()
  }
}

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------

export type AuthState =
  | { status: 'loading' }
  /** The server says this deployment authenticates with OIDC and we have a live session. */
  | { status: 'authenticated'; principal: Principal }
  /** The server says this deployment uses OIDC and we do not have a session. */
  | { status: 'unauthenticated'; config: AuthConfig }
  /** The server is in development mode. No credential is required. */
  | { status: 'dev'; config: AuthConfig }
  /** We could not establish the deployment's mode. Fail closed. */
  | { status: 'error'; message: string }

/**
 * Decide the application's authentication state.
 *
 * Fail closed: when the mode cannot be determined the result is `error`, never
 * `dev`. A development identity is only ever used when the *server* says the
 * deployment is in development mode.
 */
export async function bootstrapAuth(): Promise<AuthState> {
  let config: AuthConfig
  try {
    config = await fetchConfig()
  } catch (error) {
    return {
      status: 'error',
      message:
        error instanceof Error
          ? error.message
          : 'Could not determine how this deployment authenticates',
    }
  }

  if (config.auth_mode !== 'oidc') {
    return { status: 'dev', config }
  }

  if (config.oidc_configured && hasUsableSession()) {
    try {
      const principal = await fetchPrincipal()
      return { status: 'authenticated', principal }
    } catch (error) {
      if (error instanceof AuthError && error.status === 401) {
        // Expired or revoked: fall through to the login state.
        return { status: 'unauthenticated', config }
      }
      return {
        status: 'error',
        message: error instanceof Error ? error.message : 'Could not verify the session',
      }
    }
  }

  return { status: 'unauthenticated', config }
}
