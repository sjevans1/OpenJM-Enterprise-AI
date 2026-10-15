/**
 * EP-01A P1 — governed role administration behaviour (jsdom).
 *
 * The heavy interaction and browser coverage lives in the Playwright spec
 * (frontend/e2e/ep01a-prototypes.spec.ts); these tests give the CI test job a
 * fast, deterministic contract: draft changes never write, the review dialog
 * names the target and the old/new values, refusals keep the draft, and a
 * conflict offers reconciliation.
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, test } from 'vitest'
import AdminRolesPrototype from './AdminRolesPrototype'
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
  cleanup()
})

function mutations() {
  return handle.requests.filter((record) => record.method !== 'GET')
}

async function openAdaEditor() {
  render(<AdminRolesPrototype dialogImpl="existing" />)
  await screen.findByText('Ada Evans')
  fireEvent.click(screen.getByRole('button', { name: 'Manage Ada Evans' }))
  await screen.findByRole('heading', { name: 'Manage Ada Evans' })
}

function roleSelect(): HTMLSelectElement {
  return screen.getByLabelText('Role') as HTMLSelectElement
}

test('directory loads with truthful totals and draft changes issue no requests', async () => {
  await openAdaEditor()
  expect(screen.getByText('26 of 26 members')).toBeTruthy()
  handle.clear()
  fireEvent.change(roleSelect(), { target: { value: 'admin' } })
  expect(roleSelect().value).toBe('admin')
  expect(screen.getByText(/Pending change: Viewer → Administrator/)).toBeTruthy()
  expect(handle.requests).toHaveLength(0)
})

test('review dialog shows the named target and old/new values; cancel writes nothing', async () => {
  await openAdaEditor()
  fireEvent.change(roleSelect(), { target: { value: 'admin' } })
  handle.clear()
  fireEvent.click(screen.getByRole('button', { name: 'Review and save role' }))
  const dialog = await screen.findByRole('dialog')
  expect(within(dialog).getByText('Change user role?')).toBeTruthy()
  expect(within(dialog).getByText('Ada Evans · ada.evans@northwind.example')).toBeTruthy()
  expect(within(dialog).getByText('Viewer → Administrator')).toBeTruthy()
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  expect(handle.requests).toHaveLength(0)
})

test('Escape closes the review without writes and keeps the draft', async () => {
  await openAdaEditor()
  fireEvent.change(roleSelect(), { target: { value: 'admin' } })
  handle.clear()
  fireEvent.click(screen.getByRole('button', { name: 'Review and save role' }))
  const dialog = await screen.findByRole('dialog')
  fireEvent.keyDown(dialog, { key: 'Escape' })
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  expect(handle.requests).toHaveLength(0)
  expect(roleSelect().value).toBe('admin')
})

test('confirm issues exactly one write with the expected revision and reconciles', async () => {
  await openAdaEditor()
  fireEvent.change(roleSelect(), { target: { value: 'admin' } })
  handle.clear()
  fireEvent.click(screen.getByRole('button', { name: 'Review and save role' }))
  fireEvent.click(
    within(await screen.findByRole('dialog')).getByRole('button', {
      name: 'Confirm role change',
    }),
  )
  await screen.findByText("Ada Evans's role was changed to Administrator.")
  const writes = mutations()
  expect(writes).toHaveLength(1)
  expect(writes[0].path).toBe('/api/ep01a/admin/members/m-002/role')
  expect(writes[0].body).toEqual({
    role: 'admin',
    status: 'active',
    expected_revision: 2,
  })
  expect(handle.requests[handle.requests.length - 1].method).toBe('GET')
})

test('a denied write shows the permission message and keeps the draft', async () => {
  await openAdaEditor()
  fireEvent.change(roleSelect(), { target: { value: 'admin' } })
  fireEvent.click(screen.getByRole('button', { name: 'Review and save role' }))
  const dialog = await screen.findByRole('dialog')
  handle.setBehavior('denied')
  handle.clear()
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Confirm role change' }),
  )
  await within(dialog).findByRole('alert')
  expect(within(dialog).getByRole('alert').textContent).toContain(
    'do not have permission',
  )
  expect(screen.queryByText(/role was changed/)).toBeNull()
  expect(roleSelect().value).toBe('admin')
  expect(mutations()).toHaveLength(1)
})

test('a conflict shows the server value and reload reconciles without overwriting', async () => {
  await openAdaEditor()
  fireEvent.change(roleSelect(), { target: { value: 'admin' } })
  fireEvent.click(screen.getByRole('button', { name: 'Review and save role' }))
  const dialog = await screen.findByRole('dialog')
  handle.setBehavior('conflict')
  handle.clear()
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Confirm role change' }),
  )
  await within(dialog).findByText(/Current on the server:/)
  expect(within(dialog).getByText(/revision 3/)).toBeTruthy()
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Reload the latest version' }),
  )
  await screen.findByText(/Loaded the latest version of Ada Evans/)
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  expect(roleSelect().value).toBe('steward')
  expect(mutations()).toHaveLength(1)
})

test('a failed load offers retry and recovers', async () => {
  handle.setBehavior('fail_load')
  render(<AdminRolesPrototype dialogImpl="existing" />)
  await screen.findByText('Members could not be loaded')
  handle.setBehavior('ok')
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
  await screen.findByText('Ada Evans')
})

test('a permission-denied load explains the state without a directory', async () => {
  handle.setBehavior('denied_load')
  render(<AdminRolesPrototype dialogImpl="existing" />)
  await screen.findByText('Access denied')
  expect(
    screen.getByText(/do not have permission to view administration data/i),
  ).toBeTruthy()
  expect(screen.queryByText('Ada Evans')).toBeNull()
})
