/**
 * EP-01A Prototype P2 — resource catalog (browser).
 *
 * Proves, in a real Chromium against real rendered screens:
 *  - truthful server-side totals, ranges and stable row identity;
 *  - search/filter accuracy compared against the mock's own responses;
 *  - loading / empty / stale / failed / denied states with recovery;
 *  - detail view with metadata, keyboard open/close and focus return;
 *  - desktop 1440x900, tablet 768x1024, mobile 390x844, 320px reflow,
 *    200%-zoom-equivalent reflow, internal table overflow and reduced motion.
 */
import { expect, test, type Page } from '@playwright/test'
import { openPrototype, record, setBehavior, shot } from './ep01a-evidence'

async function openCatalog(page: Page, extra = '') {
  await openPrototype(page, 'catalog', extra)
  await expect(page.getByText(/Showing 1–25 of 168 resources/)).toBeVisible()
}

test('totals, pagination ranges and stable row identity are truthful', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await openCatalog(page)
  await shot(page, testInfo, 'ep01a-p2-catalog-1440')

  const firstIdPage1 = await page.locator('tr[data-resource-id]').first().getAttribute('data-resource-id')
  await page.getByRole('button', { name: 'Next page' }).click()
  await expect(page.getByText(/Showing 26–50 of 168 resources/)).toBeVisible()
  const firstIdPage2 = await page.locator('tr[data-resource-id]').first().getAttribute('data-resource-id')
  expect(firstIdPage2).not.toBe(firstIdPage1)
  await page.getByRole('button', { name: 'Previous page' }).click()
  await expect(page.getByText(/Showing 1–25 of 168 resources/)).toBeVisible()
  expect(await page.locator('tr[data-resource-id]').first().getAttribute('data-resource-id')).toBe(firstIdPage1)

  await page.getByLabel('Rows per page').selectOption('10')
  await expect(page.getByText(/Showing 1–10 of 168 resources/)).toBeVisible()
  record('p2-pagination.json', { firstIdPage1, firstIdPage2, pageSizeSwitch: '1–10 of 168' })
})

test('search and filters narrow results with counts matching the dataset', async ({ page }) => {
  await openCatalog(page)
  const expected = await page.evaluate(async () => {
    const response = await fetch('/api/ep01a/catalog?page=1&page_size=25&sort=updated&query=payroll')
    return response.json()
  })
  await page.getByLabel('Search', { exact: true }).fill('payroll')
  await expect(
    page.getByText(new RegExp(`Showing 1–${Math.min(25, expected.total)} of ${expected.total} resources`)),
  ).toBeVisible()

  await page.getByLabel('Search', { exact: true }).fill('zzz-nothing-matches')
  await expect(page.getByText('No resources match')).toBeVisible()
  await page.getByRole('button', { name: 'Clear search and filters' }).click()
  await expect(page.getByText(/Showing 1–25 of 168 resources/)).toBeVisible()
})

test('stale data is flagged and reconciles through refresh', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await openCatalog(page, '&mock=stale')
  const banner = page.getByText(/An upstream source has refreshed since/)
  await expect(banner).toBeVisible()
  await shot(page, testInfo, 'ep01a-p2-stale-1440')
  await page.getByRole('button', { name: 'Refresh now' }).click()
  await expect(banner).toHaveCount(0)
  await expect(page.getByText(/Showing 1–25 of 168 resources/)).toBeVisible()
})

test('failed and denied loads render recoverable states', async ({ page }, testInfo) => {
  await openPrototype(page, 'catalog', '&mock=fail_load')
  await expect(page.getByRole('heading', { name: 'Catalog could not be loaded' })).toBeVisible()
  await shot(page, testInfo, 'ep01a-p2-load-failed')
  await setBehavior(page, 'ok')
  await page.getByRole('button', { name: 'Retry' }).click()
  await expect(page.getByText(/Showing 1–25 of 168 resources/)).toBeVisible()

  await setBehavior(page, 'denied_load')
  await page.getByRole('button', { name: 'Refresh catalog' }).click()
  await expect(page.getByText('Access denied')).toBeVisible()
  await expect(page.getByRole('table')).toHaveCount(0)
})

test('detail view shows metadata, closes on Escape and restores focus', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await openCatalog(page)
  const trigger = page.getByRole('button', { name: /^View details for / }).first()
  await trigger.click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  await expect(dialog.getByText(/Metadata \(\d+ entries\)/)).toBeVisible()
  await shot(page, testInfo, 'ep01a-p2-detail-1440', false)
  // Modal containment: the background is inert and Tab cannot leave the drawer.
  expect(
    await page.evaluate(() =>
      document.querySelector('.ep01a-shell > nav')?.hasAttribute('inert'),
    ),
  ).toBe(true)
  for (let step = 0; step < 6; step += 1) {
    await page.keyboard.press('Tab')
  }
  expect(
    await page.evaluate(() =>
      Boolean((document.activeElement as HTMLElement | null)?.closest('.ep01a-detail')),
    ),
  ).toBe(true)
  await page.keyboard.press('Shift+Tab')
  expect(
    await page.evaluate(() =>
      Boolean((document.activeElement as HTMLElement | null)?.closest('.ep01a-detail')),
    ),
  ).toBe(true)
  await page.keyboard.press('Escape')
  await expect(dialog).not.toBeVisible()
  await expect
    .poll(() =>
      page.evaluate(() =>
        document.querySelector('.ep01a-shell > nav')?.hasAttribute('inert') ?? true,
      ),
    )
    .toBe(false)
  const focusedLabel = await page.evaluate(
    () => (document.activeElement as HTMLElement | null)?.getAttribute('aria-label') ?? '',
  )
  expect(focusedLabel).toMatch(/^View details for /)
})

test('responsive: 390 mobile, 768 tablet, 320 reflow, 200% zoom, reduced motion', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await openCatalog(page)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  const overflow = await page.evaluate(() => {
    const container = document.querySelector('.ep01a-table-scroll')
    return container ? { scrollWidth: container.scrollWidth, clientWidth: container.clientWidth } : null
  })
  expect(overflow!.scrollWidth).toBeGreaterThan(overflow!.clientWidth)
  await shot(page, testInfo, 'ep01a-p2-catalog-390')

  await page.setViewportSize({ width: 768, height: 1024 })
  await expect(page.getByText(/Showing 1–25 of 168 resources/)).toBeVisible()
  await shot(page, testInfo, 'ep01a-p2-catalog-768')

  await page.setViewportSize({ width: 320, height: 844 })
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth))
    .toBe(true)
  await shot(page, testInfo, 'ep01a-p2-catalog-320')

  // 200% zoom equivalent at a 1440x900 physical screen is a 720px CSS viewport.
  await page.setViewportSize({ width: 720, height: 900 })
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth))
    .toBe(true)

  await page.emulateMedia({ reducedMotion: 'reduce' })
  await openPrototype(page, 'catalog', '', 500)
  const skeleton = page.locator('.ep01a-skeleton-row').first()
  await expect(skeleton).toBeVisible()
  expect(
    await skeleton.evaluate((element) => getComputedStyle(element).animationName),
  ).toBe('none')
  await expect(page.getByText(/Showing 1–25 of 168 resources/)).toBeVisible()
})
