import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import AdminPanel from './AdminPanel'
import PolicyEditor from './PolicyEditor'
import { api, type AdminMember } from './api'

const ada: AdminMember = {
  principal_id: 'ada',
  subject: 'oidc|ada',
  display_name: 'Ada Evans',
  role: 'viewer',
  status: 'active',
  department_ids: ['hr'],
  group_ids: ['team'],
}
const departments = [
  { id: 'hr', name: 'Human Resources', slug: 'hr', status: 'active' },
]
const groups = [
  {
    id: 'team',
    name: 'HR Team',
    slug: 'team',
    status: 'active',
    department_id: 'hr',
  },
]

beforeEach(() => {
  vi.spyOn(api.admin, 'members').mockResolvedValue([ada])
  vi.spyOn(api.admin, 'departments').mockResolvedValue(departments)
  vi.spyOn(api.admin, 'groups').mockResolvedValue(groups)
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

async function selectRole() {
  render(<AdminPanel actorRole="owner" />)
  const role = await screen.findByLabelText('Role for Ada Evans')
  fireEvent.change(role, { target: { value: 'admin' } })
  return role
}

test('role selection and cancellation send no mutation; save reviews the exact change', async () => {
  const change = vi
    .spyOn(api.admin, 'changeMemberRole')
    .mockResolvedValue({ ...ada, role: 'admin' })
  const role = await selectRole()
  expect(change).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Cancel role change' }))
  expect((role as HTMLSelectElement).value).toBe('viewer')
  expect(change).not.toHaveBeenCalled()
  fireEvent.change(role, { target: { value: 'admin' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save role' }))
  const dialog = screen.getByRole('dialog', { name: 'Change user role?' })
  expect(dialog.textContent).toContain('Ada Evans: Viewer → Administrator')
  expect(change).not.toHaveBeenCalled()
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
  expect(change).not.toHaveBeenCalled()
  expect((role as HTMLSelectElement).value).toBe('admin')
  fireEvent.click(screen.getByRole('button', { name: 'Save role' }))
  fireEvent.click(screen.getByRole('button', { name: 'Confirm role change' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  expect(change).toHaveBeenCalledExactlyOnceWith('ada', 'admin')
  expect(screen.queryByRole('button', { name: 'Save role' })).toBeNull()
  expect(screen.getByRole('status').textContent).toContain('completed')
})

test('a failed role save preserves the draft and exposes the server refusal', async () => {
  const change = vi
    .spyOn(api.admin, 'changeMemberRole')
    .mockRejectedValue(new Error('The last active owner cannot be demoted'))
  await selectRole()
  fireEvent.click(screen.getByRole('button', { name: 'Save role' }))
  fireEvent.click(screen.getByRole('button', { name: 'Confirm role change' }))
  expect(await screen.findByRole('alert')).toHaveProperty(
    'textContent',
    'The last active owner cannot be demoted',
  )
  expect(screen.getByRole('dialog')).toBeTruthy()
  expect(change).toHaveBeenCalledTimes(1)
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
  expect(
    (screen.getByLabelText('Role for Ada Evans') as HTMLSelectElement).value,
  ).toBe('admin')
  expect(screen.getByRole('button', { name: 'Save role' })).toBeTruthy()
})

test('pending confirmations reject duplicate submits and cannot be dismissed mid-request', async () => {
  let finish!: (value: AdminMember) => void
  const change = vi.spyOn(api.admin, 'changeMemberRole').mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve
      }),
  )
  await selectRole()
  fireEvent.click(screen.getByRole('button', { name: 'Save role' }))
  const confirm = screen.getByRole('button', { name: 'Confirm role change' })
  fireEvent.click(confirm)
  fireEvent.click(confirm)
  fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
  expect(change).toHaveBeenCalledTimes(1)
  expect(screen.getByRole('dialog')).toBeTruthy()
  expect(
    (screen.getByRole('button', { name: 'Cancel' }) as HTMLButtonElement)
      .disabled,
  ).toBe(true)
  await act(async () => finish({ ...ada, role: 'admin' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
})

test('confirmation starts on Cancel, traps keyboard focus and Escape cancels without a write', async () => {
  const revoke = vi.spyOn(api.admin, 'revokeMember')
  render(<AdminPanel />)
  await screen.findByText('Ada Evans')
  const trigger = screen.getByRole('button', { name: 'Revoke access' })
  trigger.focus()
  fireEvent.click(trigger)
  const cancel = screen.getByRole('button', { name: 'Cancel' })
  expect(document.activeElement).toBe(cancel)
  fireEvent.keyDown(cancel, { key: 'Tab', shiftKey: true })
  expect(document.activeElement).toBe(
    screen.getByRole('button', { name: 'Confirm revoke access' }),
  )
  fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
  expect(document.activeElement).toBe(trigger)
  expect(revoke).not.toHaveBeenCalled()
})

test.each([
  ['Revoke access', 'Confirm revoke access', 'revokeMember'],
  ['End sessions', 'Confirm end sessions', 'revokeMemberSessions'],
] as const)(
  '%s requires explicit confirmation',
  async (label, confirmation, method) => {
    const mutation = vi
      .spyOn(api.admin, method)
      .mockResolvedValue({ ...ada, revoked: 1 } as never)
    render(<AdminPanel />)
    await screen.findByText('Ada Evans')
    fireEvent.click(screen.getByRole('button', { name: label }))
    expect(mutation).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: confirmation }))
    await waitFor(() => expect(mutation).toHaveBeenCalledExactlyOnceWith('ada'))
  },
)

test('an administrator cannot select Owner and cannot edit their own role', async () => {
  render(<AdminPanel principalId="ada" actorRole="admin" />)
  const role = await screen.findByLabelText('Role for Ada Evans')
  expect((role as HTMLSelectElement).disabled).toBe(true)
  expect(
    (within(role).getByRole('option', { name: 'Owner' }) as HTMLOptionElement)
      .disabled,
  ).toBe(true)
})

test('policy editing preserves existing grants, writes only after confirmation, and can clear a department', async () => {
  const resource = {
    id: 'source-1',
    name: 'Payroll',
    classification: 'confidential',
    department_id: 'hr',
    tenant_visible: false,
    allowed_group_ids: ['team'],
  }
  vi.spyOn(api.admin, 'dataAccess').mockResolvedValue({
    sources: [resource],
    documents: [],
  })
  const update = vi.spyOn(api.admin, 'updatePolicy').mockResolvedValue({})
  render(<PolicyEditor departments={departments} groups={groups} />)
  fireEvent.click(
    await screen.findByRole('button', { name: 'Edit policy for Payroll' }),
  )
  expect((screen.getByLabelText('HR Team') as HTMLInputElement).checked).toBe(
    true,
  )
  fireEvent.change(screen.getByLabelText('Classification'), {
    target: { value: 'highly_restricted' },
  })
  fireEvent.change(
    screen.getByLabelText('Department', { selector: 'select' }),
    { target: { value: '' } },
  )
  expect(update).not.toHaveBeenCalled()
  fireEvent.click(
    screen.getByRole('button', { name: 'Review and save policy' }),
  )
  expect(update).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Confirm policy change' }))
  await waitFor(() =>
    expect(update).toHaveBeenCalledExactlyOnceWith('source', 'source-1', {
      classification: 'highly_restricted',
      department_id: null,
      clear_department: true,
      tenant_visible: false,
      allowed_group_ids: ['team'],
    }),
  )
  await screen.findByText('Access policy saved for Payroll.')
})

test('policy cancellation never alters the current grants', async () => {
  vi.spyOn(api.admin, 'dataAccess').mockResolvedValue({
    sources: [],
    documents: [
      {
        id: 'doc',
        title: 'SOP',
        classification: 'internal',
        tenant_visible: true,
        allowed_group_ids: ['team'],
      },
    ],
  })
  const update = vi.spyOn(api.admin, 'updatePolicy')
  render(<PolicyEditor departments={departments} groups={groups} />)
  fireEvent.click(
    await screen.findByRole('button', { name: 'Edit policy for SOP' }),
  )
  fireEvent.click(screen.getByLabelText('HR Team'))
  fireEvent.click(screen.getByRole('button', { name: 'Cancel policy changes' }))
  expect(update).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Edit policy for SOP' }))
  expect((screen.getByLabelText('HR Team') as HTMLInputElement).checked).toBe(
    true,
  )
})

test('configured plan and allowance are shown instead of an obsolete placeholder', async () => {
  vi.spyOn(api.admin, 'usage').mockResolvedValue({
    tenant_id: 'acme',
    usage: { total_tokens: 2050 },
    plan: { plan_name: 'Enterprise' },
    entitlements: {
      subscription_status: 'active',
      allowance: { available_units: 275, consumed_units: 25, held_units: 0 },
    },
    note: 'Usage aggregation is M2; plan and entitlement values are M3.',
  })
  render(<AdminPanel />)
  fireEvent.click(screen.getByRole('button', { name: 'Usage / Plan' }))
  await screen.findByText('Enterprise')
  expect(screen.getByText('275')).toBeTruthy()
  expect(screen.queryByText(/M2/)).toBeNull()
})
