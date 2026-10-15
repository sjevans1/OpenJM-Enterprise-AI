/**
 * EP-01A — mock API and synthetic-data invariants.
 *
 * These tests prove the fixture layer itself: deterministic generation,
 * truthful totals, stable ordering, optimistic concurrency, the last-owner
 * guard, error behaviours and the request log the browser tests assert on.
 */
import { afterEach, beforeEach, expect, test } from 'vitest'
import { generateResources, catalogMetadataTotal } from './data'
import {
  installMockServer,
  __resetMockServerForTests,
  type Ep01aMockHandle,
} from './mockServer'

let handle: Ep01aMockHandle

beforeEach(() => {
  handle = installMockServer()
  __resetMockServerForTests()
})

afterEach(() => {
  handle.clear()
})

async function getJson(path: string): Promise<{ status: number; body: any }> {
  const response = await fetch(path)
  return { status: response.status, body: await response.json() }
}

test('the generated catalog is deterministic and complete', () => {
  const first = generateResources()
  const second = generateResources()
  expect(first.length).toBe(168)
  expect(JSON.stringify(first)).toBe(JSON.stringify(second))
  const ids = new Set(first.map((row) => row.id))
  expect(ids.size).toBe(first.length)
  const metadataTotal = catalogMetadataTotal(first)
  expect(metadataTotal).toBeGreaterThanOrEqual(1000)
  expect(metadataTotal).toBe(handle.catalogSummary().metadata)
})

test('member directory has one owner and 26 stable identities', async () => {
  const { status, body } = await getJson('/api/ep01a/admin/members')
  expect(status).toBe(200)
  expect(body.members).toHaveLength(26)
  expect(body.members.filter((m: any) => m.role === 'owner')).toHaveLength(1)
})

test('catalog pagination reports truthful totals and stable row identity', async () => {
  const page1 = await getJson('/api/ep01a/catalog?page=1&page_size=25')
  expect(page1.body.total).toBe(168)
  expect(page1.body.items).toHaveLength(25)
  const page2 = await getJson('/api/ep01a/catalog?page=2&page_size=25')
  expect(page2.body.items).toHaveLength(25)
  expect(page2.body.items[0].id).not.toBe(page1.body.items[0].id)
  const page7 = await getJson('/api/ep01a/catalog?page=7&page_size=25')
  expect(page7.body.items).toHaveLength(18)
  const page1Again = await getJson('/api/ep01a/catalog?page=1&page_size=25')
  expect(page1Again.body.items.map((row: any) => row.id)).toEqual(
    page1.body.items.map((row: any) => row.id),
  )
})

test('filters and search stay consistent with the dataset', async () => {
  const all = generateResources()
  const expectedConfidential = all.filter(
    (row) => row.classification === 'confidential',
  )
  const confidential = await getJson(
    '/api/ep01a/catalog?classification=confidential&page=1&page_size=100',
  )
  expect(confidential.body.total).toBe(expectedConfidential.length)
  expect(
    confidential.body.items.every(
      (row: any) => row.classification === 'confidential',
    ),
  ).toBe(true)

  const payroll = await getJson(
    '/api/ep01a/catalog?query=payroll&page=1&page_size=100',
  )
  expect(payroll.body.total).toBeGreaterThan(0)
  expect(
    payroll.body.items.every((row: any) =>
      `${row.name} ${row.owner} ${row.source} ${row.tags.join(' ')}`
        .toLowerCase()
        .includes('payroll'),
    ),
  ).toBe(true)
})

test('role writes use optimistic concurrency and update the directory', async () => {
  const stale = await fetch('/api/ep01a/admin/members/m-002/role', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ role: 'admin', expected_revision: 999 }),
  })
  expect(stale.status).toBe(409)
  const staleBody = await stale.json()
  expect(staleBody.current.revision).toBe(2)

  const ok = await fetch('/api/ep01a/admin/members/m-002/role', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ role: 'steward', status: 'active', expected_revision: 2 }),
  })
  expect(ok.status).toBe(200)
  const okBody = await ok.json()
  expect(okBody.member.role).toBe('steward')
  expect(okBody.member.revision).toBe(3)

  const after = await getJson('/api/ep01a/admin/members')
  const ada = after.body.members.find((m: any) => m.id === 'm-002')
  expect(ada.role).toBe('steward')
})

test('the last owner cannot lose the owner role', async () => {
  const response = await fetch('/api/ep01a/admin/members/m-001/role', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ role: 'viewer', expected_revision: 4 }),
  })
  expect(response.status).toBe(409)
  const body = await response.json()
  expect(body.detail).toContain('last owner')
})

test('failure behaviours are injectable and observable', async () => {
  handle.setBehavior('denied')
  const denied = await fetch('/api/ep01a/admin/members/m-002/role', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ role: 'admin', expected_revision: 2 }),
  })
  expect(denied.status).toBe(403)

  handle.setBehavior('error')
  const failed = await fetch('/api/ep01a/admin/members/m-002/role', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ role: 'admin', expected_revision: 2 }),
  })
  expect(failed.status).toBe(500)

  handle.setBehavior('fail_load')
  const brokenLoad = await fetch('/api/ep01a/admin/members')
  expect(brokenLoad.status).toBe(500)

  handle.setBehavior('denied_load')
  const deniedLoad = await fetch('/api/ep01a/catalog?page=1&page_size=10')
  expect(deniedLoad.status).toBe(403)
})

test('conflict simulates a concurrent administrator change', async () => {
  handle.setBehavior('conflict')
  const response = await fetch('/api/ep01a/admin/members/m-002/role', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ role: 'admin', expected_revision: 2 }),
  })
  expect(response.status).toBe(409)
  const body = await response.json()
  expect(body.detail).toContain('another administrator')
  expect(body.current.id).toBe('m-002')
  expect(body.current.role).toBe('steward')
  expect(body.current.revision).toBe(3)
})

test('stale catalog state reconciles through an explicit refresh', async () => {
  handle.setBehavior('stale')
  const stale = await getJson('/api/ep01a/catalog?page=1&page_size=10')
  expect(stale.body.stale).toBe(true)
  expect(new Date(stale.body.last_refreshed).getTime()).toBeLessThan(
    new Date('2026-10-15T09:00:00Z').getTime(),
  )

  const refreshed = await getJson('/api/ep01a/catalog?page=1&page_size=10&refresh=1')
  expect(refreshed.body.stale).toBe(false)
  expect(handle.getBehavior()).toBe('ok')
})

test('the request log records every attempt with method, path and body', async () => {
  handle.clear()
  await fetch('/api/ep01a/admin/members')
  await fetch('/api/ep01a/admin/members/m-002/role', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ role: 'admin', expected_revision: 2 }),
  })
  expect(handle.requests).toHaveLength(2)
  expect(handle.requests[0].method).toBe('GET')
  expect(handle.requests[0].path).toBe('/api/ep01a/admin/members')
  expect(handle.requests[1].method).toBe('POST')
  expect(handle.requests[1].body).toEqual({ role: 'admin', expected_revision: 2 })
  expect(handle.requests[1].status).toBe(200)
  handle.clear()
  expect(handle.requests).toHaveLength(0)
})
