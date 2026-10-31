/**
 * EP-01A — automated accessibility scans (axe-core) across prototype states.
 *
 * Records every scan (violations with impact, incomplete/pass counts) as JSON
 * evidence and fails on any serious or critical violation. Automated scans are
 * a supplement; the keyboard, focus and reflow behaviour is asserted in the
 * prototype specs and reviewed manually.
 */
import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import { openPrototype, record } from './ep01a-evidence'

async function scan(page: Page, name: string): Promise<void> {
  const results = await new AxeBuilder({ page }).analyze()
  record(`axe-${name}.json`, {
    url: page.url(),
    violations: results.violations.map((violation) => ({
      id: violation.id,
      impact: violation.impact,
      help: violation.help,
      nodes: violation.nodes.length,
      targets: violation.nodes.map((node) => node.target.join(' ')).slice(0, 8),
    })),
    incomplete: results.incomplete.map((item) => ({
      id: item.id,
      impact: item.impact,
      nodes: item.nodes.length,
      targets: item.nodes.map((node) => node.target.join(' ')).slice(0, 8),
      summary: item.nodes.map((node) => node.failureSummary ?? '').slice(0, 4),
    })),
    passes: results.passes.length,
  })
  const serious = results.violations.filter(
    (violation) => violation.impact === 'serious' || violation.impact === 'critical',
  )
  expect(
    serious,
    JSON.stringify(
      serious.map((violation) => ({
        id: violation.id,
        help: violation.help,
        targets: violation.nodes.map((node) => node.target.join(' ')),
      })),
      null,
      2,
    ),
  ).toEqual([])
}

test('scan: governed roles surface and review dialog', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await openPrototype(page, 'roles')
  await expect(page.getByText('Ada Evans')).toBeVisible()
  await scan(page, 'p1-roles')

  await page.getByRole('button', { name: 'Manage Ada Evans' }).click()
  await page.getByLabel('Role', { exact: true }).selectOption('admin')
  await page.getByRole('button', { name: 'Review and save role' }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await scan(page, 'p1-review-dialog')
  await page.keyboard.press('Escape')
})

test('scan: catalog, detail and empty states', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await openPrototype(page, 'catalog')
  await expect(page.getByText(/Showing 1–25 of 168 resources/)).toBeVisible()
  await scan(page, 'p2-catalog')

  await page.getByRole('button', { name: /^View details for / }).first().click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await scan(page, 'p2-detail')
  await page.keyboard.press('Escape')

  await page.getByLabel('Search', { exact: true }).fill('zzz-nothing-matches')
  await expect(page.getByText('No resources match')).toBeVisible()
  await scan(page, 'p2-empty')
})
