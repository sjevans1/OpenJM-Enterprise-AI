/**
 * EP-01A evidence helpers for the Playwright specs.
 *
 * When EP01A_EVIDENCE_DIR is set (local evidence runs), screenshots and JSON
 * records are written there for committing. In CI the same calls fall back to
 * Playwright's per-test output directory, which the workflow uploads as the
 * browser-evidence artifact.
 */
import fs from 'node:fs'
import path from 'node:path'
import { expect, type Page, type TestInfo } from '@playwright/test'

const evidenceDir = process.env.EP01A_EVIDENCE_DIR

/**
 * Navigate to a prototype surface. The app's branding fetch
 * (/api/config/public) is stubbed so no request leaks to the dev proxy — the
 * mock boundary for prototype data itself is the in-page /api/ep01a shim.
 */
export async function openPrototype(
  page: Page,
  view: 'roles' | 'catalog' | 'probes',
  extra = '',
  latency?: number,
): Promise<void> {
  const query = new URLSearchParams({
    prototype: 'ep01a',
    view,
    latency: String(latency ?? 0),
  })
  for (const [key, value] of new URLSearchParams(extra.replace(/^[&?]/, ''))) {
    query.set(key, value)
  }
  await page.route('**/api/config/public', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: '{}' }),
  )
  await page.goto(`/?${query.toString()}`)
  await expect(page.getByText('EP-01A prototype')).toBeVisible()
}

export function record(name: string, data: unknown): void {
  if (!evidenceDir) return
  const dir = path.resolve(evidenceDir)
  fs.mkdirSync(dir, { recursive: true })
  fs.writeFileSync(path.join(dir, name), JSON.stringify(data, null, 2) + '\n')
}

export async function shot(
  page: Page,
  testInfo: TestInfo,
  name: string,
  fullPage = true,
): Promise<void> {
  if (evidenceDir) {
    const dir = path.resolve(evidenceDir, 'screenshots')
    fs.mkdirSync(dir, { recursive: true })
    await page.screenshot({ path: path.join(dir, `${name}.png`), fullPage })
    return
  }
  await page.screenshot({ path: testInfo.outputPath(`${name}.png`), fullPage })
}

export type MockRequest = {
  method: string
  path: string
  query: string
  body: unknown
  status: number
}

export async function requests(page: Page): Promise<MockRequest[]> {
  return page.evaluate(() =>
    ((window as unknown as { __ep01a?: { requests: MockRequest[] } }).__ep01a?.requests ?? []).map(
      (entry) => ({
        method: entry.method,
        path: entry.path,
        query: entry.query,
        body: entry.body,
        status: entry.status,
      }),
    ),
  )
}

export async function clearRequests(page: Page): Promise<void> {
  await page.evaluate(() => {
    ;(window as unknown as { __ep01a?: { clear: () => void } }).__ep01a?.clear()
  })
}

export async function setBehavior(page: Page, behavior: string): Promise<void> {
  await page.evaluate((value) => {
    ;(
      window as unknown as { __ep01a?: { setBehavior: (b: never) => void } }
    ).__ep01a?.setBehavior(value as never)
  }, behavior)
}

export function mutations(entries: MockRequest[]): MockRequest[] {
  return entries.filter((entry) => entry.method !== 'GET')
}
