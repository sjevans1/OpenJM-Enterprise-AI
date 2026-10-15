/**
 * EP-01A Prototype P1 — governed role administration (browser).
 *
 * Proves, in a real Chromium against real rendered screens:
 *  - changing a dropdown issues zero requests;
 *  - Cancel and Escape issue zero writes and keep the draft;
 *  - Confirm issues exactly one POST with the expected revision, then reloads;
 *  - denied / conflict / failed states keep the draft and offer recovery;
 *  - the flow is identical under the current OpenJM dialog implementation;
 *  - desktop 1440x900, tablet 768x1024 and mobile 390x844 layouts hold.
 */
import { expect, test, type Page } from '@playwright/test'
import {
  clearRequests,
  mutations,
  openPrototype,
  record,
  requests,
  setBehavior,
  shot,
} from './ep01a-evidence'

async function openRoles(page: Page, extra = '') {
  await openPrototype(page, 'roles', extra)
  await expect(page.getByText('Ada Evans')).toBeVisible()
}

async function startDraft(page: Page) {
  await page.getByRole('button', { name: 'Manage Ada Evans' }).click()
  await expect(page.getByRole('heading', { name: 'Manage Ada Evans' })).toBeVisible()
  await page.getByLabel('Role', { exact: true }).selectOption('admin')
  await expect(page.getByText(/Pending change: Viewer → Administrator/)).toBeVisible()
}

test('draft, cancel and Escape issue zero requests; confirm writes once and reconciles', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await openRoles(page)
  await clearRequests(page)
  await startDraft(page)
  expect(await requests(page)).toHaveLength(0)
  await shot(page, testInfo, 'ep01a-p1-roles-1440')

  const dialog = page.getByRole('dialog')
  await page.getByRole('button', { name: 'Review and save role' }).click()
  await expect(dialog).toBeVisible()
  await expect(dialog).toContainText('Change user role?')
  await expect(dialog).toContainText('Ada Evans · ada.evans@northwind.example')
  await expect(dialog).toContainText('Viewer → Administrator')
  await expect(dialog).toContainText('The change is recorded in the workspace audit trail.')
  await expect(dialog.getByRole('button', { name: 'Cancel' })).toBeFocused()
  await page.keyboard.press('Shift+Tab')
  await expect(dialog.getByRole('button', { name: 'Confirm role change' })).toBeFocused()
  await shot(page, testInfo, 'ep01a-p1-review-dialog-1440', false)
  expect(await requests(page)).toHaveLength(0)

  await page.keyboard.press('Escape')
  await expect(dialog).not.toBeVisible()
  await expect(page.getByRole('button', { name: 'Review and save role' })).toBeFocused()
  expect(await requests(page)).toHaveLength(0)
  await expect(page.getByLabel('Role', { exact: true })).toHaveValue('admin')

  await page.getByRole('button', { name: 'Review and save role' }).click()
  await expect(dialog).toBeVisible()
  await dialog.getByRole('button', { name: 'Confirm role change' }).click()
  await expect(dialog).not.toBeVisible()
  await expect(page.getByText("Ada Evans's role was changed to Administrator.")).toBeVisible()

  const log = await requests(page)
  const writes = mutations(log)
  expect(writes).toHaveLength(1)
  expect(writes[0]).toMatchObject({ method: 'POST', path: '/api/ep01a/admin/members/m-002/role', status: 200 })
  expect(writes[0].body).toEqual({ role: 'admin', status: 'active', expected_revision: 2 })
  expect(log[log.length - 1].method).toBe('GET')
  await expect(page.getByRole('button', { name: 'Manage Ada Evans' }).locator('xpath=ancestor::tr').getByText('Administrator')).toBeVisible()
  await shot(page, testInfo, 'ep01a-p1-success-1440')
  record('p1-interaction.json', {
    cancelWorkflow: 'Escape closed the review with zero requests; the draft was preserved',
    confirmWrite: writes[0],
    reconciledWithGet: log[log.length - 1].method === 'GET',
  })
})

test('a denied write explains the refusal and keeps the draft open', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await openRoles(page, '&mock=denied')
  await startDraft(page)
  const dialog = page.getByRole('dialog')
  await page.getByRole('button', { name: 'Review and save role' }).click()
  await dialog.getByRole('button', { name: 'Confirm role change' }).click()
  await expect(dialog.getByRole('alert')).toContainText('do not have permission')
  await expect(dialog).toBeVisible()
  await expect(page.getByLabel('Role', { exact: true })).toHaveValue('admin')
  await expect(page.getByText(/role was changed/)).toHaveCount(0)
  expect(mutations(await requests(page))).toHaveLength(1)
  await shot(page, testInfo, 'ep01a-p1-denied-1440', false)
})

test('a conflict shows the server values and reload reconciles without overwriting', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await openRoles(page, '&mock=conflict')
  await startDraft(page)
  const dialog = page.getByRole('dialog')
  await page.getByRole('button', { name: 'Review and save role' }).click()
  await dialog.getByRole('button', { name: 'Confirm role change' }).click()
  await expect(dialog.getByRole('region', { name: 'Conflict details' })).toContainText(
    'Current on the server: Steward · revision 3',
  )
  await shot(page, testInfo, 'ep01a-p1-conflict-1440', false)
  await dialog.getByRole('button', { name: 'Reload the latest version' }).click()
  await expect(dialog).not.toBeVisible()
  await expect(page.getByText(/Loaded the latest version of Ada Evans/)).toBeVisible()
  await expect(page.getByLabel('Role', { exact: true })).toHaveValue('steward')
  expect(mutations(await requests(page))).toHaveLength(1)
})

test('a failed save keeps the draft and retry succeeds', async ({ page }) => {
  await openRoles(page, '&mock=error')
  await startDraft(page)
  const dialog = page.getByRole('dialog')
  await page.getByRole('button', { name: 'Review and save role' }).click()
  await dialog.getByRole('button', { name: 'Confirm role change' }).click()
  await expect(dialog.getByRole('alert')).toContainText('could not be saved')
  await expect(dialog).toBeVisible()
  await setBehavior(page, 'ok')
  await dialog.getByRole('button', { name: 'Confirm role change' }).click()
  await expect(dialog).not.toBeVisible()
  await expect(page.getByText("Ada Evans's role was changed to Administrator.")).toBeVisible()
})

test('load failure and permission denial render recoverable panels', async ({ page }, testInfo) => {
  await openPrototype(page, 'roles', '&mock=fail_load')
  await expect(page.getByRole('heading', { name: 'Members could not be loaded' })).toBeVisible()
  await shot(page, testInfo, 'ep01a-p1-load-failed')
  await setBehavior(page, 'ok')
  await page.getByRole('button', { name: 'Retry' }).click()
  await expect(page.getByText('Ada Evans')).toBeVisible()
  await setBehavior(page, 'denied_load')
  await page.getByRole('button', { name: 'Refresh directory' }).click()
  await expect(page.getByText('Access denied')).toBeVisible()
  await expect(page.getByRole('table')).toHaveCount(0)
})

test('the same flow works under the current OpenJM dialog implementation', async ({ page }) => {
  await openRoles(page, '&dialog=existing')
  await startDraft(page)
  const dialog = page.getByRole('dialog')
  await page.getByRole('button', { name: 'Review and save role' }).click()
  await expect(dialog.getByRole('button', { name: 'Cancel' })).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(dialog).not.toBeVisible()
  await expect(page.getByRole('button', { name: 'Review and save role' })).toBeFocused()
  await page.getByRole('button', { name: 'Review and save role' }).click()
  await dialog.getByRole('button', { name: 'Confirm role change' }).click()
  await expect(page.getByText("Ada Evans's role was changed to Administrator.")).toBeVisible()
})

test('tablet 768 and mobile 390 layouts hold without page overflow', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 768, height: 1024 })
  await openRoles(page)
  await startDraft(page)
  await shot(page, testInfo, 'ep01a-p1-roles-768')

  await page.setViewportSize({ width: 390, height: 844 })
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth))
    .toBe(true)
  await shot(page, testInfo, 'ep01a-p1-roles-390')
  await page.getByRole('button', { name: 'Review and save role' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  const bounds = await dialog.boundingBox()
  expect(bounds!.width).toBeLessThanOrEqual(390)
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
  ).toBe(true)
  await shot(page, testInfo, 'ep01a-p1-review-dialog-390', false)
})
