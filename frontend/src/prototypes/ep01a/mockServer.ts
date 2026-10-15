/**
 * EP-01A prototype — in-page mock API for `/api/ep01a/**`.
 *
 * The prototypes must never touch the real OpenJM backend (no authorization
 * changes for visual experimentation). This module installs a fetch shim that
 * serves deterministic fixture responses, records every attempt in a request
 * log, and supports injectable failure behaviours (denied, conflict, stale,
 * failed load) so the UI's error/recovery states are provable.
 *
 * The full request log is exposed on `window.__ep01a` for Playwright
 * assertions: "changing a dropdown issues zero mutation requests" is checked
 * against this log, not against UI state.
 */

import {
  CATALOG_ANCHOR,
  MEMBERS,
  generateResources,
  metadataFor,
  type Member,
  type MemberStatus,
  type Resource,
  type RoleId,
} from './data'

export type Behavior =
  | 'ok'
  | 'fail_load'
  | 'denied_load'
  | 'denied'
  | 'error'
  | 'conflict'
  | 'stale'

export type RequestRecord = {
  n: number
  at: string
  method: string
  path: string
  query: string
  body: unknown
  behavior: Behavior
  status: number
  ms: number
}

export type Ep01aMockHandle = {
  requests: RequestRecord[]
  clear: () => void
  setBehavior: (behavior: Behavior) => void
  getBehavior: () => Behavior
  setLatency: (ms: number) => void
  catalogSummary: () => { resources: number; metadata: number }
  members: () => Member[]
}

declare global {
  interface Window {
    __ep01a?: Ep01aMockHandle
  }
}

const ROLE_IDS: RoleId[] = ['owner', 'admin', 'steward', 'viewer']
const STATUSES: MemberStatus[] = ['active', 'suspended', 'invited']

type MockState = {
  handle: Ep01aMockHandle
  members: Member[]
  resources: Resource[]
  membersRevision: number
  catalogRevision: number
  behavior: Behavior
  latencyMs: number
}

let state: MockState | null = null

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function delay(ms: number): Promise<void> {
  if (ms <= 0) return Promise.resolve()
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function readBody(init: RequestInit | undefined): unknown {
  const raw = init?.body
  if (typeof raw === 'string' && raw) {
    try {
      return JSON.parse(raw)
    } catch {
      return null
    }
  }
  return null
}

/* ------------------------------------------------------------------ */
/* handlers                                                           */
/* ------------------------------------------------------------------ */

function listMembers(s: MockState): Response {
  if (s.behavior === 'fail_load') {
    return json({ detail: 'The member directory could not be loaded.' }, 500)
  }
  if (s.behavior === 'denied_load') {
    return json(
      { detail: 'You do not have permission to view administration data.' },
      403,
    )
  }
  return json({ members: s.members, revision: s.membersRevision })
}

function updateMemberRole(
  s: MockState,
  id: string,
  body: unknown,
): Response {
  const member = s.members.find((row) => row.id === id)
  if (!member) return json({ detail: 'That member no longer exists.' }, 404)
  const payload = (body || {}) as {
    role?: RoleId
    status?: MemberStatus
    expected_revision?: number
  }
  if (!payload.role || !ROLE_IDS.includes(payload.role)) {
    return json({ detail: 'Choose a valid role.' }, 400)
  }
  if (payload.status && !STATUSES.includes(payload.status)) {
    return json({ detail: 'Choose a valid account status.' }, 400)
  }

  if (s.behavior === 'denied') {
    return json(
      { detail: 'You do not have permission to change roles in this workspace.' },
      403,
    )
  }
  if (s.behavior === 'error') {
    return json(
      { detail: 'The change could not be saved. No changes were applied.' },
      500,
    )
  }
  if (s.behavior === 'conflict') {
    // Another administrator changed the same member between load and save.
    member.role = member.role === 'steward' ? 'viewer' : 'steward'
    member.revision += 1
    s.membersRevision += 1
    return json(
      {
        detail:
          'This member was changed by another administrator while you were editing.',
        current: member,
        revision: s.membersRevision,
      },
      409,
    )
  }

  const ownerCount = s.members.filter((row) => row.role === 'owner').length
  if (member.role === 'owner' && payload.role !== 'owner' && ownerCount <= 1) {
    return json(
      {
        detail: `${member.name} is the workspace's last owner. Assign another owner before changing this role.`,
        current: member,
        revision: s.membersRevision,
      },
      409,
    )
  }
  if (
    typeof payload.expected_revision === 'number' &&
    payload.expected_revision !== member.revision
  ) {
    return json(
      {
        detail: 'This member changed since you loaded the page.',
        current: member,
        revision: s.membersRevision,
      },
      409,
    )
  }

  member.role = payload.role
  if (payload.status) member.status = payload.status
  member.revision += 1
  s.membersRevision += 1
  return json({ member, revision: s.membersRevision })
}

function listCatalog(s: MockState, params: URLSearchParams): Response {
  if (s.behavior === 'fail_load') {
    return json({ detail: 'The resource catalog could not be loaded.' }, 500)
  }
  if (s.behavior === 'denied_load') {
    return json(
      { detail: 'You do not have permission to view these sources.' },
      403,
    )
  }
  const refresh = params.get('refresh') === '1'
  const stale = s.behavior === 'stale' && !refresh
  if (refresh && s.behavior === 'stale') s.behavior = 'ok'

  const query = (params.get('query') || '').trim().toLowerCase()
  const kind = params.get('kind') || ''
  const classification = params.get('classification') || ''
  const status = params.get('status') || ''
  const sort = params.get('sort') || 'updated'
  const page = Math.max(1, Number(params.get('page') || 1))
  const pageSize = Math.min(100, Math.max(5, Number(params.get('page_size') || 25)))

  let rows = s.resources.slice()
  if (query) {
    rows = rows.filter((row) =>
      `${row.name} ${row.owner} ${row.source} ${row.tags.join(' ')}`
        .toLowerCase()
        .includes(query),
    )
  }
  if (kind) rows = rows.filter((row) => row.kind === kind)
  if (classification) rows = rows.filter((row) => row.classification === classification)
  if (status) rows = rows.filter((row) => row.status === status)
  rows.sort((a, b) => {
    if (sort === 'name') {
      const byName = a.name.localeCompare(b.name)
      return byName !== 0 ? byName : a.id.localeCompare(b.id)
    }
    if (sort === 'records') {
      const byRecords = b.records - a.records
      return byRecords !== 0 ? byRecords : a.id.localeCompare(b.id)
    }
    const byUpdated = b.updatedAt.localeCompare(a.updatedAt)
    return byUpdated !== 0 ? byUpdated : a.id.localeCompare(b.id)
  })

  const total = rows.length
  const start = (page - 1) * pageSize
  const items = rows.slice(start, start + pageSize)
  return json({
    items,
    total,
    page,
    page_size: pageSize,
    revision: s.catalogRevision,
    stale,
    last_refreshed: stale
      ? new Date(new Date(CATALOG_ANCHOR).getTime() - 41 * 60_000).toISOString()
      : refresh
        ? new Date().toISOString()
        : CATALOG_ANCHOR,
  })
}

function catalogDetail(s: MockState, id: string): Response {
  const resource = s.resources.find((row) => row.id === id)
  if (!resource) return json({ detail: 'That resource is no longer available.' }, 404)
  return json({
    resource,
    metadata: metadataFor(resource),
    revision: s.catalogRevision,
  })
}

/* ------------------------------------------------------------------ */
/* installation                                                       */
/* ------------------------------------------------------------------ */

export function installMockServer(): Ep01aMockHandle {
  if (state) return state.handle

  const s: MockState = {
    members: MEMBERS.map((member) => ({ ...member })),
    resources: generateResources(),
    membersRevision: 11,
    catalogRevision: 4,
    behavior: 'ok',
    latencyMs: 140,
    handle: undefined as unknown as Ep01aMockHandle,
  }

  const handle: Ep01aMockHandle = {
    requests: [],
    clear: () => {
      s.handle.requests.length = 0
    },
    setBehavior: (behavior: Behavior) => {
      s.behavior = behavior
    },
    getBehavior: () => s.behavior,
    setLatency: (ms: number) => {
      s.latencyMs = ms
    },
    catalogSummary: () => ({
      resources: s.resources.length,
      metadata: s.resources.reduce((sum, row) => sum + metadataFor(row).length, 0),
    }),
    members: () => s.members.map((member) => ({ ...member })),
  }
  s.handle = handle
  state = s

  const target = globalThis as unknown as { fetch: typeof fetch }
  const originalFetch = (target.fetch as typeof fetch).bind(globalThis)

  const shim = async (
    input: RequestInfo | URL,
    init?: RequestInit,
  ): Promise<Response> => {
    let rawUrl: string
    let inheritedMethod = 'GET'
    if (input instanceof Request) {
      rawUrl = input.url
      inheritedMethod = input.method || 'GET'
    } else if (typeof input === 'string') {
      rawUrl = input
    } else {
      rawUrl = input.toString()
    }
    const url = new URL(rawUrl, window.location.origin)
    if (!url.pathname.startsWith('/api/ep01a/')) {
      return originalFetch(input as RequestInfo, init)
    }

    const method = (init?.method || inheritedMethod || 'GET').toUpperCase()
    const record: RequestRecord = {
      n: handle.requests.length + 1,
      at: new Date().toISOString(),
      method,
      path: url.pathname,
      query: url.search,
      body: method === 'GET' ? null : readBody(init),
      behavior: s.behavior,
      status: 0,
      ms: 0,
    }
    handle.requests.push(record)

    const started = Date.now()
    await delay(s.latencyMs)

    let response: Response
    const path = url.pathname
    const params = url.searchParams
    if (method === 'GET' && path === '/api/ep01a/admin/members') {
      response = listMembers(s)
    } else if (method === 'POST' && /^\/api\/ep01a\/admin\/members\/[^/]+\/role$/.test(path)) {
      const id = path.split('/')[5]
      response = updateMemberRole(s, id, record.body)
    } else if (method === 'GET' && path === '/api/ep01a/catalog') {
      response = listCatalog(s, params)
    } else if (method === 'GET' && /^\/api\/ep01a\/catalog\/[^/]+$/.test(path)) {
      response = catalogDetail(s, path.split('/')[4])
    } else {
      response = json({ detail: `No mock route for ${method} ${path}` }, 404)
    }

    record.status = response.status
    record.ms = Date.now() - started
    return response
  }

  target.fetch = shim as typeof fetch
  if (typeof window !== 'undefined') {
    try {
      ;(window as unknown as { fetch: typeof fetch }).fetch = shim as typeof fetch
    } catch {
      // jsdom window may be read-only in some setups; globalThis is patched regardless.
    }
  }

  window.__ep01a = handle
  return handle
}

/** For unit tests: reset all fixture state in place so each test starts fresh. */
export function __resetMockServerForTests(): void {
  if (!state) return
  state.members = MEMBERS.map((member) => ({ ...member }))
  state.resources = generateResources()
  state.membersRevision = 11
  state.catalogRevision = 4
  state.behavior = 'ok'
  state.latencyMs = 0
  state.handle.requests.length = 0
}
