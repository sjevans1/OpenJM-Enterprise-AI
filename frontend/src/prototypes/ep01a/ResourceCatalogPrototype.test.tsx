/**
 * EP-01A P2 — resource catalog behaviour (jsdom).
 *
 * Proves truthful totals, filter/search correctness (compared against the
 * mock's own responses), pagination identity, and the loading/empty/error/
 * denied/stale/recovery contract. Browser-level keyboard, viewport and axe
 * evidence lives in the Playwright spec.
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, test } from 'vitest'
import ResourceCatalogPrototype from './ResourceCatalogPrototype'
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

async function renderLoaded() {
  render(<ResourceCatalogPrototype />)
  await screen.findByText(/Showing 1–25 of 168 resources/)
}

test('catalog loads with truthful totals from the server response', async () => {
  await renderLoaded()
  const response = await fetch('/api/ep01a/catalog?page=1&page_size=25&sort=updated')
  const body = await response.json()
  expect(body.total).toBe(168)
  expect(screen.getByText(new RegExp(`Showing 1–25 of ${body.total} resources`))).toBeTruthy()
})

test('search narrows results with counts that match the dataset', async () => {
  await renderLoaded()
  const expected = await fetch(
    '/api/ep01a/catalog?page=1&page_size=25&sort=updated&query=payroll',
  ).then((response) => response.json())
  fireEvent.change(screen.getByLabelText('Search'), {
    target: { value: 'payroll' },
  })
  await waitFor(() =>
    expect(
      screen.getByText(
        new RegExp(`Showing 1–${Math.min(25, expected.total)} of ${expected.total} resources`),
      ),
    ).toBeTruthy(),
  )
})

test('an unmatched search shows the empty state and clears cleanly', async () => {
  await renderLoaded()
  fireEvent.change(screen.getByLabelText('Search'), {
    target: { value: 'zzz-nothing-matches' },
  })
  await screen.findByText('No resources match')
  fireEvent.click(screen.getByRole('button', { name: 'Clear search and filters' }))
  await screen.findByText(/Showing 1–25 of 168 resources/)
})

test('pagination moves through stable pages and reports ranges', async () => {
  await renderLoaded()
  const firstRowName = screen
    .getAllByRole('button', { name: /^View details for / })[0]
    .getAttribute('aria-label')!
  fireEvent.click(screen.getByRole('button', { name: 'Next page' }))
  await screen.findByText(/Showing 26–50 of 168 resources/)
  const secondPageFirst = screen
    .getAllByRole('button', { name: /^View details for / })[0]
    .getAttribute('aria-label')!
  expect(secondPageFirst).not.toBe(firstRowName)
  fireEvent.click(screen.getByRole('button', { name: 'Previous page' }))
  await screen.findByText(/Showing 1–25 of 168 resources/)
  expect(
    screen.getAllByRole('button', { name: /^View details for / })[0].getAttribute('aria-label'),
  ).toBe(firstRowName)
})

test('a section filter keeps totals truthful', async () => {
  await renderLoaded()
  const expected = await fetch(
    '/api/ep01a/catalog?page=1&page_size=25&sort=updated&kind=document',
  ).then((response) => response.json())
  fireEvent.change(screen.getByLabelText('Type'), {
    target: { value: 'document' },
  })
  await waitFor(() =>
    expect(
      screen.getByText(
        new RegExp(`of ${expected.total} resources`),
      ),
    ).toBeTruthy(),
  )
})

test('stale data is flagged and refresh reconciles it', async () => {
  await renderLoaded()
  handle.setBehavior('stale')
  fireEvent.change(screen.getByLabelText('Type'), { target: { value: 'database' } })
  await screen.findByText(/An upstream source has refreshed since/)
  fireEvent.click(screen.getByRole('button', { name: 'Refresh now' }))
  await waitFor(() =>
    expect(screen.queryByText(/An upstream source has refreshed since/)).toBeNull(),
  )
  expect(handle.getBehavior()).toBe('ok')
})

test('a failed load offers retry and recovers', async () => {
  await renderLoaded()
  handle.setBehavior('fail_load')
  fireEvent.click(screen.getByRole('button', { name: 'Refresh catalog' }))
  await screen.findByText('Catalog could not be loaded')
  handle.setBehavior('ok')
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
  await screen.findByText(/Showing 1–25 of 168 resources/)
})

test('denied access shows the permission state with no rows', async () => {
  await renderLoaded()
  handle.setBehavior('denied_load')
  fireEvent.click(screen.getByRole('button', { name: 'Refresh catalog' }))
  await screen.findByText('Access denied')
  expect(screen.queryByRole('table')).toBeNull()
})

test('detail view lists metadata, closes on Escape and restores focus', async () => {
  await renderLoaded()
  const trigger = screen.getAllByRole('button', { name: /^View details for / })[0]
  trigger.focus()
  fireEvent.click(trigger)
  const dialog = await screen.findByRole('dialog')
  expect(within(dialog).getByText(/Metadata \(\d+ entries\)/)).toBeTruthy()
  fireEvent.keyDown(dialog, { key: 'Escape' })
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  expect(document.activeElement).toBe(trigger)
})
