/**
 * OPS1 OpenJM Control Center (platform operator plane).
 *
 * A distinct operator surface for Plane 1. It is never mixed into Client
 * Administration: a tenant owner/admin holds no platform capability, so the
 * backend refuses every route here for them, and the navigation entry is driven
 * by the server-resolved `platform_capabilities`, never by a tenant role.
 *
 * The browser never receives the monitoring credential used by the machine
 * monitoring boundary. Every panel calls a capability-gated platform API that
 * reuses the same internal readiness, usage and audit implementations
 * server-side.
 *
 * Summary first, drill-down second. No raw JSON dumps: each section renders a
 * useful operator presentation, and every payload is already projected
 * server-side (no content, no prompts, no secrets).
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Activity,
  AlertTriangle,
  Building2,
  FileClock,
  Gauge,
  Layers,
  LifeBuoy,
  ShieldCheck,
  Siren,
} from 'lucide-react'

import {
  ApiError,
  api,
  type PlatformAuditEvent,
  type PlatformAuditPage,
  type PlatformAuditSummary,
  type PlatformDiagnostics,
  type PlatformFleet,
  type PlatformOverview,
  type PlatformReleaseView,
  type PlatformSecurity,
  type PlatformTenantUsage,
} from './api'

type OpsTab =
  | 'overview'
  | 'tenants'
  | 'usage'
  | 'audit'
  | 'diagnostics'
  | 'security'
  | 'releases'

const TABS: { id: OpsTab; label: string; icon: typeof Gauge }[] = [
  { id: 'overview', label: 'Overview', icon: Gauge },
  { id: 'tenants', label: 'Tenants', icon: Building2 },
  { id: 'usage', label: 'Usage', icon: Activity },
  { id: 'audit', label: 'Audit', icon: FileClock },
  { id: 'diagnostics', label: 'Diagnostics', icon: Siren },
  { id: 'security', label: 'Security', icon: ShieldCheck },
  { id: 'releases', label: 'Releases', icon: Layers },
]

const STATUS_CLASS: Record<string, string> = {
  ok: 'status-ok',
  healthy: 'status-ok',
  warning: 'status-warning',
  degraded: 'status-warning',
  error: 'status-error',
  disabled: 'status-disabled',
  unavailable: 'status-disabled',
}

function StatusDot({ status }: { status?: string | null }) {
  const key = (status || 'unknown').toLowerCase()
  return (
    <span
      className={`status-dot ${STATUS_CLASS[key] || 'status-disabled'}`}
      aria-hidden="true"
    />
  )
}

function StatusLabel({ status }: { status?: string | null }) {
  return (
    <span className="ops-status">
      <StatusDot status={status} />
      {status || 'unknown'}
    </span>
  )
}

function humanise(value: string): string {
  return value.replace(/_/g, ' ')
}

function when(value?: string | null): string {
  if (!value) return 'n/a'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString()
}

function count(value?: unknown): string {
  if (typeof value === 'number' && Number.isFinite(value))
    return value.toLocaleString()
  return '0'
}

export default function OperatorConsole() {
  const [tab, setTab] = useState<OpsTab>('overview')
  const sequence = useRef(0)
  const [updatedAt, setUpdatedAt] = useState<Partial<Record<OpsTab, string>>>(
    {},
  )
  const [fleetOffset, setFleetOffset] = useState(0)
  const [usageOffset, setUsageOffset] = useState(0)
  const [auditOffset, setAuditOffset] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(true)

  const [overview, setOverview] = useState<PlatformOverview | null>(null)
  const [fleet, setFleet] = useState<PlatformFleet | null>(null)
  const [usage, setUsage] = useState<PlatformTenantUsage[]>([])
  const [audit, setAudit] = useState<PlatformAuditPage | null>(null)
  const [auditSummary, setAuditSummary] = useState<PlatformAuditSummary | null>(
    null,
  )
  const [auditFamily, setAuditFamily] = useState('')
  const [auditDecision, setAuditDecision] = useState('')
  const [selectedEvent, setSelectedEvent] = useState<PlatformAuditEvent | null>(
    null,
  )
  const [diagnostics, setDiagnostics] = useState<PlatformDiagnostics | null>(
    null,
  )
  const [correlation, setCorrelation] = useState('')
  const [security, setSecurity] = useState<PlatformSecurity | null>(null)
  const [releases, setReleases] = useState<PlatformReleaseView | null>(null)

  const fail = useCallback((exc: unknown) => {
    setError(
      exc instanceof ApiError
        ? exc.message
        : 'The control center request failed.',
    )
  }, [])

  const loadTab = useCallback(
    async (next: OpsTab) => {
      const request = ++sequence.current
      setBusy(true)
      setError(null)
      try {
        // Apply only the latest request. Slow responses must not overwrite newer
        // filters or clear another tab's loading/error state.
        let commit: () => void = () => undefined
        if (next === 'overview') {
          const data = await api.platformOverview()
          commit = () => setOverview(data)
        }
        if (next === 'tenants') {
          const data = await api.platformFleet(fleetOffset)
          commit = () => setFleet(data)
        }
        if (next === 'usage') {
          const data = await api.platformUsage(usageOffset)
          commit = () => setUsage(data.tenants ?? [])
        }
        if (next === 'audit') {
          const [page, summary] = await Promise.all([
            api.platformAudit({
              family: auditFamily || undefined,
              decision: auditDecision || undefined,
              limit: 25,
              offset: auditOffset,
            }),
            api.platformAuditSummary(),
          ])
          commit = () => {
            setAudit(page)
            setAuditSummary(summary)
          }
        }
        if (next === 'diagnostics') {
          const data = await api.platformDiagnostics({
            correlation_id: correlation || undefined,
            limit: 25,
          })
          commit = () => setDiagnostics(data)
        }
        if (next === 'security') {
          const data = await api.platformSecurity()
          commit = () => setSecurity(data)
        }
        if (next === 'releases') {
          const data = await api.platformReleases()
          commit = () => setReleases(data)
        }
        if (request === sequence.current) {
          commit()
          setUpdatedAt((current) => ({
            ...current,
            [next]: new Date().toLocaleTimeString(),
          }))
        }
      } catch (exc) {
        if (request === sequence.current) fail(exc)
      } finally {
        if (request === sequence.current) setBusy(false)
      }
    },
    [
      auditDecision,
      auditFamily,
      auditOffset,
      correlation,
      fail,
      fleetOffset,
      usageOffset,
    ],
  )

  useEffect(() => {
    void loadTab(tab)
    return () => {
      sequence.current += 1
    }
  }, [tab, loadTab])

  return (
    <section className="ops-surface" aria-label="OpenJM Control Center">
      <header className="workspace-header">
        <div>
          <div className="eyebrow">OpenJM platform operations</div>
          <h1>Control Center</h1>
        </div>
        <span className="system-badge">
          <ShieldCheck size={14} />
          Platform operator
        </span>
        <button type="button" disabled={busy} onClick={() => void loadTab(tab)}>
          Refresh view
        </button>
      </header>

      <nav className="admin-tabs" aria-label="Control center sections">
        {TABS.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            className={tab === id ? 'nav-item active' : 'nav-item'}
            aria-current={tab === id ? 'page' : undefined}
            onClick={() => setTab(id)}
          >
            <Icon size={15} />
            {label}
          </button>
        ))}
      </nav>

      <p className="admin-help" role="status">
        {busy
          ? 'Updating view…'
          : updatedAt[tab]
            ? `Last successful refresh: ${updatedAt[tab]}. Use Refresh view for current status.`
            : 'This view has not loaded successfully.'}
      </p>
      {error && (
        <p className="auth-error" role="alert">
          {error}
        </p>
      )}

      {tab === 'overview' && <OverviewPanel data={overview} busy={busy} />}
      {tab === 'tenants' && (
        <>
          <TenantsPanel data={fleet} busy={busy} />
          <PageControls
            offset={fleetOffset}
            limit={50}
            hasMore={fleet?.has_more ?? false}
            busy={busy}
            onPage={setFleetOffset}
            label="Tenant fleet"
          />
        </>
      )}
      {tab === 'usage' && (
        <>
          <UsagePanel tenants={usage} busy={busy} />
          <PageControls
            offset={usageOffset}
            limit={50}
            hasMore={usage.length === 50}
            busy={busy}
            onPage={setUsageOffset}
            label="Usage"
          />
        </>
      )}
      {tab === 'audit' && (
        <>
          <AuditPanel
            page={audit}
            summary={auditSummary}
            family={auditFamily}
            decision={auditDecision}
            onFamily={(value) => {
              setAuditFamily(value)
              setAuditOffset(0)
              setSelectedEvent(null)
            }}
            onDecision={(value) => {
              setAuditDecision(value)
              setAuditOffset(0)
              setSelectedEvent(null)
            }}
            onSelect={setSelectedEvent}
            selected={selectedEvent}
          />
          <PageControls
            offset={auditOffset}
            limit={25}
            hasMore={audit?.has_more ?? false}
            busy={busy}
            onPage={(offset) => {
              setAuditOffset(offset)
              setSelectedEvent(null)
            }}
            label="Audit"
          />
        </>
      )}
      {tab === 'diagnostics' && (
        <DiagnosticsPanel
          data={diagnostics}
          correlation={correlation}
          onCorrelation={setCorrelation}
        />
      )}
      {tab === 'security' && <SecurityPanel data={security} busy={busy} />}
      {tab === 'releases' && <ReleasesPanel data={releases} busy={busy} />}
    </section>
  )
}

// ---------------------------------------------------------------------------
// Overview
// ---------------------------------------------------------------------------

function OverviewPanel({
  data,
  busy,
}: {
  data: PlatformOverview | null
  busy: boolean
}) {
  if (!data)
    return (
      <p className="admin-help">
        {busy ? 'Loading overview…' : 'No overview available.'}
      </p>
    )
  const release = data.release as Record<string, string | null>
  return (
    <>
      <div className="ops-cards">
        <div className="ops-card">
          <div className="ops-card-label">Core readiness</div>
          <div className="ops-card-value">
            <StatusLabel status={data.ready ? 'healthy' : 'error'} />
          </div>
          <p className="admin-help">
            {data.components.database?.status === 'ok'
              ? 'Database reachable. '
              : ''}
            {data.components.migrations?.drift
              ? 'Schema drift detected.'
              : 'Schema at head.'}
          </p>
        </div>
        <div className="ops-card">
          <div className="ops-card-label">Release</div>
          <div className="ops-card-value">{release.version || 'unknown'}</div>
          <p className="admin-help">
            {release.release_train || 'n/a'} · {release.release_id || 'n/a'} ·{' '}
            {data.profile}
          </p>
        </div>
        <div className="ops-card">
          <div className="ops-card-label">Model</div>
          <div className="ops-card-value">{data.model.model_name}</div>
          <p className="admin-help">
            {data.model.provider_mode} · fallback{' '}
            {humanise(data.model.fallback_policy || 'n/a')}
          </p>
        </div>
        <div className="ops-card">
          <div className="ops-card-label">Tenants</div>
          <div className="ops-card-value">{count(data.fleet.tenants)}</div>
          <p className="admin-help">
            {count(data.fleet.suspended)} suspended ·{' '}
            {count(data.fleet.operators)} operators
          </p>
        </div>
      </div>

      <div className="admin-section">
        <h2>
          <Siren size={16} /> Findings
        </h2>
        {data.alerts.length === 0 ? (
          <p className="admin-help">
            No operator findings. All reported components are healthy.
          </p>
        ) : (
          <ul className="admin-list">
            {data.alerts.map((alert) => (
              <li key={alert.code}>
                <AlertTriangle size={14} />{' '}
                <strong>{humanise(alert.code)}</strong> · {alert.severity} ·{' '}
                {alert.detail}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="admin-section">
        <h2>
          <LifeBuoy size={16} /> Components
        </h2>
        <table className="admin-table">
          <thead>
            <tr>
              <th scope="col">Component</th>
              <th scope="col">Status</th>
              <th scope="col">Detail</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(data.components).map(([name, component]) => (
              <tr key={name}>
                <td>{humanise(name)}</td>
                <td>
                  <StatusLabel status={component.status} />
                </td>
                <td>{describeComponent(component)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

function describeComponent(component: Record<string, unknown>): string {
  const parts: string[] = []
  if (typeof component.backend === 'string') parts.push(component.backend)
  if (typeof component.revision === 'string')
    parts.push(`rev ${component.revision}`)
  if (typeof component.head === 'string') parts.push(`head ${component.head}`)
  if (typeof component.free_mb === 'number')
    parts.push(`${component.free_mb} MB free`)
  if (typeof component.active_schedules === 'number')
    parts.push(`${component.active_schedules} schedules`)
  if (typeof component.instances === 'number')
    parts.push(`${component.instances} connectors`)
  if (typeof component.pending === 'number')
    parts.push(`${component.pending} pending`)
  if (typeof component.latency_ms === 'number')
    parts.push(`${component.latency_ms} ms`)
  if (typeof component.detail === 'string') parts.push(component.detail)
  return parts.join(' · ') || '—'
}

// ---------------------------------------------------------------------------
// Tenants
// ---------------------------------------------------------------------------

function TenantsPanel({
  data,
  busy,
}: {
  data: PlatformFleet | null
  busy: boolean
}) {
  const [needle, setNeedle] = useState('')
  if (!data)
    return (
      <p className="admin-help">
        {busy ? 'Loading fleet…' : 'No fleet available.'}
      </p>
    )
  const filtered = data.tenants.filter((tenant) =>
    `${tenant.name} ${tenant.slug} ${tenant.status}`
      .toLowerCase()
      .includes(needle.trim().toLowerCase()),
  )
  return (
    <div className="admin-section">
      <h2>
        <Building2 size={16} /> Tenant fleet ({count(data.total)})
      </h2>
      <label className="admin-form">
        <span>Search this page</span>
        <input
          type="search"
          value={needle}
          placeholder="Name, slug or status"
          onChange={(event) => setNeedle(event.target.value)}
        />
      </label>
      <table className="admin-table">
        <thead>
          <tr>
            <th scope="col">Tenant</th>
            <th scope="col">Slug</th>
            <th scope="col">Status</th>
            <th scope="col">Requests</th>
            <th scope="col">Total tokens</th>
          </tr>
        </thead>
        <tbody>
          {filtered.map((tenant) => (
            <tr key={tenant.id}>
              <td>{tenant.name}</td>
              <td>{tenant.slug}</td>
              <td>
                <StatusLabel status={tenant.status} />
              </td>
              <td>{count(tenant.usage.requests)}</td>
              <td>{count(tenant.usage.total_tokens)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {filtered.length === 0 && (
        <p className="admin-help">No tenant matches that search.</p>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Usage
// ---------------------------------------------------------------------------

function UsagePanel({
  tenants,
  busy,
}: {
  tenants: PlatformTenantUsage[]
  busy: boolean
}) {
  if (!busy && tenants.length === 0) {
    return <p className="admin-help">No aggregate usage recorded yet.</p>
  }
  return (
    <div className="admin-section">
      <h2>
        <Activity size={16} /> Cross-tenant usage
      </h2>
      <p className="admin-help">
        Aggregate metering only. No prompt, message or response content is
        available here.
      </p>
      <table className="admin-table">
        <thead>
          <tr>
            <th scope="col">Tenant</th>
            <th scope="col">Requests</th>
            <th scope="col">Input tokens</th>
            <th scope="col">Output tokens</th>
            <th scope="col">Total tokens</th>
          </tr>
        </thead>
        <tbody>
          {tenants.map((row) => (
            <tr key={String(row.tenant_id ?? row.slug ?? 'unknown')}>
              <td>{String(row.tenant_id ?? 'n/a')}</td>
              <td>{count(row.requests)}</td>
              <td>{count(row.input_tokens)}</td>
              <td>{count(row.output_tokens)}</td>
              <td>{count(row.total_tokens)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Audit
// ---------------------------------------------------------------------------

const AUDIT_FAMILIES = [
  'authorize',
  'auth',
  'platform',
  'governance',
  'connector',
  'knowledge',
  'notification',
  'inference',
  'report',
  'action',
]

function AuditPanel({
  page,
  summary,
  family,
  decision,
  onFamily,
  onDecision,
  onSelect,
  selected,
}: {
  page: PlatformAuditPage | null
  summary: PlatformAuditSummary | null
  family: string
  decision: string
  onFamily: (value: string) => void
  onDecision: (value: string) => void
  onSelect: (event: PlatformAuditEvent | null) => void
  selected: PlatformAuditEvent | null
}) {
  return (
    <>
      {summary && (
        <div className="ops-cards">
          <div className="ops-card">
            <div className="ops-card-label">Audit records</div>
            <div className="ops-card-value">{count(summary.total)}</div>
            <p className="admin-help">
              Persisted governance and security trail.
            </p>
          </div>
          <div className="ops-card">
            <div className="ops-card-label">Denied decisions</div>
            <div className="ops-card-value">{count(summary.denied)}</div>
            <p className="admin-help">
              Authorization decisions recorded as deny.
            </p>
          </div>
        </div>
      )}

      <div className="admin-section">
        <h2>
          <FileClock size={16} /> Audit explorer
        </h2>
        <div className="admin-form ops-filters">
          <label>
            <span>Family</span>
            <select
              value={family}
              onChange={(event) => onFamily(event.target.value)}
            >
              <option value="">All families</option>
              {AUDIT_FAMILIES.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Decision</span>
            <select
              value={decision}
              onChange={(event) => onDecision(event.target.value)}
            >
              <option value="">All decisions</option>
              <option value="allow">allow</option>
              <option value="deny">deny</option>
            </select>
          </label>
        </div>

        {!page ? (
          <p className="admin-help">Loading audit records…</p>
        ) : page.events.length === 0 ? (
          <p className="admin-help">No audit records match those filters.</p>
        ) : (
          <>
            <table className="admin-table">
              <thead>
                <tr>
                  <th scope="col">Time</th>
                  <th scope="col">Action</th>
                  <th scope="col">Decision</th>
                  <th scope="col">Tenant</th>
                  <th scope="col">Principal</th>
                </tr>
              </thead>
              <tbody>
                {page.events.map((event) => (
                  <tr
                    key={event.id}
                    className={
                      selected?.id === event.id ? 'selected' : undefined
                    }
                  >
                    <td>
                      <button
                        className="link-button"
                        onClick={() => onSelect(event)}
                      >
                        {when(event.created_at)}
                      </button>
                    </td>
                    <td>{event.action}</td>
                    <td>
                      <StatusLabel
                        status={event.decision === 'deny' ? 'error' : 'ok'}
                      />{' '}
                      {event.decision}
                    </td>
                    <td>{event.tenant_id}</td>
                    <td>{event.principal_id || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="admin-note">
              Showing {page.events.length} of {count(page.total)} records (page
              size capped server-side at {page.limit}).
            </p>
          </>
        )}

        {selected && (
          <div className="admin-subsection">
            <h3>Record {selected.id}</h3>
            <dl className="admin-facts">
              <div>
                <dt>Action</dt>
                <dd>{selected.action}</dd>
              </div>
              <div>
                <dt>Family</dt>
                <dd>{selected.family}</dd>
              </div>
              <div>
                <dt>Resource</dt>
                <dd>
                  {selected.resource_type || '—'} {selected.resource_id || ''}
                </dd>
              </div>
              <div>
                <dt>Reason</dt>
                <dd>{selected.reason || '—'}</dd>
              </div>
              {Object.entries(selected.metadata).map(([key, value]) => (
                <div key={key}>
                  <dt>{humanise(key)}</dt>
                  <dd>{value}</dd>
                </div>
              ))}
            </dl>
          </div>
        )}
      </div>
    </>
  )
}

// ---------------------------------------------------------------------------
// Diagnostics
// ---------------------------------------------------------------------------

function DiagnosticsPanel({
  data,
  correlation,
  onCorrelation,
}: {
  data: PlatformDiagnostics | null
  correlation: string
  onCorrelation: (value: string) => void
}) {
  if (!data) return <p className="admin-help">Loading diagnostics…</p>
  return (
    <>
      <div className="admin-section">
        <h2>
          <Siren size={16} /> Diagnostics
        </h2>
        <p className="admin-help">
          Runtime telemetry and safe metadata. Prompt, message and document
          bodies are never available here, and neither is raw SQL.
        </p>
        <label className="admin-form">
          <span>Follow a correlation id</span>
          <input
            type="search"
            value={correlation}
            placeholder="Request / correlation id"
            onChange={(event) => onCorrelation(event.target.value)}
          />
        </label>

        <h3>Recent execution traces ({count(data.traces_total)})</h3>
        {data.traces.length === 0 ? (
          <p className="admin-help">No execution traces match.</p>
        ) : (
          <table className="admin-table">
            <thead>
              <tr>
                <th scope="col">Time</th>
                <th scope="col">Tool</th>
                <th scope="col">Status</th>
                <th scope="col">Duration</th>
                <th scope="col">Failure</th>
                <th scope="col">Correlation</th>
              </tr>
            </thead>
            <tbody>
              {data.traces.map((trace) => (
                <tr key={trace.id}>
                  <td>{when(trace.created_at)}</td>
                  <td>{trace.tool_name}</td>
                  <td>
                    <StatusLabel
                      status={trace.status === 'failed' ? 'error' : 'ok'}
                    />{' '}
                    {trace.status}
                  </td>
                  <td>
                    {trace.duration_ms !== null &&
                    trace.duration_ms !== undefined
                      ? `${trace.duration_ms} ms`
                      : '—'}
                  </td>
                  <td>{trace.error_class || '—'}</td>
                  <td>
                    <button
                      className="link-button"
                      onClick={() => onCorrelation(trace.request_id)}
                    >
                      {trace.request_id.slice(0, 8)}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        <h3>Failure categories</h3>
        {data.failure_categories.length === 0 ? (
          <p className="admin-help">No recorded failure categories.</p>
        ) : (
          <ul className="admin-list">
            {data.failure_categories.map((row) => (
              <li key={`${row.tool_name}-${row.error_class}`}>
                <strong>{row.tool_name}</strong> · {row.error_class} ·{' '}
                {count(row.count)}
              </li>
            ))}
          </ul>
        )}

        <div className="admin-note">
          <strong>Operational logs.</strong> {data.operational_logs.guidance}{' '}
          (persistence: {data.operational_logs.persistence}, format:{' '}
          {data.operational_logs.format}, aggregated into the metadata database:{' '}
          {data.operational_logs.aggregated_into_metadata_db ? 'yes' : 'no'}.)
        </div>
      </div>
    </>
  )
}

// ---------------------------------------------------------------------------
// Security
// ---------------------------------------------------------------------------

function SecurityPanel({
  data,
  busy,
}: {
  data: PlatformSecurity | null
  busy: boolean
}) {
  if (!data)
    return (
      <p className="admin-help">
        {busy ? 'Loading security view…' : 'No security data.'}
      </p>
    )
  return (
    <>
      <div className="ops-cards">
        <div className="ops-card">
          <div className="ops-card-label">Platform operators</div>
          <div className="ops-card-value">{count(data.operator_count)}</div>
          <p className="admin-help">
            Explicit capability grants. Never derived from a tenant role.
          </p>
        </div>
        <div className="ops-card">
          <div className="ops-card-label">Active content delegations</div>
          <div className="ops-card-value">
            {count(data.active_content_delegations)}
          </div>
          <p className="admin-help">
            {count(data.expiring_delegations)} expiring within 7 days.
          </p>
        </div>
        <div className="ops-card">
          <div className="ops-card-label">Suspended tenants</div>
          <div className="ops-card-value">{count(data.suspended_tenants)}</div>
        </div>
      </div>

      <div className="admin-section">
        <h2>
          <ShieldCheck size={16} /> Operator capabilities
        </h2>
        {data.operators.length === 0 ? (
          <p className="admin-help">No platform operators granted.</p>
        ) : (
          <table className="admin-table">
            <thead>
              <tr>
                <th scope="col">Principal</th>
                <th scope="col">Capability</th>
                <th scope="col">Status</th>
                <th scope="col">Expires</th>
              </tr>
            </thead>
            <tbody>
              {data.operators.map((operator) => (
                <tr key={`${operator.principal_id}-${operator.capability}`}>
                  <td>{operator.principal_id}</td>
                  <td>{operator.capability}</td>
                  <td>
                    <StatusLabel status={operator.status} />
                  </td>
                  <td>
                    {operator.expires_at ? when(operator.expires_at) : 'none'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="admin-section">
        <h2>
          <LifeBuoy size={16} /> Support delegations
        </h2>
        {data.support_delegations.length === 0 ? (
          <p className="admin-help">No support delegations recorded.</p>
        ) : (
          <table className="admin-table">
            <thead>
              <tr>
                <th scope="col">Tenant</th>
                <th scope="col">Principal</th>
                <th scope="col">Scope</th>
                <th scope="col">Effective</th>
                <th scope="col">Expires</th>
              </tr>
            </thead>
            <tbody>
              {data.support_delegations.map((row) => (
                <tr key={`${row.tenant_id}-${row.principal_id}-${row.scope}`}>
                  <td>{row.tenant_id}</td>
                  <td>{row.principal_id}</td>
                  <td>{row.scope}</td>
                  <td>
                    <StatusLabel
                      status={row.effective ? 'active' : 'disabled'}
                    />
                  </td>
                  <td>{row.expires_at ? when(row.expires_at) : 'none'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="admin-section">
        <h2>
          <AlertTriangle size={16} /> Recent privileged actions
        </h2>
        {data.recent_privileged_actions.length === 0 ? (
          <p className="admin-help">No recent privileged actions.</p>
        ) : (
          <ul className="admin-list">
            {data.recent_privileged_actions.map((event) => (
              <li key={event.id}>
                {when(event.created_at)} · <strong>{event.action}</strong> ·{' '}
                {event.decision} · {event.tenant_id}
              </li>
            ))}
          </ul>
        )}

        <h3>Denied categories</h3>
        {data.denied_categories.length === 0 ? (
          <p className="admin-help">No denied decisions recorded.</p>
        ) : (
          <ul className="admin-list">
            {data.denied_categories.map((row) => (
              <li key={row.action}>
                <strong>{row.action}</strong> · {count(row.count)}
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  )
}

// ---------------------------------------------------------------------------
// Releases
// ---------------------------------------------------------------------------

function ReleasesPanel({
  data,
  busy,
}: {
  data: PlatformReleaseView | null
  busy: boolean
}) {
  if (!data)
    return (
      <p className="admin-help">
        {busy ? 'Loading releases…' : 'No release data.'}
      </p>
    )
  const release = data.release as Record<string, string | null>
  return (
    <>
      <div className="ops-cards">
        <div className="ops-card">
          <div className="ops-card-label">Version</div>
          <div className="ops-card-value">{release.version || 'unknown'}</div>
          <p className="admin-help">
            {release.release_train || 'n/a'} · {release.release_id || 'n/a'}
          </p>
        </div>
        <div className="ops-card">
          <div className="ops-card-label">Deployments</div>
          <div className="ops-card-value">{count(data.deployment_count)}</div>
          <p className="admin-help">
            Modelled as a list so a future fleet can extend this view.
          </p>
        </div>
      </div>

      <div className="admin-section">
        <h2>
          <Layers size={16} /> Deployment health
        </h2>
        <table className="admin-table">
          <thead>
            <tr>
              <th scope="col">Profile</th>
              <th scope="col">Migration</th>
              <th scope="col">Head</th>
              <th scope="col">Drift</th>
              <th scope="col">Model</th>
            </tr>
          </thead>
          <tbody>
            {data.deployments.map((deployment) => (
              <tr key={deployment.release_id || deployment.profile}>
                <td>{deployment.profile}</td>
                <td>{deployment.migration_revision || 'n/a'}</td>
                <td>{deployment.migration_head || 'n/a'}</td>
                <td>
                  <StatusLabel
                    status={deployment.migration_drift ? 'degraded' : 'ok'}
                  />
                </td>
                <td>{deployment.model_name}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="admin-note">
          A single deployment is shown because only one exists today. No
          synthetic fleet is rendered.
        </p>
      </div>
    </>
  )
}

function PageControls({
  offset,
  limit,
  hasMore,
  busy,
  onPage,
  label,
}: {
  offset: number
  limit: number
  hasMore: boolean
  busy: boolean
  onPage: (offset: number) => void
  label: string
}) {
  return (
    <nav className="admin-toolbar" aria-label={`${label} pages`}>
      <button
        type="button"
        disabled={busy || offset === 0}
        onClick={() => onPage(Math.max(0, offset - limit))}
      >
        Previous page
      </button>
      <span>Page {Math.floor(offset / limit) + 1}</span>
      <button
        type="button"
        disabled={busy || !hasMore}
        onClick={() => onPage(offset + limit)}
      >
        Next page
      </button>
    </nav>
  )
}
