import { authorizedFetch } from './auth'

export type ExecutionMode = 'chat' | 'knowledge' | 'data' | 'hybrid'

export type Evidence = {
  source_type: string
  source_id: string
  title: string
  passage: string
  score?: number | null
  evidence_id?: string | null
  provenance?: Record<string, unknown>
  access_context?: Record<string, unknown>
  processing_location?: string | null
  observed_at?: string | null
  metadata: Record<string, unknown>
}

export type Message = {
  id: string
  role: string
  content: string
  execution_class?: string | null
  requested_mode?: ExecutionMode | null
  evidence: Evidence[]
  artifacts?: ChatArtifact[]
  created_at: string
}

export type ChatArtifactFormat = 'html' | 'markdown' | 'text' | 'csv'

/**
 * Metadata + download affordance for a Chat artifact. This is a Chat work
 * product, NOT a Governed Saved Report: the object never carries the file
 * content, base64 or the storage key — only the server artifact id, the
 * server-normalised filename, MIME type and size.
 */
export type ChatArtifact = {
  id: string
  title: string
  filename: string
  mime_type: string
  artifact_format: ChatArtifactFormat
  size_bytes: number
  state: string
  is_evidence_backed: boolean
  conversation_id?: string | null
  message_id?: string | null
  created_at: string
}

export type Conversation = {
  id: string
  title: string
  created_at: string
  updated_at: string
}

export type ConversationDetail = Conversation & {
  messages: Message[]
}

export type ChatResponse = {
  conversation_id: string
  message_id: string
  answer: string
  execution_class: 'general' | 'knowledge' | 'structured' | 'hybrid'
  mode: ExecutionMode
  evidence: Evidence[]
  artifacts?: ChatArtifact[]
}

export type DocumentRecord = {
  id: string
  original_name: string
  mime_type?: string | null
  size_bytes: number
  status: string
  indexed: boolean
  created_at: string
}

export type DataColumnSchema = {
  name: string
  type: string
  nullable: boolean
  primary_key: boolean
}

export type DataForeignKeySchema = {
  constrained_columns: string[]
  referred_schema?: string | null
  referred_table: string
  referred_columns: string[]
}

export type DataTableSchema = {
  schema_name: string
  name: string
  qualified_name: string
  columns: DataColumnSchema[]
  primary_key: string[]
  foreign_keys: DataForeignKeySchema[]
}

export type DataSourceRecord = {
  id: string
  name: string
  engine: 'sqlite' | 'postgresql'
  status: string
  enabled: boolean
  tables: DataTableSchema[]
  last_error?: string | null
  last_schema_refresh?: string | null
  created_at: string
  updated_at: string
}

export type DataSourceCreate = {
  name: string
  engine: 'sqlite' | 'postgresql'
  connection_uri: string
  enabled: boolean
}

export type SavedReportSummary = {
  id: string
  title: string
  conversation_id: string
  message_id: string
  execution_class: string
  snapshot_as_of: string
  created_at: string
  source_count: number
  available: boolean
}

export type SavedReportDetail = SavedReportSummary & {
  answer: string
  evidence: Evidence[]
  is_live: false
}

export type ReportRerunPreview = {
  report_id: string
  source_message_id: string
  original_question: string
  mode: 'knowledge' | 'data' | 'hybrid'
  snapshot_as_of: string
  original_source_count: number
  requires_explicit_send: true
  executes_queries: false
}

export type ReportDefinition = {
  id: string
  report_id: string
  version: number
  question: string
  mode: 'knowledge' | 'data' | 'hybrid'
  pinned_document_ids: string[]
  pinned_source_tables: Record<string, string[]>
  created_at: string
  executes_queries: false
  runnable: boolean
}

export type ReportRunResult = {
  answer: string | null
  evidence: Evidence[]
  structured_result: Record<string, unknown>
  trace_ids: string[]
}

export type ReportRunSummary = {
  id: string
  report_id: string
  definition_version: number
  requested_mode: string
  status: string
  started_at: string
  finished_at?: string | null
  failure_category?: string | null
  result_size_bytes?: number | null
  trace_count: number
}

export type ReportRunDetail = ReportRunSummary & {
  result: ReportRunResult | null
}

export type ConnectorTypeOperation = {
  name: string
  capability: string
  operation_class: string
  description: string
  required_permission: string
  requires_user_authorization: boolean
  requires_approval: boolean
  timeout_seconds: number
  idempotency: string
}

export type ConnectorTypeSpec = {
  type_id: string
  version: string
  key: string
  display_name: string
  description: string
  capabilities: string[]
  authorization_behavior: string
  requires_user_mapping: boolean
  credential_kind: string
  credential_fields: string[]
  credential_rotation: string
  event_support: boolean
  reconciliation_support: boolean
  incremental_support: boolean
  supports_test_connection: boolean
  timeout_seconds: number
  max_retries: number
  initial_sync_limit: number
  operations: ConnectorTypeOperation[]
}

export type ConnectorRun = {
  id: string
  run_type: string
  status: string
  started_at: string
  finished_at?: string | null
  items_scanned: number
  items_created: number
  items_updated: number
  items_deleted: number
  items_quarantined: number
  items_skipped: number
  failure_category?: string | null
  detail?: string | null
}

export type ConnectorInstance = {
  id: string
  name: string
  connector_type: string
  connector_version: string
  connector_key: string
  display_name: string
  enabled: boolean
  status: string
  health_status: string
  health_detail?: string | null
  config: Record<string, unknown>
  has_credential: boolean
  last_successful_connection_at?: string | null
  last_successful_sync_at?: string | null
  last_reconciliation_at?: string | null
  last_failure_category?: string | null
  last_failure_at?: string | null
  created_at: string
  updated_at: string
  runs?: ConnectorRun[]
}

export type ConnectorResource = {
  id: string
  external_id: string
  resource_type: string
  title: string
  external_revision?: string | null
  lifecycle_state: string
  permission_state: string
  quarantine_reason?: string | null
  document_id?: string | null
  last_observed_at?: string | null
  last_synced_at?: string | null
  last_reconciled_at?: string | null
  provenance?: Record<string, unknown>
}

export type ConnectorMapping = {
  id: string
  principal_id: string
  external_user_id: string
  status: string
  created_at?: string | null
  revoked_at?: string | null
}

export type ConnectorCreate = {
  name: string
  connector_type: string
  version?: string | null
  config: Record<string, unknown>
  credential?: Record<string, string> | null
  credential_label?: string
}

export type ConnectorTestResult = {
  ok: boolean
  detail: string
  connector: ConnectorInstance
}

export type Schedule = {
  id: string
  name: string
  schedule_type: string
  operation: string
  status: string
  enabled: boolean
  timezone: string
  interval_seconds: number
  next_run_at?: string | null
  last_run_at?: string | null
  last_result?: string | null
  last_failure_category?: string | null
  misfire_policy: string
  max_retries: number
}

export type ScheduleCreate = {
  name: string
  schedule_type: string
  operation: string
  interval_seconds: number
  timezone_name: string
  misfire_policy: string
  max_retries: number
  target?: Record<string, unknown> | null
}

export type NotificationChannel = {
  id: string
  name: string
  channel_type: string
  status: string
  enabled: boolean
  last_delivery_at?: string | null
  last_failure_category?: string | null
}

export type NotificationRecord = {
  id: string
  category: string
  subject: string
  status: string
  attempts: number
  max_attempts: number
  resource_type?: string | null
  resource_id?: string | null
  failure_category?: string | null
  created_at?: string | null
}

export class ApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/** Pull a human-readable message out of an error body. Most routes return a
 * plain string detail, while connectors and schedules return a
 * ``{code, message}`` object; both must surface a message, not "[object Object]". */
function detailMessage(body: unknown, fallback: string): string {
  if (body && typeof body === 'object') {
    const detail = (body as { detail?: unknown }).detail
    if (typeof detail === 'string' && detail) return detail
    if (detail && typeof detail === 'object') {
      const message = (detail as { message?: unknown }).message
      if (typeof message === 'string' && message) return message
    }
  }
  return fallback
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await authorizedFetch(url, init)
  if (!response.ok) {
    let message = `Request failed (${response.status})`
    try {
      message = detailMessage(await response.json(), message)
    } catch {
      // keep generic message
    }
    throw new ApiError(message, response.status)
  }
  return response.json()
}

export type ExportFormat = 'csv' | 'html'

async function downloadExport(url: string, fallbackName: string): Promise<void> {
  const response = await authorizedFetch(url)
  if (!response.ok) {
    let message = `Export failed (${response.status})`
    try {
      const body = await response.json()
      message = body.detail || message
    } catch {
      // keep generic message
    }
    throw new ApiError(message, response.status)
  }
  const blob = await response.blob()
  const disposition = response.headers.get('Content-Disposition') || ''
  const match = /filename="([^"]+)"/.exec(disposition)
  const filename = match ? match[1] : fallbackName
  const objectUrl = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = objectUrl
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(objectUrl)
}

export const api = {
  reports: () => request<SavedReportSummary[]>('/api/reports'),
  report: (id: string) => request<SavedReportDetail>(`/api/reports/${encodeURIComponent(id)}`),
  reportRerunPreview: (id: string) => request<ReportRerunPreview>(`/api/reports/${encodeURIComponent(id)}/rerun-preview`),
  reportDefinitions: (id: string) =>
    request<ReportDefinition[]>(`/api/reports/${encodeURIComponent(id)}/definitions`),
  createReportDefinition: (id: string) =>
    request<ReportDefinition>(`/api/reports/${encodeURIComponent(id)}/definitions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    }),
  reportRuns: (id: string, offset = 0, limit = 20) =>
    request<ReportRunSummary[]>(
      `/api/reports/${encodeURIComponent(id)}/runs?offset=${offset}&limit=${limit}`,
    ),
  reportRun: (runId: string) =>
    request<ReportRunDetail>(`/api/reports/runs/${encodeURIComponent(runId)}`),
  submitReportRun: (id: string, version: number, idempotencyKey: string) =>
    request<ReportRunDetail>(
      `/api/reports/${encodeURIComponent(id)}/definitions/${version}/runs`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ idempotency_key: idempotencyKey }),
      },
    ),
  saveReport: (messageId: string, title?: string) =>
    request<SavedReportDetail>('/api/reports', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(
        title !== undefined && title !== null
          ? { message_id: messageId, title }
          : { message_id: messageId },
      ),
    }),
  deleteReport: async (id: string): Promise<void> => {
    const response = await authorizedFetch(`/api/reports/${encodeURIComponent(id)}`, {
      method: 'DELETE',
    })
    if (!response.ok) throw new Error('Could not delete the saved report')
    // The API intentionally returns 204 No Content.
  },

  exportReport: (id: string, format: ExportFormat, index?: number) =>
    downloadExport(
      `/api/reports/${encodeURIComponent(id)}/exports/${format}` +
        (index === undefined ? '' : `?index=${index}`),
      `report.${format}`,
    ),

  exportReportRun: (reportId: string, runId: string, format: ExportFormat, index?: number) =>
    downloadExport(
      `/api/reports/${encodeURIComponent(reportId)}/runs/${encodeURIComponent(runId)}/exports/${format}` +
        (index === undefined ? '' : `?index=${index}`),
      `run.${format}`,
    ),

  conversations: () => request<Conversation[]>('/api/conversations'),

  conversation: (id: string) =>
    request<ConversationDetail>(`/api/conversations/${id}`),

  chat: (message: string, conversationId?: string | null, mode?: ExecutionMode) =>
    request<ChatResponse>('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message,
        conversation_id: conversationId || null,
        mode: mode || 'chat',
      }),
    }),

  // BV5-C Chat artifacts. Every response is metadata + a server artifact id;
  // the browser never receives file content in the transcript. Downloads go
  // through the authorized fetch so the request carries the caller's credential.
  artifacts: () => request<ChatArtifact[]>('/api/artifacts'),

  artifact: (id: string) =>
    request<ChatArtifact>(`/api/artifacts/${encodeURIComponent(id)}`),

  downloadArtifact: (id: string, fallbackName: string) =>
    downloadExport(`/api/artifacts/${encodeURIComponent(id)}/download`, fallbackName),

  downloadArtifactRender: (id: string, target: 'pdf' | 'docx', fallbackName: string) =>
    downloadExport(
      `/api/artifacts/${encodeURIComponent(id)}/render/${target}`,
      fallbackName,
    ),

  deleteArtifact: async (id: string): Promise<void> => {
    const response = await authorizedFetch(`/api/artifacts/${encodeURIComponent(id)}`, {
      method: 'DELETE',
    })
    if (!response.ok) {
      let message = 'Could not remove the artifact'
      try {
        const body = await response.json()
        message = body.detail || message
      } catch {
        // keep generic message
      }
      throw new ApiError(message, response.status)
    }
  },

  documents: () => request<DocumentRecord[]>('/api/knowledge/documents'),

  uploadDocument: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<DocumentRecord>('/api/knowledge/documents', {
      method: 'POST',
      body: form,
    })
  },

  deleteDocument: (id: string) =>
    request<{ deleted: boolean }>(`/api/knowledge/documents/${id}`, {
      method: 'DELETE',
    }),

  dataSources: () => request<DataSourceRecord[]>('/api/data/sources'),

  createDataSource: (payload: DataSourceCreate) =>
    request<DataSourceRecord>('/api/data/sources', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),

  testDataSource: (id: string) =>
    request<{ source_id: string; status: string; ok: boolean; detail: string }>(
      `/api/data/sources/${id}/test`,
      { method: 'POST' },
    ),

  refreshDataSource: (id: string) =>
    request<{ source: DataSourceRecord; table_count: number }>(
      `/api/data/sources/${id}/refresh`,
      { method: 'POST' },
    ),

  setDataSourceEnabled: (id: string, enabled: boolean) =>
    request<DataSourceRecord>(`/api/data/sources/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    }),

  deleteDataSource: (id: string) =>
    request<{ deleted: boolean; source_id: string }>(`/api/data/sources/${id}`, {
      method: 'DELETE',
    }),

  // VS7 governed connectors. Every call is tenant-scoped and permission-guarded
  // on the server; the browser only presents the result.
  connectorTypes: () =>
    request<{ types: ConnectorTypeSpec[] }>('/api/connectors/types'),

  connectors: () =>
    request<{ connectors: ConnectorInstance[] }>('/api/connectors'),

  connector: (id: string) =>
    request<ConnectorInstance>(`/api/connectors/${encodeURIComponent(id)}`),

  createConnector: (payload: ConnectorCreate) =>
    request<ConnectorInstance>('/api/connectors', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),

  testConnector: (id: string) =>
    request<ConnectorTestResult>(`/api/connectors/${encodeURIComponent(id)}/test`, {
      method: 'POST',
    }),

  setConnectorEnabled: (id: string, enabled: boolean) =>
    request<ConnectorInstance>(
      `/api/connectors/${encodeURIComponent(id)}/${enabled ? 'enable' : 'disable'}`,
      { method: 'POST' },
    ),

  rotateConnectorCredential: (id: string, credential: Record<string, string>, label?: string) =>
    request<{ rotated: boolean; credential_id: string; version: number; connector: ConnectorInstance }>(
      `/api/connectors/${encodeURIComponent(id)}/credentials/rotate`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ credential, label: label || null }),
      },
    ),

  revokeConnectorCredential: (id: string) =>
    request<{ revoked: boolean; connector: ConnectorInstance }>(
      `/api/connectors/${encodeURIComponent(id)}/credentials/revoke`,
      { method: 'POST' },
    ),

  disconnectConnector: (id: string, purge: boolean) =>
    request<{ quarantined: number; connector: ConnectorInstance }>(
      `/api/connectors/${encodeURIComponent(id)}/disconnect`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ purge }),
      },
    ),

  syncConnector: (id: string, runType: string, limit?: number) =>
    request<ConnectorRun>(`/api/connectors/${encodeURIComponent(id)}/sync`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ run_type: runType, limit: limit ?? null }),
    }),

  connectorRuns: (id: string) =>
    request<{ runs: ConnectorRun[] }>(`/api/connectors/${encodeURIComponent(id)}/runs`),

  connectorResources: (id: string) =>
    request<{ resources: ConnectorResource[] }>(
      `/api/connectors/${encodeURIComponent(id)}/resources`,
    ),

  connectorMappings: (id: string) =>
    request<{ mappings: ConnectorMapping[] }>(
      `/api/connectors/${encodeURIComponent(id)}/mappings`,
    ),

  createConnectorMapping: (id: string, principalId: string, externalUserId: string) =>
    request<ConnectorMapping>(`/api/connectors/${encodeURIComponent(id)}/mappings`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ principal_id: principalId, external_user_id: externalUserId }),
    }),

  deleteConnectorMapping: (id: string, mappingId: string) =>
    request<{ revoked: boolean; id: string }>(
      `/api/connectors/${encodeURIComponent(id)}/mappings/${encodeURIComponent(mappingId)}`,
      { method: 'DELETE' },
    ),

  // VS7 operations: schedules and notification channels. The operation
  // vocabulary is closed on the server, so the UI only ever offers what the
  // scheduler has registered.
  scheduleOperations: () =>
    request<{ operations: string[] }>('/api/operations/schedules/operations'),

  schedules: () =>
    request<{ schedules: Schedule[] }>('/api/operations/schedules'),

  createSchedule: (payload: ScheduleCreate) =>
    request<Schedule>('/api/operations/schedules', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),

  setScheduleState: (id: string, enabled: boolean, status?: string) =>
    request<Schedule>(`/api/operations/schedules/${encodeURIComponent(id)}/state`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled, status: status ?? null }),
    }),

  notificationChannels: () =>
    request<{ channel_types: string[]; channels: NotificationChannel[] }>(
      '/api/operations/notification-channels',
    ),

  createNotificationChannel: (name: string, channelType: string, config?: Record<string, unknown>) =>
    request<NotificationChannel>('/api/operations/notification-channels', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, channel_type: channelType, config: config ?? {} }),
    }),

  notifications: () =>
    request<{ notifications: NotificationRecord[] }>('/api/operations/notifications'),

  // BV3-B client administration (business-facing; tenant:admin only).
  admin: {
    members: () => request<AdminMember[]>('/api/admin/members'),
    provisionMember: (body: AdminMemberInput) =>
      request<AdminMember>('/api/admin/members', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }),
    changeMemberRole: (principalId: string, role: string) =>
      request<AdminMember>(`/api/admin/members/${encodeURIComponent(principalId)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ role }),
      }),
    revokeMember: (principalId: string) =>
      request<AdminMember>(`/api/admin/members/${encodeURIComponent(principalId)}/revoke`, {
        method: 'POST',
      }),
    reactivateMember: (principalId: string) =>
      request<AdminMember>(`/api/admin/members/${encodeURIComponent(principalId)}/reactivate`, {
        method: 'POST',
      }),
    revokeMemberSessions: (principalId: string) =>
      request<{ revoked: number }>(
        `/api/admin/members/${encodeURIComponent(principalId)}/sessions/revoke`,
        { method: 'POST' },
      ),

    departments: () => request<AdminDepartment[]>('/api/admin/departments'),
    createDepartment: (body: { slug: string; name: string }) =>
      request<AdminDepartment>('/api/admin/departments', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }),

    groups: () => request<AdminGroup[]>('/api/admin/groups'),
    createGroup: (body: { slug: string; name: string; department_id?: string | null }) =>
      request<AdminGroup>('/api/admin/groups', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }),
    groupMembers: (groupId: string) =>
      request<{ group_id: string; members: string[] }>(
        `/api/admin/groups/${encodeURIComponent(groupId)}/members`,
      ),
    addGroupMember: (groupId: string, principalId: string) =>
      request<{ group_id: string; principal_id: string; status: string }>(
        `/api/admin/groups/${encodeURIComponent(groupId)}/members`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ principal_id: principalId }),
        },
      ),
    removeGroupMember: (groupId: string, principalId: string) =>
      request<{ removed: number }>(
        `/api/admin/groups/${encodeURIComponent(groupId)}/members/${encodeURIComponent(principalId)}`,
        { method: 'DELETE' },
      ),

    stewards: () => request<AdminSteward[]>('/api/admin/stewards'),
    grantSteward: (body: AdminStewardInput) =>
      request<AdminSteward>('/api/admin/stewards', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }),
    revokeSteward: (principalId: string, scopeType: string, scopeId: string) =>
      request<{ revoked: number }>(
        `/api/admin/stewards?principal_id=${encodeURIComponent(principalId)}` +
          `&scope_type=${encodeURIComponent(scopeType)}&scope_id=${encodeURIComponent(scopeId)}`,
        { method: 'DELETE' },
      ),

    dataAccess: () => request<AdminDataAccess>('/api/admin/data-access'),

    preferences: () => request<AdminPreferences>('/api/admin/preferences'),
    updatePreferences: (preferences: Record<string, unknown>) =>
      request<AdminPreferences>('/api/admin/preferences', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ preferences }),
      }),

    usage: () => request<AdminUsageSummary>('/api/admin/usage'),
    audit: () => request<{ entries: AdminAuditEntry[] }>('/api/admin/audit'),
  },
}

export type AdminMember = {
  principal_id: string
  subject: string
  role: string
  status: string
  email?: string | null
  display_name?: string | null
  department_ids: string[]
  group_ids: string[]
}

export type AdminMemberInput = {
  subject: string
  role: string
  email?: string | null
  display_name?: string | null
}

export type AdminDepartment = {
  id: string
  slug: string
  name: string
  status: string
}

export type AdminGroup = {
  id: string
  slug: string
  name: string
  status: string
  department_id?: string | null
}

export type AdminSteward = {
  principal_id: string
  scope_type: string
  scope_id: string
  status: string
}

export type AdminStewardInput = {
  principal_id: string
  scope_type: string
  scope_id: string
}

export type AdminDataAccess = {
  sources: {
    id: string
    name?: string | null
    classification?: string | null
    department_id?: string | null
    tenant_visible?: boolean | null
  }[]
  documents: {
    id: string
    title?: string | null
    classification?: string | null
  }[]
}

export type AdminPreferences = {
  preferences: Record<string, unknown>
}

export type AdminUsageSummary = {
  tenant_id: string
  usage: Record<string, unknown>
  plan: unknown | null
  entitlements: unknown | null
  note: string
}

export type AdminAuditEntry = {
  action: string
  resource_type?: string | null
  resource_id?: string | null
  decision: string
  created_at?: string | null
}
