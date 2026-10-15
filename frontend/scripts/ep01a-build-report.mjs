#!/usr/bin/env node
/**
 * EP-01A — build report for the evidence pack.
 *
 * Reads the production build output (run `npm run build` first) and writes:
 *   - per-file raw + gzip sizes for every emitted asset,
 *   - the exact tested dependency versions for the component candidates.
 *
 * Usage: node scripts/ep01a-build-report.mjs <output.json> [distDir]
 */
import fs from 'node:fs'
import path from 'node:path'
import zlib from 'node:zlib'

const output = process.argv[2]
if (!output) {
  console.error('usage: node scripts/ep01a-build-report.mjs <output.json> [distDir]')
  process.exit(2)
}
const distDir = path.resolve(process.argv[3] || 'dist')

const packageJson = JSON.parse(
  fs.readFileSync(new URL('../package.json', import.meta.url), 'utf8'),
)
const candidates = [
  '@radix-ui/react-dialog',
  'react-aria-components',
  '@base-ui/react',
  '@axe-core/playwright',
]

function walk(dir) {
  const out = []
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) out.push(...walk(full))
    else out.push(full)
  }
  return out
}

const assets = walk(distDir)
  .filter((file) => /\.(js|css|html)$/.test(file))
  .map((file) => {
    const bytes = fs.readFileSync(file)
    return {
      file: path.relative(distDir, file),
      bytes: bytes.length,
      gzipBytes: zlib.gzipSync(bytes).length,
    }
  })
  .sort((a, b) => b.bytes - a.bytes)

const report = {
  producedAt: new Date().toISOString(),
  packageVersions: Object.fromEntries(
    candidates.map((name) => [
      name,
      packageJson.dependencies?.[name] ?? packageJson.devDependencies?.[name] ?? null,
    ]),
  ),
  assets,
  totals: {
    jsBytes: assets.filter((a) => a.file.endsWith('.js')).reduce((n, a) => n + a.bytes, 0),
    jsGzipBytes: assets.filter((a) => a.file.endsWith('.js')).reduce((n, a) => n + a.gzipBytes, 0),
    cssBytes: assets.filter((a) => a.file.endsWith('.css')).reduce((n, a) => n + a.bytes, 0),
    cssGzipBytes: assets.filter((a) => a.file.endsWith('.css')).reduce((n, a) => n + a.gzipBytes, 0),
  },
}

fs.mkdirSync(path.dirname(path.resolve(output)), { recursive: true })
fs.writeFileSync(output, JSON.stringify(report, null, 2) + '\n')
console.log(`wrote ${output} (${assets.length} assets)`)
