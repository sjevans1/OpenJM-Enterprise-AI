import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import App from './App'
import { api, type DataSourceRecord } from './api'

const source: DataSourceRecord = {
  id: 'source-one',
  name: 'Finance warehouse',
  engine: 'postgresql',
  status: 'untested',
  enabled: false,
  tables: [],
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
}
beforeEach(() => {
  localStorage.clear()
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL) => {
      if (String(input) === '/api/auth/config')
        return Promise.resolve(
          new Response(
            JSON.stringify({ auth_mode: 'dev', oidc_configured: false }),
          ),
        )
      throw new Error(`Unexpected request: ${input}`)
    }),
  )
  vi.spyOn(api, 'conversations').mockResolvedValue([])
  vi.spyOn(api, 'documents').mockResolvedValue([])
  vi.spyOn(api, 'reports').mockResolvedValue([])
  vi.spyOn(api, 'dataSources').mockResolvedValue([])
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

async function setupForm() {
  render(<App />)
  fireEvent.click(await screen.findByRole('button', { name: /^Data/ }))
  fireEvent.change(screen.getByLabelText('Display name'), {
    target: { value: source.name },
  })
  fireEvent.change(screen.getByLabelText('Connection URI'), {
    target: { value: 'postgresql://reader:secret@host/finance' },
  })
}

test('a source is created disabled and failed verification does not invite duplicate creation', async () => {
  const create = vi.spyOn(api, 'createDataSource').mockResolvedValue(source)
  vi.spyOn(api, 'testDataSource').mockRejectedValue(
    new Error('Connection failed'),
  )
  await setupForm()
  vi.mocked(api.dataSources).mockResolvedValue([source])
  fireEvent.click(screen.getByRole('button', { name: 'Connect source' }))
  await screen.findByRole('alert')
  expect(create).toHaveBeenCalledExactlyOnceWith({
    name: source.name,
    engine: 'postgresql',
    connection_uri: 'postgresql://reader:secret@host/finance',
    enabled: false,
  })
  expect(
    (screen.getByLabelText('Connection URI') as HTMLInputElement).value,
  ).toBe('')
  expect(
    (screen.getByLabelText('Display name') as HTMLInputElement).value,
  ).toBe('')
  expect(screen.getByRole('alert').textContent).toContain('was saved')
  expect(screen.getByRole('button', { name: 'Test connection' })).toBeTruthy()
  expect(screen.getByText('Disabled')).toBeTruthy()
})

test('connection setup locks the form until verification completes', async () => {
  let finish!: (value: DataSourceRecord) => void
  const create = vi.spyOn(api, 'createDataSource').mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve
      }),
  )
  vi.spyOn(api, 'testDataSource').mockResolvedValue({
    source_id: source.id,
    status: 'error',
    ok: false,
    detail: 'Unreachable',
  })
  await setupForm()
  const button = screen.getByRole('button', { name: 'Connect source' })
  fireEvent.click(button)
  fireEvent.click(button)
  expect(
    (
      screen
        .getByLabelText('Connection URI')
        .closest('fieldset') as HTMLFieldSetElement
    ).disabled,
  ).toBe(true)
  expect(create).toHaveBeenCalledTimes(1)
  await act(async () => finish(source))
  await waitFor(() =>
    expect((button as HTMLButtonElement).disabled).toBe(false),
  )
})

test('enabling and deleting a source require named confirmation', async () => {
  vi.mocked(api.dataSources).mockResolvedValue([source])
  const enable = vi
    .spyOn(api, 'setDataSourceEnabled')
    .mockResolvedValue({ ...source, enabled: true })
  const remove = vi
    .spyOn(api, 'deleteDataSource')
    .mockResolvedValue({ deleted: true, source_id: source.id })
  render(<App />)
  fireEvent.click(await screen.findByRole('button', { name: /^Data/ }))
  fireEvent.click(await screen.findByRole('button', { name: 'Enable' }))
  expect(screen.getByRole('dialog').textContent).toContain(source.name)
  expect(enable).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
  fireEvent.click(screen.getByRole('button', { name: 'Delete' }))
  expect(remove).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Confirm remove source' }))
  await waitFor(() => expect(remove).toHaveBeenCalledExactlyOnceWith(source.id))
})
