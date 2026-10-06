/**
 * VS7 governed connector management surface.
 *
 * This is a management surface, not an authorization surface. It presents
 * connector state and submits operator actions; every decision is re-made by the
 * server. It never renders a credential value and holds a credential in
 * component state only between input and submit.
 */
import { FormEvent, useEffect, useState } from 'react'
import {
  Activity,
  Cable,
  CircleAlert,
  CircleCheck,
  KeyRound,
  Link2,
  Plug,
  Power,
  RefreshCw,
  ShieldCheck,
  Trash2,
} from 'lucide-react'
import {
  api,
  type ConnectorInstance,
  type ConnectorMapping,
  type ConnectorResource,
  type ConnectorRun,
  type ConnectorTypeSpec,
} from './api'

type ConfigRow = { key: string; value: string }

const SYNC_RUN_TYPES = ['incremental', 'initial', 'reconcile'] as const

function formatTimestamp(value?: string | null): string {
  if (!value) return 'Never'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString()
}

function rowsToConfig(rows: ConfigRow[]): Record<string, string> {
  const config: Record<string, string> = {}
  for (const row of rows) {
    const key = row.key.trim()
    if (key) config[key] = row.value
  }
  return config
}

function credentialPayload(
  fields: string[],
  values: Record<string, string>,
): Record<string, string> {
  const payload: Record<string, string> = {}
  for (const field of fields) {
    if (values[field]) payload[field] = values[field]
  }
  return payload
}

function healthClass(status: string | null | undefined): string {
  const normalised = (status || '').toLowerCase()
  if (['healthy', 'ok', 'connected', 'active'].includes(normalised)) return 'healthy'
  if (['degraded', 'warning', 'stale'].includes(normalised)) return 'degraded'
  if (['error', 'failed', 'unhealthy'].includes(normalised)) return 'error'
  return 'unknown'
}

function RunRow({ run }: { run: ConnectorRun }) {
  return (
    <tr>
      <td>{run.run_type}</td>
      <td>{run.status}</td>
      <td>{formatTimestamp(run.started_at)}</td>
      <td>{run.items_scanned}</td>
      <td>{run.items_created}</td>
      <td>{run.items_updated}</td>
      <td>{run.items_deleted}</td>
      <td>{run.items_quarantined}</td>
      <td>{run.items_skipped}</td>
      <td>{run.failure_category || '—'}</td>
    </tr>
  )
}

export default function ConnectorsPanel() {
  const [types, setTypes] = useState<ConnectorTypeSpec[]>([])
  const [connectors, setConnectors] = useState<ConnectorInstance[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [selected, setSelected] = useState<ConnectorInstance | null>(null)
  const [runs, setRuns] = useState<ConnectorRun[]>([])
  const [resources, setResources] = useState<ConnectorResource[]>([])
  const [mappings, setMappings] = useState<ConnectorMapping[]>([])
  const [detailBusy, setDetailBusy] = useState(false)
  const [busyAction, setBusyAction] = useState<string | null>(null)

  const [testResult, setTestResult] = useState<{ ok: boolean; detail: string } | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  // Create form. Credential values live only here, between input and submit.
  const [name, setName] = useState('')
  const [typeKey, setTypeKey] = useState('')
  const [configRows, setConfigRows] = useState<ConfigRow[]>([{ key: '', value: '' }])
  const [credentials, setCredentials] = useState<Record<string, string>>({})
  const [credentialLabel, setCredentialLabel] = useState('primary')
  const [creating, setCreating] = useState(false)

  // Sync controls
  const [syncRunType, setSyncRunType] = useState<string>('incremental')
  const [syncLimit, setSyncLimit] = useState('')

  // Credential rotation form
  const [rotation, setRotation] = useState<Record<string, string>>({})
  const [rotationLabel, setRotationLabel] = useState('rotated')
  const [rotating, setRotating] = useState(false)
  const [confirmRevoke, setConfirmRevoke] = useState(false)

  // Disconnect
  const [purge, setPurge] = useState(false)
  const [confirmDisconnect, setConfirmDisconnect] = useState(false)

  // New mapping form
  const [mappingPrincipal, setMappingPrincipal] = useState('')
  const [mappingExternal, setMappingExternal] = useState('')
  const [mappingBusy, setMappingBusy] = useState(false)

  const selectedType = types.find((item) => item.key === typeKey) || types[0] || null

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const [typeResult, connectorResult] = await Promise.all([
          api.connectorTypes(),
          api.connectors(),
        ])
        if (cancelled) return
        setTypes(typeResult.types)
        setConnectors(connectorResult.connectors)
        if (typeResult.types[0]) setTypeKey(typeResult.types[0].key)
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Unable to load connectors')
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [])

  const refreshList = async () => {
    const result = await api.connectors()
    setConnectors(result.connectors)
    return result.connectors
  }

  const loadDetail = async (id: string) => {
    const [detail, resourceResult, mappingResult] = await Promise.all([
      api.connector(id),
      api.connectorResources(id),
      api.connectorMappings(id),
    ])
    setSelected(detail)
    setRuns(detail.runs || [])
    setResources(resourceResult.resources)
    setMappings(mappingResult.mappings)
  }

  const openConnector = async (id: string) => {
    setSelectedId(id)
    setDetailBusy(true)
    setError(null)
    setTestResult(null)
    setNotice(null)
    setConfirmDisconnect(false)
    setConfirmRevoke(false)
    setRotation({})
    setRotationLabel('rotated')
    try {
      await loadDetail(id)
    } catch (err) {
      setSelected(null)
      setError(err instanceof Error ? err.message : 'Connector detail is unavailable')
    } finally {
      setDetailBusy(false)
    }
  }

  const createConnector = async (event: FormEvent) => {
    event.preventDefault()
    if (!selectedType || creating) return
    if (!name.trim()) {
      setError('A connector name is required')
      return
    }
    const fields = selectedType.credential_fields
    const credential = credentialPayload(fields, credentials)
    if (fields.length > 0 && Object.keys(credential).length === 0) {
      setError('A credential is required for this connector type')
      return
    }
    setCreating(true)
    setError(null)
    setNotice(null)
    try {
      const created = await api.createConnector({
        name: name.trim(),
        connector_type: selectedType.type_id,
        version: selectedType.version,
        config: rowsToConfig(configRows),
        credential: Object.keys(credential).length > 0 ? credential : undefined,
        credential_label: credentialLabel.trim() || 'primary',
      })
      setName('')
      setConfigRows([{ key: '', value: '' }])
      setCredentialLabel('primary')
      await refreshList()
      await openConnector(created.id)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to create connector')
    } finally {
      // The credential never outlives the submit.
      setCredentials({})
      setCreating(false)
    }
  }

  const testConnection = async () => {
    if (!selected) return
    setBusyAction('test')
    setTestResult(null)
    setError(null)
    try {
      const result = await api.testConnector(selected.id)
      setTestResult({ ok: result.ok, detail: result.detail })
      setSelected(result.connector)
      await refreshList()
    } catch (err) {
      setTestResult({
        ok: false,
        detail: err instanceof Error ? err.message : 'Test connection failed',
      })
    } finally {
      setBusyAction(null)
    }
  }

  const toggleEnabled = async (enabled: boolean) => {
    if (!selected) return
    setBusyAction(enabled ? 'enable' : 'disable')
    setError(null)
    try {
      const updated = await api.setConnectorEnabled(selected.id, enabled)
      setSelected(updated)
      await refreshList()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to change connector state')
    } finally {
      setBusyAction(null)
    }
  }

  const runSync = async () => {
    if (!selected) return
    const limit = syncLimit.trim() ? Number(syncLimit.trim()) : undefined
    setBusyAction('sync')
    setError(null)
    setNotice(null)
    try {
      const run = await api.syncConnector(selected.id, syncRunType, limit)
      setNotice(
        run.status === 'succeeded'
          ? `Sync run ${run.run_type} ${run.status}`
          : `Sync run ${run.run_type} ${run.status}${run.failure_category ? ` (${run.failure_category})` : ''}`,
      )
      await loadDetail(selected.id)
      await refreshList()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sync failed')
    } finally {
      setBusyAction(null)
    }
  }

  const disconnect = async () => {
    if (!selected) return
    setBusyAction('disconnect')
    setError(null)
    setConfirmDisconnect(false)
    try {
      const result = await api.disconnectConnector(selected.id, purge)
      setNotice(
        result.connector.enabled
          ? 'Disconnect reported no state change'
          : `Connector disconnected${result.quarantined ? `, ${result.quarantined} resource(s) quarantined` : ''}`,
      )
      setSelected(result.connector)
      await loadDetail(selected.id)
      await refreshList()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Disconnect failed')
    } finally {
      setBusyAction(null)
    }
  }

  const rotateCredential = async (event: FormEvent) => {
    event.preventDefault()
    if (!selected || !selectedType || rotating) return
    const fields = selectedType.credential_fields
    const credential = credentialPayload(fields, rotation)
    if (Object.keys(credential).length === 0) {
      setError('Enter the replacement credential before rotating')
      return
    }
    setRotating(true)
    setError(null)
    setNotice(null)
    try {
      const result = await api.rotateConnectorCredential(
        selected.id,
        credential,
        rotationLabel.trim() || undefined,
      )
      setSelected(result.connector)
      setNotice(`Credential rotated to version ${result.version}`)
      await refreshList()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Credential rotation failed')
    } finally {
      // The replacement value is discarded the moment the request is done.
      setRotation({})
      setRotating(false)
    }
  }

  const revokeCredential = async () => {
    if (!selected) return
    setBusyAction('revoke')
    setError(null)
    setConfirmRevoke(false)
    try {
      const result = await api.revokeConnectorCredential(selected.id)
      setSelected(result.connector)
      setNotice('Credential revoked')
      await refreshList()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Credential revocation failed')
    } finally {
      setBusyAction(null)
    }
  }

  const addMapping = async (event: FormEvent) => {
    event.preventDefault()
    if (!selected || mappingBusy) return
    if (!mappingPrincipal.trim() || !mappingExternal.trim()) {
      setError('Both the OpenJM principal and the external user id are required')
      return
    }
    setMappingBusy(true)
    setError(null)
    try {
      await api.createConnectorMapping(
        selected.id,
        mappingPrincipal.trim(),
        mappingExternal.trim(),
      )
      setMappingPrincipal('')
      setMappingExternal('')
      await loadDetail(selected.id)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to create the mapping')
    } finally {
      setMappingBusy(false)
    }
  }

  const revokeMapping = async (mappingId: string) => {
    if (!selected) return
    setError(null)
    try {
      await api.deleteConnectorMapping(selected.id, mappingId)
      await loadDetail(selected.id)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to revoke the mapping')
    }
  }

  const updateConfigRow = (index: number, patch: Partial<ConfigRow>) => {
    setConfigRows((current) =>
      current.map((row, position) => (position === index ? { ...row, ...patch } : row)),
    )
  }

  const latestRun = runs[0] || null

  return (
    <section className="manage-stage">
      <div className="manage-intro">
        <div className="eyebrow">Governed connectors</div>
        <h2>Connectors</h2>
        <p>
          Connect external systems under a closed, declared capability set. Test connection,
          synchronize, reconcile and manage user mappings here. Credentials are stored
          server-side, never returned, and never displayed by this screen.
        </p>
      </div>

      {error && <div className="error-banner data-error">{error}</div>}

      <div className="manage-grid">
        <div className="manage-column">
          <div className="manage-list-heading">
            <strong>Configured connectors</strong>
            <span>{connectors.length}</span>
          </div>

          {loading ? (
            <div className="manage-empty">Loading connectors…</div>
          ) : connectors.length === 0 ? (
            <div className="manage-empty">No connectors configured yet.</div>
          ) : (
            connectors.map((connector) => (
              <button
                type="button"
                key={connector.id}
                className={selectedId === connector.id ? 'manage-item selected' : 'manage-item'}
                disabled={detailBusy}
                onClick={() => openConnector(connector.id)}
              >
                <span className="manage-item-name">{connector.name}</span>
                <span className="manage-item-meta">
                  {connector.display_name || connector.connector_type} · v{connector.connector_version}
                </span>
                <span className="manage-item-row">
                  <span className={connector.enabled ? 'source-enabled' : 'source-disabled'}>
                    {connector.enabled ? 'Enabled' : 'Disabled'}
                  </span>
                  <span className={`connector-health ${healthClass(connector.health_status)}`}>
                    {connector.health_status || 'unknown'}
                  </span>
                </span>
                <span className="manage-item-meta">
                  Last successful sync: {formatTimestamp(connector.last_successful_sync_at)}
                </span>
                {connector.last_failure_category && (
                  <span className="manage-item-failure">
                    Last failure: {connector.last_failure_category}
                  </span>
                )}
              </button>
            ))
          )}

          <form className="manage-form" onSubmit={createConnector}>
            <div className="manage-list-heading">
              <strong>Add connector</strong>
            </div>
            <label>
              <span>Connector type</span>
              <select value={typeKey} onChange={(event) => setTypeKey(event.target.value)}>
                {types.map((type) => (
                  <option key={type.key} value={type.key}>
                    {type.display_name} v{type.version}
                  </option>
                ))}
              </select>
            </label>
            {selectedType && (
              <span className="field-note">
                {selectedType.description} · capability: {selectedType.capabilities.join(', ') || 'none'}
              </span>
            )}
            <label>
              <span>Name</span>
              <input
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="e.g. Workspace production"
              />
            </label>
            <div>
              <span className="field-label">Non-secret configuration</span>
              {configRows.map((row, index) => (
                <div className="kv-row" key={index}>
                  <input
                    aria-label={`Config key ${index + 1}`}
                    value={row.key}
                    onChange={(event) => updateConfigRow(index, { key: event.target.value })}
                    placeholder="key"
                  />
                  <input
                    aria-label={`Config value ${index + 1}`}
                    value={row.value}
                    onChange={(event) => updateConfigRow(index, { value: event.target.value })}
                    placeholder="value"
                  />
                </div>
              ))}
              <button
                type="button"
                className="secondary-action"
                onClick={() => setConfigRows((current) => [...current, { key: '', value: '' }])}
              >
                Add config field
              </button>
            </div>
            {selectedType?.credential_fields.map((field) => (
              <label key={field}>
                <span>{field}</span>
                <input
                  type="password"
                  autoComplete="new-password"
                  value={credentials[field] || ''}
                  onChange={(event) =>
                    setCredentials((current) => ({ ...current, [field]: event.target.value }))
                  }
                  placeholder={`Enter ${field}`}
                />
              </label>
            ))}
            <label>
              <span>Credential label</span>
              <input
                value={credentialLabel}
                onChange={(event) => setCredentialLabel(event.target.value)}
                placeholder="primary"
              />
            </label>
            <span className="field-note">
              A credential is encrypted server-side and never shown again after submission.
            </span>
            <button className="primary-action" type="submit" disabled={creating || !selectedType}>
              <Plug size={16} />
              {creating ? 'Creating…' : 'Create connector'}
            </button>
          </form>
        </div>

        <div className="manage-column manage-detail">
          {!selected ? (
            <div className="manage-empty">
              {detailBusy ? 'Loading connector…' : 'Select a connector to manage its health, runs, resources and mappings.'}
            </div>
          ) : (
            <>
              <h3>{selected.name}</h3>
              <div className="manage-detail-sub">
                {selected.display_name || selected.connector_type} · v{selected.connector_version}
                {' · '}
                {selected.has_credential ? 'Credential stored' : 'No credential stored'}
              </div>

              {notice && <div className="status-note ok" role="status">{notice}</div>}
              {testResult && (
                <div className={testResult.ok ? 'status-note ok' : 'status-note fail'} role="status">
                  <strong>{testResult.ok ? 'Connection healthy' : 'Connection failed'}</strong>
                  <br />
                  {testResult.detail}
                </div>
              )}

              <div className="manage-section">
                <h4>
                  <ShieldCheck size={13} /> Actions
                </h4>
                <div className="connector-actions">
                  <button type="button" disabled={busyAction !== null} onClick={testConnection}>
                    <Cable size={14} /> Test connection
                  </button>
                  <button
                    type="button"
                    disabled={busyAction !== null}
                    onClick={() => toggleEnabled(!selected.enabled)}
                  >
                    <Power size={14} /> {selected.enabled ? 'Disable' : 'Enable'}
                  </button>
                  <button type="button" disabled={busyAction !== null} onClick={runSync}>
                    <RefreshCw size={14} /> Sync
                  </button>
                  <button
                    type="button"
                    className="danger-text"
                    disabled={busyAction !== null}
                    onClick={() => setConfirmDisconnect(true)}
                  >
                    <Trash2 size={14} /> Disconnect
                  </button>
                </div>
                <div className="manage-inline">
                  <label>
                    <span>Run type</span>
                    <select
                      value={syncRunType}
                      onChange={(event) => setSyncRunType(event.target.value)}
                    >
                      {SYNC_RUN_TYPES.map((type) => (
                        <option key={type} value={type}>{type}</option>
                      ))}
                    </select>
                  </label>
                  <label>
                    <span>Limit (optional)</span>
                    <input
                      value={syncLimit}
                      onChange={(event) => setSyncLimit(event.target.value)}
                      placeholder="bounded"
                    />
                  </label>
                </div>

                {confirmDisconnect && (
                  <div className="run-confirmation" role="group" aria-label="Confirm disconnect">
                    <strong>Disconnect this connector?</strong>
                    <p>
                      The connector stops syncing. Enabling it again does not re-prove old
                      resources; a fresh sync is required.
                    </p>
                    <label className="checkbox-line">
                      <input
                        type="checkbox"
                        checked={purge}
                        onChange={(event) => setPurge(event.target.checked)}
                      />
                      Also purge ingested resources
                    </label>
                    <div>
                      <button
                        type="button"
                        className="secondary-action"
                        onClick={() => setConfirmDisconnect(false)}
                      >
                        Cancel
                      </button>
                      <button type="button" className="primary-action" onClick={disconnect}>
                        Confirm disconnect
                      </button>
                    </div>
                  </div>
                )}
              </div>

              <div className="manage-section">
                <h4>
                  <Activity size={13} /> Operational status
                </h4>
                <div className="status-grid">
                  <div>
                    <span>Enabled</span>
                    <strong>{selected.enabled ? 'Yes' : 'No'}</strong>
                  </div>
                  <div>
                    <span>Health</span>
                    <strong className={`connector-health ${healthClass(selected.health_status)}`}>
                      {selected.health_status || 'unknown'}
                    </strong>
                  </div>
                  <div>
                    <span>Last successful sync</span>
                    <strong>{formatTimestamp(selected.last_successful_sync_at)}</strong>
                  </div>
                  <div>
                    <span>Last reconciliation</span>
                    <strong>{formatTimestamp(selected.last_reconciliation_at)}</strong>
                  </div>
                  <div>
                    <span>Last failure</span>
                    <strong>{selected.last_failure_category || 'None'}</strong>
                  </div>
                  <div>
                    <span>Most recent run</span>
                    <strong>
                      {latestRun
                        ? `${latestRun.status}${latestRun.failure_category ? ` · ${latestRun.failure_category}` : ''}`
                        : 'No runs yet'}
                    </strong>
                  </div>
                </div>
                {selected.health_detail && (
                  <div className="field-note">Health detail: {selected.health_detail}</div>
                )}
              </div>

              <div className="manage-section">
                <h4>
                  <KeyRound size={13} /> Credential rotation
                </h4>
                <p className="manage-hint">
                  Submitting a replacement credential replaces the active one. The previous value
                  is never displayed or retained by this screen.
                </p>
                <form className="manage-form" onSubmit={rotateCredential}>
                  {(selectedType?.credential_fields || []).map((field) => (
                    <label key={field}>
                      <span>Replacement {field}</span>
                      <input
                        type="password"
                        autoComplete="new-password"
                        value={rotation[field] || ''}
                        onChange={(event) =>
                          setRotation((current) => ({ ...current, [field]: event.target.value }))
                        }
                        placeholder={`Replacement ${field}`}
                      />
                    </label>
                  ))}
                  <label>
                    <span>Label (optional)</span>
                    <input
                      value={rotationLabel}
                      onChange={(event) => setRotationLabel(event.target.value)}
                      placeholder="rotated"
                    />
                  </label>
                  <div className="connector-actions">
                    <button className="primary-action" type="submit" disabled={rotating}>
                      <KeyRound size={15} /> {rotating ? 'Rotating…' : 'Rotate credential'}
                    </button>
                    <button
                      type="button"
                      className="secondary-action danger-text"
                      disabled={busyAction !== null || !selected.has_credential}
                      onClick={() => setConfirmRevoke(true)}
                    >
                      Revoke credential
                    </button>
                  </div>
                  {confirmRevoke && (
                    <div className="run-confirmation" role="group" aria-label="Confirm revoke credential">
                      <strong>Revoke the stored credential?</strong>
                      <p>The connector will fail its next connection test until a new credential is supplied.</p>
                      <div>
                        <button type="button" className="secondary-action" onClick={() => setConfirmRevoke(false)}>
                          Cancel
                        </button>
                        <button type="button" className="primary-action" onClick={revokeCredential}>
                          Confirm revoke
                        </button>
                      </div>
                    </div>
                  )}
                </form>
              </div>

              <div className="manage-section">
                <h4>
                  <RefreshCw size={13} /> Recent runs
                </h4>
                {runs.length === 0 ? (
                  <div className="manage-empty">No sync or reconciliation runs recorded yet.</div>
                ) : (
                  <div className="manage-table-scroll">
                    <table className="manage-table">
                      <thead>
                        <tr>
                          <th>Type</th>
                          <th>Status</th>
                          <th>Started</th>
                          <th>Scanned</th>
                          <th>Created</th>
                          <th>Updated</th>
                          <th>Deleted</th>
                          <th>Quarantined</th>
                          <th>Skipped</th>
                          <th>Failure</th>
                        </tr>
                      </thead>
                      <tbody>
                        {runs.map((run) => (
                          <RunRow key={run.id} run={run} />
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>

              <div className="manage-section">
                <h4>
                  <Link2 size={13} /> Resources
                </h4>
                {resources.length === 0 ? (
                  <div className="manage-empty">No resources observed for this connector yet.</div>
                ) : (
                  <div className="manage-table-scroll">
                    <table className="manage-table">
                      <thead>
                        <tr>
                          <th>External id</th>
                          <th>Type</th>
                          <th>Title</th>
                          <th>Lifecycle</th>
                          <th>Permission</th>
                          <th>Quarantine reason</th>
                        </tr>
                      </thead>
                      <tbody>
                        {resources.map((resource) => (
                          <tr key={resource.id}>
                            <td>{resource.external_id}</td>
                            <td>{resource.resource_type}</td>
                            <td>{resource.title}</td>
                            <td>{resource.lifecycle_state}</td>
                            <td>{resource.permission_state}</td>
                            <td>{resource.quarantine_reason || '—'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>

              <div className="manage-section">
                <h4>
                  <Link2 size={13} /> User mappings
                </h4>
                {mappings.length === 0 ? (
                  <div className="manage-empty">No user mappings configured.</div>
                ) : (
                  <div className="manage-table-scroll">
                    <table className="manage-table">
                      <thead>
                        <tr>
                          <th>OpenJM principal</th>
                          <th>External user id</th>
                          <th>Status</th>
                          <th>Created</th>
                          <th />
                        </tr>
                      </thead>
                      <tbody>
                        {mappings.map((mapping) => (
                          <tr key={mapping.id}>
                            <td>{mapping.principal_id}</td>
                            <td>{mapping.external_user_id}</td>
                            <td>{mapping.status}</td>
                            <td>{formatTimestamp(mapping.created_at)}</td>
                            <td>
                              {mapping.status === 'active' && (
                                <button
                                  type="button"
                                  className="icon-button danger"
                                  title="Revoke mapping"
                                  aria-label={`Revoke mapping ${mapping.external_user_id}`}
                                  onClick={() => revokeMapping(mapping.id)}
                                >
                                  <Trash2 size={14} />
                                </button>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                <form className="manage-form manage-form-row" onSubmit={addMapping}>
                  <label>
                    <span>OpenJM principal id</span>
                    <input
                      value={mappingPrincipal}
                      onChange={(event) => setMappingPrincipal(event.target.value)}
                      placeholder="principal id"
                    />
                  </label>
                  <label>
                    <span>External user id</span>
                    <input
                      value={mappingExternal}
                      onChange={(event) => setMappingExternal(event.target.value)}
                      placeholder="provider user id"
                    />
                  </label>
                  <button className="primary-action" type="submit" disabled={mappingBusy}>
                    {mappingBusy ? 'Adding…' : 'Add mapping'}
                  </button>
                </form>
              </div>

              {selected.last_failure_category && (
                <div className="status-note fail">
                  <CircleAlert size={13} /> Last failure category: {selected.last_failure_category}
                </div>
              )}
              {!selected.last_failure_category && selected.health_status && (
                <div className="status-note ok">
                  <CircleCheck size={13} /> No recorded failure category.
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </section>
  )
}
