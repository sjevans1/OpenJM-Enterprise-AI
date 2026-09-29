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
  evidence: Evidence[]
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
  execution_class: 'general' | 'knowledge' | 'structured'
  evidence: Evidence[]
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

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init)
  if (!response.ok) {
    let message = `Request failed (${response.status})`
    try {
      const body = await response.json()
      message = body.detail || message
    } catch {
      // keep generic message
    }
    throw new Error(message)
  }
  return response.json()
}

export const api = {
  conversations: () => request<Conversation[]>('/api/conversations'),

  conversation: (id: string) =>
    request<ConversationDetail>(`/api/conversations/${id}`),

  chat: (message: string, conversationId?: string | null) =>
    request<ChatResponse>('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message,
        conversation_id: conversationId || null,
      }),
    }),

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
}
