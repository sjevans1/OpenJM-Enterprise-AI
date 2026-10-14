import { expect, test, type Page } from '@playwright/test'

async function workspace(page: Page) {
  const mutations: { path: string; body: unknown }[] = []
  const members = [
    { principal_id: 'ada', subject: 'sub-ada', display_name: 'Ada Evans', email: 'ada@example.test', role: 'viewer', status: 'active', department_ids: ['hr'], group_ids: ['team'] },
    { principal_id: 'owner', subject: 'sub-owner', display_name: 'Morgan James', role: 'owner', status: 'active', department_ids: [], group_ids: [] },
  ]
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    let result: unknown = []
    if (request.method() !== 'GET') mutations.push({ path, body: request.postDataJSON() })
    if (path === '/api/auth/config') result = { auth_mode: 'dev', oidc_configured: false }
    if (path === '/api/admin/members') result = members
    if (path === '/api/admin/departments') result = [{ id: 'hr', name: 'Human Resources', slug: 'hr', status: 'active' }]
    if (path === '/api/admin/groups') result = [{ id: 'team', name: 'HR Team', slug: 'team', status: 'active', department_id: 'hr' }]
    if (path === '/api/admin/members/ada') { members[0].role = request.postDataJSON().role; result = members[0] }
    if (path === '/api/admin/data-access') result = {
      sources: [{ id: 'payroll', name: 'Payroll warehouse', classification: 'confidential', department_id: 'hr', tenant_visible: false, allowed_group_ids: ['team'] }],
      documents: [{ id: 'handbook', title: 'Employee Handbook.pdf', classification: 'internal', tenant_visible: true, allowed_group_ids: [] }],
    }
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(result) })
  })
  await page.goto('/')
  await page.getByRole('button', { name: 'Open navigation' }).waitFor({ state: 'attached' })
  if (await page.getByRole('button', { name: 'Open navigation' }).isVisible()) {
    await page.getByRole('button', { name: 'Open navigation' }).click()
  }
  await page.getByRole('button', { name: 'Administration', exact: true }).click()
  await expect(page.getByText('Ada Evans', { exact: true })).toBeVisible()
  return mutations
}

test('role changes are reviewed, cancelable and keyboard accessible', async ({ page }, testInfo) => {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  const mutations = await workspace(page)
  await page.screenshot({ path: testInfo.outputPath('administration.png'), fullPage: true })
  await page.getByLabel('Role for Ada Evans').selectOption('admin')
  expect(mutations).toHaveLength(0)
  await page.getByRole('button', { name: 'Save role', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: 'Change user role?' })
  await expect(dialog).toContainText('Ada Evans: Viewer → Administrator')
  await expect(dialog.getByRole('button', { name: 'Cancel', exact: true })).toBeFocused()
  await page.keyboard.press('Shift+Tab')
  await expect(dialog.getByRole('button', { name: 'Confirm role change' })).toBeFocused()
  await page.screenshot({ path: testInfo.outputPath('role-confirmation.png'), fullPage: true })
  await page.keyboard.press('Escape')
  await expect(dialog).not.toBeVisible()
  expect(mutations).toHaveLength(0)
  await page.getByRole('button', { name: 'Save role', exact: true }).click()
  await page.getByRole('button', { name: 'Confirm role change' }).click()
  await expect(dialog).not.toBeVisible()
  expect(mutations).toEqual([{ path: '/api/admin/members/ada', body: { role: 'admin' } }])
  await expect(page.getByRole('button', { name: 'Save role', exact: true })).toHaveCount(0)
  expect(errors).toEqual([])
})

test('policy drafts preserve grants and warn before leaving administration', async ({ page }, testInfo) => {
  const mutations = await workspace(page)
  await page.getByRole('button', { name: 'Data Access', exact: true }).click()
  await page.getByRole('button', { name: 'Edit policy for Payroll warehouse' }).click()
  await expect(page.getByLabel('HR Team', { exact: true })).toBeChecked()
  await page.screenshot({ path: testInfo.outputPath('policy-editor.png'), fullPage: true })
  await page.getByRole('button', { name: 'Chat', exact: true }).click()
  const discard = page.getByRole('dialog', { name: 'Discard unsaved administration changes?' })
  await expect(discard).toBeVisible()
  await discard.getByRole('button', { name: 'Cancel', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Edit policy: Payroll warehouse' })).toBeVisible()
  await page.getByRole('button', { name: 'Cancel policy changes' }).click()
  expect(mutations).toHaveLength(0)
  await page.getByRole('button', { name: /^Data\s*0$/ }).click()
  await expect(page.getByText('New sources stay disabled until you enable them.', { exact: false })).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('data-setup.png'), fullPage: true })
})

test('administration remains usable on a narrow screen', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await workspace(page)
  await page.screenshot({ path: testInfo.outputPath('administration-mobile.png'), fullPage: true })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await page.getByLabel('Role for Ada Evans').selectOption('admin')
  await page.getByRole('button', { name: 'Save role', exact: true }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  const bounds = await page.getByRole('dialog').boundingBox()
  expect(bounds!.width).toBeLessThanOrEqual(390)
  await page.getByRole('button', { name: 'Cancel', exact: true }).click()
})
