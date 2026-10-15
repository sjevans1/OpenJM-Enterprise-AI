#!/usr/bin/env node
/**
 * EP-01A — production-exclusion gate.
 *
 * Verifies that a production release build cannot expose the `?prototype=ep01a`
 * surfaces or the synthetic /api/ep01a mock, merely by knowing the URL:
 *
 *  1. the built asset graph contains no prototype chunk and no `ep01a`
 *     token (the mount branch is compile-time dead in production);
 *  2. the production preview server serves the normal application for
 *     `/?prototype=ep01a`: no prototype banner, no `window.__ep01a`.
 *
 * Writes evidence JSON + screenshot when EP01A_EVIDENCE_DIR is set; exits
 * non-zero when the prototype is reachable in the production build.
 *
 * Usage: node scripts/ep01a-production-gate.mjs   (run `npm run build` first)
 */
import { spawn } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { chromium } from '@playwright/test'

const frontendDir = path.resolve(new URL('..', import.meta.url).pathname)
const distDir = path.join(frontendDir, 'dist')
const PORT = 4173

function fail(message) {
  console.error(`EP01A PRODUCTION GATE FAILED: ${message}`)
  process.exit(1)
}

// --- 1. static build inspection -------------------------------------------
if (!fs.existsSync(path.join(distDir, 'index.html'))) {
  fail('dist/index.html is missing — run `npm run build` first')
}
const assets = []
const walk = (dir) => {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) walk(full)
    else assets.push(full)
  }
}
walk(distDir)
const prototypeChunks = assets.filter((file) =>
  /PrototypeApp|RadixDialog|AriaDialog|BaseUiDialog/.test(path.basename(file)),
)
if (prototypeChunks.length) {
  fail(`prototype chunks present in the production build: ${prototypeChunks.join(', ')}`)
}
const ep01aTokens = []
for (const file of assets) {
  if (!/\.(js|css|html)$/.test(file)) continue
  const text = fs.readFileSync(file, 'utf8')
  if (text.includes('ep01a') || text.includes('EP-01A prototype') || text.includes('/api/ep01a/')) {
    ep01aTokens.push(path.relative(distDir, file))
  }
}
if (ep01aTokens.length) {
  fail(`prototype code/tokens found in production assets: ${ep01aTokens.join(', ')}`)
}

// --- 2. runtime inspection against the preview server ---------------------
const preview = spawn('npm', ['run', 'preview', '--', '--port', String(PORT), '--strictPort'], {
  cwd: frontendDir,
  stdio: 'ignore',
  detached: false,
})
const stopPreview = () => {
  try {
    preview.kill('SIGTERM')
  } catch {
    /* already gone */
  }
}
process.on('exit', stopPreview)

async function waitForServer(url, attempts = 40) {
  for (let i = 0; i < attempts; i += 1) {
    try {
      const response = await fetch(url)
      if (response.ok) return true
    } catch {
      /* retry */
    }
    await new Promise((resolve) => setTimeout(resolve, 250))
  }
  return false
}

const base = `http://127.0.0.1:${PORT}`
if (!(await waitForServer(base))) fail('preview server did not become ready')

const browser = await chromium.launch()
try {
  const page = await browser.newPage()
  await page.goto(`${base}/?prototype=ep01a`)
  await page.waitForTimeout(1500) // let the production app mount

  const banner = await page.getByText('EP-01A prototype').count()
  const fixtureText = await page.getByText(/Fixture data only/).count()
  const mockHandle = await page.evaluate(() => typeof window.__ep01a !== 'undefined')
  const screenshotDir = process.env.EP01A_EVIDENCE_DIR
    ? path.resolve(process.env.EP01A_EVIDENCE_DIR)
    : null
  if (screenshotDir) {
    fs.mkdirSync(path.join(screenshotDir, 'screenshots'), { recursive: true })
    await page.screenshot({
      path: path.join(screenshotDir, 'screenshots', 'ep01a-production-gate.png'),
      fullPage: true,
    })
  }
  if (banner !== 0 || fixtureText !== 0 || mockHandle) {
    fail(
      `prototype reachable in the production build (banner=${banner}, fixture=${fixtureText}, mock=${mockHandle})`,
    )
  }
  const report = {
    at: new Date().toISOString(),
    distHasPrototypeChunks: false,
    distHasEp01aTokens: false,
    previewUrl: `${base}/?prototype=ep01a`,
    prototypeBannerCount: banner,
    mockApiPresent: mockHandle,
    screenshot: screenshotDir ? 'screenshots/ep01a-production-gate.png' : null,
  }
  console.log(`EP01A PRODUCTION GATE PASSED\n${JSON.stringify(report, null, 2)}`)
  if (screenshotDir) {
    fs.writeFileSync(
      path.join(screenshotDir, 'production-gate.json'),
      JSON.stringify(report, null, 2) + '\n',
    )
  }
} finally {
  await browser.close()
  stopPreview()
}
