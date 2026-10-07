/**
 * VS8 Workstream H — white-label / release metadata for the client.
 *
 * Branding values are display text from the backend's `/api/config/public`
 * endpoint. They are applied with `textContent` and setAttribute only — never
 * `innerHTML` — so a configured name cannot become an HTML/script injection
 * surface. The provider endpoint and any credential are never part of this
 * payload.
 */

export type PublicConfig = {
  product_name: string
  organization_name: string
  brand_logo_url: string
  browser_page_title: string
  support_contact: string
  theme_accent: string
  version: string
  release_id: string | null
}

export const DEFAULT_PUBLIC_CONFIG: PublicConfig = {
  product_name: 'OpenJM Enterprise AI',
  organization_name: '',
  brand_logo_url: '',
  browser_page_title: 'OpenJM Enterprise AI',
  support_contact: '',
  theme_accent: '',
  version: '0.0.0',
  release_id: null,
}

const HEX_COLOR = /^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/

/** Apply public config to the document. Pure DOM writes; no HTML injection. */
export function applyBranding(
  config: PublicConfig,
  doc: Document = document,
): void {
  const title = config.browser_page_title || config.product_name
  doc.title = config.version ? `${title} ${config.version}` : title

  const root = doc.documentElement
  root.setAttribute('data-openjm-product', config.product_name)
  root.setAttribute('data-openjm-version', config.version)
  if (config.release_id) {
    root.setAttribute('data-openjm-release', config.release_id)
  }

  // Only a well-formed hex colour is applied; anything else is ignored so a
  // bad value cannot smuggle a CSS payload.
  if (config.theme_accent && HEX_COLOR.test(config.theme_accent)) {
    root.style.setProperty('--openjm-accent', config.theme_accent)
  }
}

/** Fetch public config without authentication; never throws on failure. */
export async function loadPublicConfig(fetchImpl: typeof fetch = fetch): Promise<PublicConfig> {
  try {
    const response = await fetchImpl('/api/config/public')
    if (!response.ok) return DEFAULT_PUBLIC_CONFIG
    const body = (await response.json()) as Partial<PublicConfig>
    return { ...DEFAULT_PUBLIC_CONFIG, ...body }
  } catch {
    return DEFAULT_PUBLIC_CONFIG
  }
}
