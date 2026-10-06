/**
 * VS7 operations management surface: schedules and notification channels.
 *
 * A schedule may only target an operation the server has registered, so the
 * operation selector is populated from the API's closed vocabulary rather than
 * free text. Notification channels are similarly constrained to registered
 * channel types.
 */
import { FormEvent, useEffect, useState } from 'react'
import { BellRing, CalendarClock, CircleAlert, Play, Plus, Power } from 'lucide-react'
import {
  api,
  type NotificationChannel,
  type NotificationRecord,
  type Schedule,
} from './api'

type ConfigRow = { key: string; value: string }

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

export default function OperationsPanel() {
  const [operations, setOperations] = useState<string[]>([])
  const [schedules, setSchedules] = useState<Schedule[]>([])
  const [channelTypes, setChannelTypes] = useState<string[]>([])
  const [channels, setChannels] = useState<NotificationChannel[]>([])
  const [notifications, setNotifications] = useState<NotificationRecord[]>([])
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [busyId, setBusyId] = useState<string | null>(null)

  // Create schedule form.
  const [scheduleName, setScheduleName] = useState('')
  const [scheduleOperation, setScheduleOperation] = useState('')
  const [scheduleType, setScheduleType] = useState('interval')
  const [scheduleInterval, setScheduleInterval] = useState('900')
  const [scheduleTimezone, setScheduleTimezone] = useState('UTC')
  const [scheduleMisfire, setScheduleMisfire] = useState('skip')
  const [scheduleRetries, setScheduleRetries] = useState('2')
  const [scheduleTarget, setScheduleTarget] = useState('')
  const [creatingSchedule, setCreatingSchedule] = useState(false)

  // Create channel form.
  const [channelName, setChannelName] = useState('')
  const [channelType, setChannelType] = useState('')
  const [channelConfig, setChannelConfig] = useState<ConfigRow[]>([{ key: '', value: '' }])
  const [creatingChannel, setCreatingChannel] = useState(false)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const [operationResult, scheduleResult, channelResult, notificationResult] =
          await Promise.all([
            api.scheduleOperations(),
            api.schedules(),
            api.notificationChannels(),
            api.notifications(),
          ])
        if (cancelled) return
        const vocabulary = operationResult.operations.map((item) => String(item))
        setOperations(vocabulary)
        setSchedules(scheduleResult.schedules)
        setChannelTypes(channelResult.channel_types.map((item) => String(item)))
        setChannels(channelResult.channels)
        setNotifications(notificationResult.notifications)
        if (vocabulary[0]) setScheduleOperation(vocabulary[0])
        if (channelResult.channel_types[0]) setChannelType(String(channelResult.channel_types[0]))
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Unable to load operations data')
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

  const refreshSchedules = async () => {
    const result = await api.schedules()
    setSchedules(result.schedules)
  }

  const createSchedule = async (event: FormEvent) => {
    event.preventDefault()
    if (creatingSchedule) return
    if (!scheduleName.trim()) {
      setError('A schedule name is required')
      return
    }
    if (!scheduleOperation) {
      setError('Select a registered operation')
      return
    }
    const interval = Number(scheduleInterval.trim())
    if (!Number.isFinite(interval) || interval < 60) {
      setError('The interval must be at least 60 seconds')
      return
    }
    const retries = Number(scheduleRetries.trim())
    if (!Number.isInteger(retries) || retries < 0 || retries > 10) {
      setError('Max retries must be a whole number between 0 and 10')
      return
    }
    let target: Record<string, unknown> | null = null
    if (scheduleTarget.trim()) {
      try {
        const parsed = JSON.parse(scheduleTarget.trim())
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
          throw new Error('not an object')
        }
        target = parsed as Record<string, unknown>
      } catch {
        setError('The schedule target must be a JSON object')
        return
      }
    }
    setCreatingSchedule(true)
    setError(null)
    setNotice(null)
    try {
      const created = await api.createSchedule({
        name: scheduleName.trim(),
        schedule_type: scheduleType.trim() || 'interval',
        operation: scheduleOperation,
        interval_seconds: interval,
        timezone_name: scheduleTimezone.trim() || 'UTC',
        misfire_policy: scheduleMisfire,
        max_retries: retries,
        target,
      })
      setScheduleName('')
      setScheduleTarget('')
      setNotice(`Schedule "${created.name}" created`)
      await refreshSchedules()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to create the schedule')
    } finally {
      setCreatingSchedule(false)
    }
  }

  const setScheduleEnabled = async (schedule: Schedule, enabled: boolean) => {
    setBusyId(schedule.id)
    setError(null)
    try {
      await api.setScheduleState(schedule.id, enabled)
      await refreshSchedules()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to change the schedule state')
    } finally {
      setBusyId(null)
    }
  }

  const createChannel = async (event: FormEvent) => {
    event.preventDefault()
    if (creatingChannel) return
    if (!channelName.trim()) {
      setError('A channel name is required')
      return
    }
    if (!channelType) {
      setError('Select a registered channel type')
      return
    }
    setCreatingChannel(true)
    setError(null)
    setNotice(null)
    try {
      await api.createNotificationChannel(
        channelName.trim(),
        channelType,
        rowsToConfig(channelConfig),
      )
      setChannelName('')
      setChannelConfig([{ key: '', value: '' }])
      const result = await api.notificationChannels()
      setChannels(result.channels)
      setChannelTypes(result.channel_types.map((item) => String(item)))
      setNotice('Notification channel created')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to create the channel')
    } finally {
      setCreatingChannel(false)
    }
  }

  const updateConfigRow = (index: number, patch: Partial<ConfigRow>) => {
    setChannelConfig((current) =>
      current.map((row, position) => (position === index ? { ...row, ...patch } : row)),
    )
  }

  return (
    <section className="manage-stage">
      <div className="manage-intro">
        <div className="eyebrow">Governed automation</div>
        <h2>Operations</h2>
        <p>
          Schedule only the operations the platform has registered, and notify through the
          channel types it supports. Neither a schedule nor a channel can be pointed at an
          arbitrary destination.
        </p>
      </div>

      {error && <div className="error-banner data-error">{error}</div>}
      {notice && <div className="status-note ok" role="status">{notice}</div>}

      <div className="manage-grid">
        <div className="manage-column">
          <div className="manage-list-heading">
            <strong>
              <CalendarClock size={13} /> Schedules
            </strong>
            <span>{schedules.length}</span>
          </div>

          {loading ? (
            <div className="manage-empty">Loading schedules…</div>
          ) : schedules.length === 0 ? (
            <div className="manage-empty">No schedules configured yet.</div>
          ) : (
            schedules.map((schedule) => (
              <div className="manage-item" key={schedule.id}>
                <span className="manage-item-name">{schedule.name}</span>
                <span className="manage-item-meta">
                  {schedule.operation} · every {schedule.interval_seconds}s · {schedule.timezone}
                </span>
                <span className="manage-item-row">
                  <span className={schedule.enabled ? 'source-enabled' : 'source-disabled'}>
                    {schedule.enabled ? 'Enabled' : 'Disabled'}
                  </span>
                  <span className={`connector-health ${schedule.enabled ? 'healthy' : 'unknown'}`}>
                    {schedule.status}
                  </span>
                </span>
                <span className="manage-item-meta">
                  Next run: {formatTimestamp(schedule.next_run_at)} · Last run:{' '}
                  {formatTimestamp(schedule.last_run_at)}
                </span>
                {schedule.last_result && (
                  <span className="manage-item-meta">Last result: {schedule.last_result}</span>
                )}
                {schedule.last_failure_category && (
                  <span className="manage-item-failure">
                    Last failure: {schedule.last_failure_category}
                  </span>
                )}
                <span className="connector-actions">
                  <button
                    type="button"
                    disabled={busyId === schedule.id}
                    onClick={() => setScheduleEnabled(schedule, !schedule.enabled)}
                  >
                    <Power size={13} /> {schedule.enabled ? 'Disable' : 'Enable'}
                  </button>
                </span>
              </div>
            ))
          )}

          <form className="manage-form" onSubmit={createSchedule}>
            <div className="manage-list-heading">
              <strong>Add schedule</strong>
            </div>
            <label>
              <span>Schedule name</span>
              <input
                value={scheduleName}
                onChange={(event) => setScheduleName(event.target.value)}
                placeholder="e.g. Incremental workspace sync"
              />
            </label>
            <label>
              <span>Operation</span>
              <select
                value={scheduleOperation}
                onChange={(event) => setScheduleOperation(event.target.value)}
              >
                {operations.map((operation) => (
                  <option key={operation} value={operation}>{operation}</option>
                ))}
              </select>
            </label>
            <span className="field-note">
              Only registered operations can be scheduled. The list comes from the server.
            </span>
            <label>
              <span>Schedule type</span>
              <input
                value={scheduleType}
                onChange={(event) => setScheduleType(event.target.value)}
                placeholder="interval"
              />
            </label>
            <div className="manage-form-row">
              <label>
                <span>Interval (seconds)</span>
                <input
                  value={scheduleInterval}
                  onChange={(event) => setScheduleInterval(event.target.value)}
                  placeholder="900"
                />
              </label>
              <label>
                <span>Timezone</span>
                <input
                  value={scheduleTimezone}
                  onChange={(event) => setScheduleTimezone(event.target.value)}
                  placeholder="UTC"
                />
              </label>
            </div>
            <div className="manage-form-row">
              <label>
                <span>Misfire policy</span>
                <select
                  value={scheduleMisfire}
                  onChange={(event) => setScheduleMisfire(event.target.value)}
                >
                  <option value="skip">skip</option>
                  <option value="catchup">catchup</option>
                </select>
              </label>
              <label>
                <span>Max retries</span>
                <input
                  value={scheduleRetries}
                  onChange={(event) => setScheduleRetries(event.target.value)}
                  placeholder="2"
                />
              </label>
            </div>
            <label>
              <span>Target (optional JSON object)</span>
              <input
                value={scheduleTarget}
                onChange={(event) => setScheduleTarget(event.target.value)}
                placeholder='{"connector_id":"..."}'
              />
            </label>
            <button className="primary-action" type="submit" disabled={creatingSchedule}>
              <Plus size={15} /> {creatingSchedule ? 'Creating…' : 'Create schedule'}
            </button>
          </form>
        </div>

        <div className="manage-column">
          <div className="manage-list-heading">
            <strong>
              <BellRing size={13} /> Notification channels
            </strong>
            <span>{channels.length}</span>
          </div>

          {loading ? (
            <div className="manage-empty">Loading channels…</div>
          ) : channels.length === 0 ? (
            <div className="manage-empty">No notification channels configured yet.</div>
          ) : (
            channels.map((channel) => (
              <div className="manage-item" key={channel.id}>
                <span className="manage-item-name">{channel.name}</span>
                <span className="manage-item-meta">{channel.channel_type}</span>
                <span className="manage-item-row">
                  <span className={channel.enabled ? 'source-enabled' : 'source-disabled'}>
                    {channel.enabled ? 'Enabled' : 'Disabled'}
                  </span>
                  <span className={`connector-health ${channel.last_failure_category ? 'error' : 'healthy'}`}>
                    {channel.status}
                  </span>
                </span>
                <span className="manage-item-meta">
                  Last delivery: {formatTimestamp(channel.last_delivery_at)}
                </span>
                {channel.last_failure_category && (
                  <span className="manage-item-failure">
                    Last failure: {channel.last_failure_category}
                  </span>
                )}
              </div>
            ))
          )}

          <form className="manage-form" onSubmit={createChannel}>
            <div className="manage-list-heading">
              <strong>Add channel</strong>
            </div>
            <label>
              <span>Channel name</span>
              <input
                value={channelName}
                onChange={(event) => setChannelName(event.target.value)}
                placeholder="e.g. In-app alerts"
              />
            </label>
            <label>
              <span>Channel type</span>
              <select value={channelType} onChange={(event) => setChannelType(event.target.value)}>
                {channelTypes.map((type) => (
                  <option key={type} value={type}>{type}</option>
                ))}
              </select>
            </label>
            <div>
              <span className="field-label">Configuration</span>
              {channelConfig.map((row, index) => (
                <div className="kv-row" key={index}>
                  <input
                    aria-label={`Channel config key ${index + 1}`}
                    value={row.key}
                    onChange={(event) => updateConfigRow(index, { key: event.target.value })}
                    placeholder="key"
                  />
                  <input
                    aria-label={`Channel config value ${index + 1}`}
                    value={row.value}
                    onChange={(event) => updateConfigRow(index, { value: event.target.value })}
                    placeholder="value"
                  />
                </div>
              ))}
              <button
                type="button"
                className="secondary-action"
                onClick={() => setChannelConfig((current) => [...current, { key: '', value: '' }])}
              >
                Add config field
              </button>
            </div>
            <span className="field-note">
              Destination keys such as url, endpoint or webhook are refused by the server.
            </span>
            <button className="primary-action" type="submit" disabled={creatingChannel}>
              <Plus size={15} /> {creatingChannel ? 'Creating…' : 'Create channel'}
            </button>
          </form>
        </div>
      </div>

      <div className="manage-section">
        <h4>
          <Play size={13} /> My notifications
        </h4>
        {notifications.length === 0 ? (
          <div className="manage-empty">No notifications have been raised for you.</div>
        ) : (
          <div className="manage-table-scroll">
            <table className="manage-table">
              <thead>
                <tr>
                  <th>Category</th>
                  <th>Subject</th>
                  <th>Status</th>
                  <th>Attempts</th>
                  <th>Resource</th>
                  <th>Failure</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                {notifications.map((notification) => (
                  <tr key={notification.id}>
                    <td>{notification.category}</td>
                    <td>{notification.subject}</td>
                    <td>{notification.status}</td>
                    <td>{notification.attempts}/{notification.max_attempts}</td>
                    <td>{notification.resource_type || '—'}</td>
                    <td>{notification.failure_category || '—'}</td>
                    <td>{formatTimestamp(notification.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {!loading && notifications.some((item) => item.failure_category) && (
          <div className="status-note fail">
            <CircleAlert size={13} /> Some notifications have a recorded failure category.
          </div>
        )}
      </div>
    </section>
  )
}
