export type Evidence = {
  source_type: string
  source_id: string
  title: string
  passage: string
  score?: number | null
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
  execution_class: 'general' | 'knowledge'
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
}
