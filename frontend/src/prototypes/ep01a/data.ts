/**
 * EP-01A prototype — deterministic synthetic business data.
 *
 * This data exists only for the EP-01A component/UX prototypes. It is not
 * seeded into any database, is never served by the OpenJM backend, and must
 * never be presented as real customer content. Everything here is generated
 * deterministically (fixed seed, no Math.random) so screenshots, tests and
 * recorded evidence stay reproducible.
 */

export type RoleId = 'owner' | 'admin' | 'steward' | 'viewer'
export type MemberStatus = 'active' | 'suspended' | 'invited'

export type Member = {
  id: string
  name: string
  email: string
  subject: string
  role: RoleId
  status: MemberStatus
  department: string
  lastActive: string
  revision: number
}

export type RoleDefinition = {
  id: RoleId
  label: string
  capabilities: string
}

export const ROLES: RoleDefinition[] = [
  {
    id: 'owner',
    label: 'Owner',
    capabilities:
      'Full workspace administration, identity control and service decisions. A workspace must keep at least one owner.',
  },
  {
    id: 'admin',
    label: 'Administrator',
    capabilities:
      'Manage people, roles and information policies across this workspace. Cannot remove the workspace owner.',
  },
  {
    id: 'steward',
    label: 'Steward',
    capabilities:
      'Maintain assigned sources, documents and connection settings they are responsible for.',
  },
  {
    id: 'viewer',
    label: 'Viewer',
    capabilities:
      'Ask questions and use artifacts they are permitted to access. No administrative access.',
  },
]

export function roleLabel(id: RoleId): string {
  return ROLES.find((role) => role.id === id)?.label || id
}

export function roleCapabilities(id: RoleId): string {
  return ROLES.find((role) => role.id === id)?.capabilities || ''
}

/** Deterministic 26-member directory. One owner so the last-owner guard is provable. */
export const MEMBERS: Member[] = [
  { id: 'm-001', name: 'Morgan James', email: 'morgan.james@northwind.example', subject: 'sub-8f21-morgan', role: 'owner', status: 'active', department: 'Executive', lastActive: '2026-10-14T09:12:00Z', revision: 4 },
  { id: 'm-002', name: 'Ada Evans', email: 'ada.evans@northwind.example', subject: 'sub-3c7a-ada', role: 'viewer', status: 'active', department: 'People & Culture', lastActive: '2026-10-14T08:40:00Z', revision: 2 },
  { id: 'm-003', name: 'Priya Raman', email: 'priya.raman@northwind.example', subject: 'sub-11de-priya', role: 'admin', status: 'active', department: 'Engineering', lastActive: '2026-10-13T17:05:00Z', revision: 3 },
  { id: 'm-004', name: 'Tomasz Kowalski', email: 'tomasz.kowalski@northwind.example', subject: 'sub-77b0-tomasz', role: 'admin', status: 'active', department: 'Finance', lastActive: '2026-10-12T11:22:00Z', revision: 1 },
  { id: 'm-005', name: 'Sofia Marchetti', email: 'sofia.marchetti@northwind.example', subject: 'sub-9021-sofia', role: 'steward', status: 'active', department: 'Legal', lastActive: '2026-10-14T07:55:00Z', revision: 5 },
  { id: 'm-006', name: 'Daniel Okafor', email: 'daniel.okafor@northwind.example', subject: 'sub-4a02-daniel', role: 'steward', status: 'active', department: 'Operations', lastActive: '2026-10-11T15:30:00Z', revision: 2 },
  { id: 'm-007', name: 'Helena Voss', email: 'helena.voss@northwind.example', subject: 'sub-e331-helena', role: 'steward', status: 'active', department: 'Finance', lastActive: '2026-10-10T09:00:00Z', revision: 1 },
  { id: 'm-008', name: 'Marcus Reid', email: 'marcus.reid@northwind.example', subject: 'sub-5d0c-marcus', role: 'steward', status: 'suspended', department: 'Sales', lastActive: '2026-09-28T13:14:00Z', revision: 6 },
  { id: 'm-009', name: 'Yuki Tanaka', email: 'yuki.tanaka@northwind.example', subject: 'sub-c918-yuki', role: 'steward', status: 'active', department: 'Engineering', lastActive: '2026-10-14T06:20:00Z', revision: 2 },
  { id: 'm-010', name: 'Amara Diallo', email: 'amara.diallo@northwind.example', subject: 'sub-2f77-amara', role: 'steward', status: 'active', department: 'People & Culture', lastActive: '2026-10-09T16:45:00Z', revision: 1 },
  { id: 'm-011', name: 'George Papadopoulos', email: 'george.papadopoulos@northwind.example', subject: 'sub-b210-george', role: 'viewer', status: 'active', department: 'Operations', lastActive: '2026-10-13T10:02:00Z', revision: 1 },
  { id: 'm-012', name: 'Ingrid Larsen', email: 'ingrid.larsen@northwind.example', subject: 'sub-6ee4-ingrid', role: 'viewer', status: 'active', department: 'Legal', lastActive: '2026-10-14T05:48:00Z', revision: 3 },
  { id: 'm-013', name: 'Rafael Ortiz', email: 'rafael.ortiz@northwind.example', subject: 'sub-3900-rafael', role: 'viewer', status: 'active', department: 'Sales', lastActive: '2026-10-12T14:36:00Z', revision: 1 },
  { id: 'm-014', name: 'Naomi Feldman', email: 'naomi.feldman@northwind.example', subject: 'sub-aa13-naomi', role: 'viewer', status: 'active', department: 'Finance', lastActive: '2026-10-13T19:21:00Z', revision: 2 },
  { id: 'm-015', name: 'Chen Wei', email: 'chen.wei@northwind.example', subject: 'sub-7742-chen', role: 'viewer', status: 'invited', department: 'Engineering', lastActive: '2026-10-08T08:00:00Z', revision: 1 },
  { id: 'm-016', name: 'Fatima Al-Sayed', email: 'fatima.al-sayed@northwind.example', subject: 'sub-e012-fatima', role: 'viewer', status: 'active', department: 'Operations', lastActive: '2026-10-14T04:12:00Z', revision: 2 },
  { id: 'm-017', name: 'Jonas Berg', email: 'jonas.berg@northwind.example', subject: 'sub-1b9d-jonas', role: 'viewer', status: 'active', department: 'Engineering', lastActive: '2026-10-11T12:33:00Z', revision: 1 },
  { id: 'm-018', name: 'Lucia Ferreira', email: 'lucia.ferreira@northwind.example', subject: 'sub-5c31-lucia', role: 'viewer', status: 'suspended', department: 'Sales', lastActive: '2026-09-19T09:41:00Z', revision: 4 },
  { id: 'm-019', name: 'Noah Andersen', email: 'noah.andersen@northwind.example', subject: 'sub-d42a-noah', role: 'viewer', status: 'active', department: 'People & Culture', lastActive: '2026-10-13T08:17:00Z', revision: 1 },
  { id: 'm-020', name: 'Zara Hussain', email: 'zara.hussain@northwind.example', subject: 'sub-88f0-zara', role: 'viewer', status: 'active', department: 'Legal', lastActive: '2026-10-10T15:05:00Z', revision: 2 },
  { id: 'm-021', name: 'Oskar Novak', email: 'oskar.novak@northwind.example', subject: 'sub-7d3e-oskar', role: 'viewer', status: 'invited', department: 'Finance', lastActive: '2026-10-07T11:00:00Z', revision: 1 },
  { id: 'm-022', name: 'Elena Petrova', email: 'elena.petrova@northwind.example', subject: 'sub-61cd-elena', role: 'viewer', status: 'active', department: 'Operations', lastActive: '2026-10-14T06:58:00Z', revision: 3 },
  { id: 'm-023', name: 'Samuel Adeyemi', email: 'samuel.adeyemi@northwind.example', subject: 'sub-0fed-samuel', role: 'viewer', status: 'active', department: 'Engineering', lastActive: '2026-10-12T18:26:00Z', revision: 1 },
  { id: 'm-024', name: 'Hannah Weber', email: 'hannah.weber@northwind.example', subject: 'sub-4cdd-hannah', role: 'viewer', status: 'active', department: 'People & Culture', lastActive: '2026-10-13T13:09:00Z', revision: 2 },
  { id: 'm-025', name: 'Diego Morales', email: 'diego.morales@northwind.example', subject: 'sub-f017-diego', role: 'viewer', status: 'active', department: 'Sales', lastActive: '2026-10-09T10:52:00Z', revision: 1 },
  { id: 'm-026', name: 'Aisha Bello', email: 'aisha.bello@northwind.example', subject: 'sub-3abb-aisha', role: 'viewer', status: 'active', department: 'Legal', lastActive: '2026-10-14T08:03:00Z', revision: 1 },
]

export const DEPARTMENTS = [
  'Executive',
  'People & Culture',
  'Finance',
  'Engineering',
  'Sales',
  'Operations',
  'Legal',
]

/* ------------------------------------------------------------------------- */
/* Resource catalog                                                           */
/* ------------------------------------------------------------------------- */

export type ResourceKind = 'document' | 'database'
export type Classification = 'public' | 'internal' | 'confidential' | 'highly_restricted'
export type ResourceStatus = 'active' | 'stale' | 'disabled'

export type Resource = {
  id: string
  name: string
  kind: ResourceKind
  classification: Classification
  department: string
  owner: string
  source: string
  updatedAt: string
  status: ResourceStatus
  records: number
  sizeLabel: string
  tags: string[]
}

export type MetadataEntry = {
  key: string
  label: string
  value: string
}

export const CLASSIFICATION_LABELS: Record<Classification, string> = {
  public: 'Public',
  internal: 'Internal',
  confidential: 'Confidential',
  highly_restricted: 'Highly restricted',
}

export const STATUS_LABELS: Record<ResourceStatus, string> = {
  active: 'Active',
  stale: 'Stale',
  disabled: 'Disabled',
}

/** Minimal deterministic PRNG (LCG). Fixed seed: same catalog on every load. */
function makeRandom(seed: number): () => number {
  let state = seed >>> 0
  return () => {
    state = (state * 1664525 + 1013904223) >>> 0
    return state / 4294967296
  }
}

const DOC_SUBJECTS = [
  'Payroll register', 'Warehouse safety procedures', 'CRM account export',
  'Supplier master data', 'Employee handbook', 'Quarterly financial close pack',
  'Customer support playbook', 'Vendor risk assessment', 'Product roadmap',
  'ISO 27001 evidence pack', 'Freight cost analysis', 'Onboarding checklist',
  'Security incident runbooks', 'Data retention schedule', 'Sales pipeline snapshot',
  'Field service reports', 'Procurement contracts', 'Marketing campaign results',
  'IT asset inventory', 'Regulatory filings', 'Inventory reconciliation',
  'Benefits enrolment guide', 'Network architecture diagrams', 'Disaster recovery plan',
  'Quality audit findings', 'Pricing approval matrix', 'Travel expense policy',
  'Equipment maintenance log', 'Training completion records', 'Access review evidence',
]

const SCOPES = [
  'Northwest region', 'EMEA', 'APAC', 'Americas', 'Global', 'Q3 2026', 'FY2025',
  'approved final', 'working draft', 'archived 2024', 'post-audit revision',
  'with commentary', 'consolidated view', 'customer-visible extract',
]

const DOC_EXTENSIONS = ['.pdf', '.docx', '.xlsx', '.pptx']
const DB_SUBJECTS = [
  'analytics warehouse', 'operations ledger', 'customer billing records',
  'payroll transactions', 'service event log', 'inventory movements',
  'sales order history', 'support ticket archive', 'procurement approvals',
  'employee directory snapshot', 'subscription usage metrics', 'freight shipments',
  'quality inspection records',
]
const DB_SCHEMAS = ['public', 'reporting', 'core', 'analytics', 'staging']

const OWNER_POOL = [
  'Ada Evans', 'Priya Raman', 'Tomasz Kowalski', 'Sofia Marchetti',
  'Daniel Okafor', 'Helena Voss', 'Yuki Tanaka', 'Amara Diallo',
  'Ingrid Larsen', 'Rafael Ortiz', 'Naomi Feldman', 'Elena Petrova',
]
const TAG_POOL = [
  'finance', 'hr', 'pii', 'retention-7y', 'board', 'candidate', 'regulated',
  'operations', 'review-required', 'customer-data', 'audit', 'legacy',
]

const CLASSIFICATIONS: Classification[] = [
  'public', 'internal', 'confidential', 'highly_restricted',
]
const STATUSES: ResourceStatus[] = ['active', 'active', 'active', 'stale', 'disabled']

function pick<T>(rng: () => number, items: T[]): T {
  return items[Math.floor(rng() * items.length)]
}

function isoDaysAgo(baseIso: string, days: number, minutes: number): string {
  const base = new Date(baseIso).getTime()
  return new Date(base - days * 86_400_000 - minutes * 60_000).toISOString()
}

/** Fixed anchor so generated timestamps and screenshots stay stable. */
export const CATALOG_ANCHOR = '2026-10-15T09:00:00Z'

export function generateResources(count = 168): Resource[] {
  const rng = makeRandom(20261015)
  const resources: Resource[] = []
  for (let index = 0; index < count; index += 1) {
    const isDatabase = index % 5 === 2 // roughly one database per five resources
    const department = pick(rng, DEPARTMENTS)
    const classification = pick(rng, CLASSIFICATIONS)
    const status = pick(rng, STATUSES)
    const updatedAt = isoDaysAgo(
      CATALOG_ANCHOR,
      Math.floor(rng() * 320),
      Math.floor(rng() * 1440),
    )
    let name: string
    let source: string
    let records: number
    let sizeLabel: string
    if (isDatabase) {
      const subject = pick(rng, DB_SUBJECTS)
      const schema = pick(rng, DB_SCHEMAS)
      name = `PostgreSQL · ${subject} (${schema})`
      source = `PostgreSQL database · ${schema} schema`
      records = 400 + Math.floor(rng() * 240_000)
      sizeLabel = `${(records / 1000).toFixed(1)}k rows`
    } else {
      const subject = pick(rng, DOC_SUBJECTS)
      const scope = pick(rng, SCOPES)
      const extension = pick(rng, DOC_EXTENSIONS)
      const longForm = rng() > 0.75
      name = longForm
        ? `${subject} — ${scope} (consolidated ${String(Math.floor(rng() * 9) + 1)}-part review${rng() > 0.5 ? ', includes legal annotations and retention notes' : ''})${extension}`
        : `${subject} — ${scope}${extension}`
      source = 'Workspace documents'
      records = 1 + Math.floor(rng() * 480)
      sizeLabel = `${(records * (0.4 + rng())).toFixed(1)} MB`
    }
    const tagCount = Math.floor(rng() * 3)
    const tags: string[] = []
    for (let t = 0; t < tagCount; t += 1) {
      const tag = pick(rng, TAG_POOL)
      if (!tags.includes(tag)) tags.push(tag)
    }
    resources.push({
      id: `res-${String(index + 1).padStart(4, '0')}`,
      name,
      kind: isDatabase ? 'database' : 'document',
      classification,
      department,
      owner: pick(rng, OWNER_POOL),
      source,
      updatedAt,
      status,
      records,
      sizeLabel,
      tags,
    })
  }
  return resources
}

export const METADATA_KEYS: { key: string; label: string; value: (resource: Resource, rng: () => number) => string }[] = [
  { key: 'owner_team', label: 'Owner team', value: (r) => r.owner },
  { key: 'retention', label: 'Retention schedule', value: (_, rng) => pick(rng, ['3 years', '5 years', '7 years', 'Indefinite (legal hold)']) },
  { key: 'last_reviewed', label: 'Last reviewed', value: (r) => r.updatedAt.slice(0, 10) },
  { key: 'steward', label: 'Data steward', value: (r) => r.owner.split(' ')[0] + ' ' + r.owner.split(' ')[1].slice(0, 1) + '.' },
  { key: 'row_estimate', label: 'Row / entry estimate', value: (r) => String(r.records) },
  { key: 'language', label: 'Primary language', value: (_, rng) => pick(rng, ['English', 'English (UK)', 'English, Spanish excerpts', 'English, French excerpts']) },
  { key: 'source_system', label: 'Source system', value: (r) => r.source },
  { key: 'sync_window', label: 'Sync window', value: (_, rng) => pick(rng, ['Nightly 02:00 UTC', 'Hourly', 'Manual review', 'Weekly Sunday']) },
  { key: 'access_requests', label: 'Access requests (30d)', value: (_, rng) => String(Math.floor(rng() * 14)) },
  { key: 'incident_link', label: 'Related incident', value: (_, rng) => (rng() > 0.85 ? `INC-${2000 + Math.floor(rng() * 800)}` : 'None') },
  { key: 'upstream_schema', label: 'Upstream schema', value: (r) => (r.kind === 'database' ? pick(rngOf(r), DB_SCHEMAS) : 'n/a — uploaded document set') },
]

function rngOf(resource: Resource): () => number {
  // Deterministic per-resource stream derived from its id.
  return makeRandom(Number(resource.id.replace('res-', '')) * 7919)
}

export function metadataFor(resource: Resource): MetadataEntry[] {
  const rng = rngOf(resource)
  const count = 7 + Math.floor(rng() * 6) // 7-12 entries; total across catalog > 1000
  const entries: MetadataEntry[] = []
  const used = new Set<string>()
  let guard = 0
  while (entries.length < count && guard < 40) {
    guard += 1
    const spec = pick(rng, METADATA_KEYS)
    if (used.has(spec.key)) continue
    used.add(spec.key)
    entries.push({ key: spec.key, label: spec.label, value: spec.value(resource, rng) })
  }
  return entries
}

/** Total metadata rows across the whole catalog (asserted in tests, shown in evidence). */
export function catalogMetadataTotal(resources: Resource[]): number {
  return resources.reduce((sum, resource) => sum + metadataFor(resource).length, 0)
}

export function memberById(members: Member[], id: string): Member | undefined {
  return members.find((member) => member.id === id)
}
