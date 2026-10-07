import { describe, expect, test, vi } from 'vitest'
import { applyBranding, DEFAULT_PUBLIC_CONFIG, loadPublicConfig } from './branding'

describe('white-label branding (VS8 H)', () => {
  test('sets the title and data attributes from public config', () => {
    document.title = ''
    applyBranding(
      {
        ...DEFAULT_PUBLIC_CONFIG,
        product_name: 'Acme Insight',
        browser_page_title: 'Acme Insight',
        version: '1.2.3',
        release_id: 'rc-1',
      },
      document,
    )
    expect(document.title).toBe('Acme Insight 1.2.3')
    expect(document.documentElement.getAttribute('data-openjm-product')).toBe('Acme Insight')
    expect(document.documentElement.getAttribute('data-openjm-version')).toBe('1.2.3')
    expect(document.documentElement.getAttribute('data-openjm-release')).toBe('rc-1')
  })

  test('applies a valid accent colour but ignores an invalid one', () => {
    applyBranding({ ...DEFAULT_PUBLIC_CONFIG, theme_accent: '#0a7cff' }, document)
    expect(document.documentElement.style.getPropertyValue('--openjm-accent')).toBe('#0a7cff')

    applyBranding(
      { ...DEFAULT_PUBLIC_CONFIG, theme_accent: 'red; background:url(x)' },
      document,
    )
    expect(document.documentElement.style.getPropertyValue('--openjm-accent')).toBe('#0a7cff')
  })

  test('never injects HTML from a configured name', () => {
    applyBranding(
      { ...DEFAULT_PUBLIC_CONFIG, product_name: '<img src=x onerror=alert(1)>' },
      document,
    )
    // The literal text is stored as an attribute value, not parsed as HTML.
    expect(document.documentElement.getAttribute('data-openjm-product')).toBe(
      '<img src=x onerror=alert(1)>',
    )
    expect(document.querySelector('img')).toBeNull()
  })

  test('loadPublicConfig falls back to defaults when the endpoint fails', async () => {
    const failing = vi.fn().mockResolvedValue({ ok: false } as Response)
    const config = await loadPublicConfig(failing as unknown as typeof fetch)
    expect(config).toEqual(DEFAULT_PUBLIC_CONFIG)
  })

  test('loadPublicConfig merges a successful response over defaults', async () => {
    const ok = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ product_name: 'Acme', version: '9.9.9' }),
    } as Response)
    const config = await loadPublicConfig(ok as unknown as typeof fetch)
    expect(config.product_name).toBe('Acme')
    expect(config.version).toBe('9.9.9')
    expect(config.organization_name).toBe('')
  })
})
