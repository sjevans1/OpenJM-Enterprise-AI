import { FormEvent, useEffect, useMemo, useRef, useState } from 'react'
import {
  Bot,
  BrainCircuit,
  ChevronRight,
  Database,
  FileText,
  FolderOpen,
  Gauge,
  MessageSquareText,
  Plus,
  Send,
  Settings,
  ShieldCheck,
  Sparkles,
  Trash2,
  Upload,
  Workflow,
} from 'lucide-react'
import {
  api,
  type Conversation,
  type DocumentRecord,
  type Evidence,
  type Message,
} from './api'

type View = 'chat' | 'knowledge'

const futureNav = [
  { label: 'Data', icon: Database },
  { label: 'Reports', icon: Gauge },
  { label: 'Automations', icon: Workflow },
  { label: 'Administration', icon: Settings },
]

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function relativeDate(value: string) {
  const date = new Date(value)
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

function EvidencePanel({ evidence }: { evidence: Evidence[] }) {
  if (!evidence.length) return null

  return (
    <div className="evidence-stack">
      <div className="evidence-heading">
        <ShieldCheck size={14} />
        Evidence used
      </div>
      {evidence.map((item, index) => (
        <details className="evidence-card" key={`${item.source_id}-${index}`}>
          <summary>
            <span className="citation-index">{index + 1}</span>
            <span className="evidence-title">{item.title}</span>
            <ChevronRight size={14} className="summary-chevron" />
          </summary>
          <div className="evidence-passage">{item.passage}</div>
        </details>
      ))}
    </div>
  )
}

export default function App() {
  const [view, setView] = useState<View>('chat')
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [chatError, setChatError] = useState<string | null>(null)
  const [documents, setDocuments] = useState<DocumentRecord[]>([])
  const [uploading, setUploading] = useState(false)
  const [knowledgeError, setKnowledgeError] = useState<string | null>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const loadConversations = async () => {
    try {
      setConversations(await api.conversations())
    } catch (error) {
      setChatError(error instanceof Error ? error.message : 'Unable to load conversations')
    }
  }

  const loadDocuments = async () => {
    try {
      setDocuments(await api.documents())
    } catch (error) {
      setKnowledgeError(error instanceof Error ? error.message : 'Unable to load documents')
    }
  }

  useEffect(() => {
    loadConversations()
    loadDocuments()
  }, [])

  const openConversation = async (id: string) => {
    setView('chat')
    setChatError(null)
    setActiveConversationId(id)
    try {
      const conversation = await api.conversation(id)
      setMessages(conversation.messages)
    } catch (error) {
      setChatError(error instanceof Error ? error.message : 'Unable to load conversation')
    }
  }

  const newConversation = () => {
    setView('chat')
    setActiveConversationId(null)
    setMessages([])
    setChatError(null)
    setInput('')
    requestAnimationFrame(() => inputRef.current?.focus())
  }

  const submitMessage = async (event?: FormEvent) => {
    event?.preventDefault()
    const message = input.trim()
    if (!message || sending) return

    setInput('')
    setSending(true)
    setChatError(null)

    const optimisticUser: Message = {
      id: `temp-${Date.now()}`,
      role: 'user',
      content: message,
      evidence: [],
      created_at: new Date().toISOString(),
    }
    setMessages((current) => [...current, optimisticUser])

    try {
      const response = await api.chat(message, activeConversationId)
      const assistant: Message = {
        id: response.message_id,
        role: 'assistant',
        content: response.answer,
        execution_class: response.execution_class,
        evidence: response.evidence,
        created_at: new Date().toISOString(),
      }
      setMessages((current) => [...current, assistant])
      if (!activeConversationId) {
        setActiveConversationId(response.conversation_id)
      }
      await loadConversations()
    } catch (error) {
      setChatError(error instanceof Error ? error.message : 'Unable to send message')
    } finally {
      setSending(false)
      requestAnimationFrame(() => inputRef.current?.focus())
    }
  }

  const uploadDocument = async (file?: File) => {
    if (!file || uploading) return
    setUploading(true)
    setKnowledgeError(null)
    try {
      await api.uploadDocument(file)
      await loadDocuments()
    } catch (error) {
      setKnowledgeError(error instanceof Error ? error.message : 'Upload failed')
    } finally {
      setUploading(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const deleteDocument = async (id: string) => {
    setKnowledgeError(null)
    try {
      await api.deleteDocument(id)
      await loadDocuments()
    } catch (error) {
      setKnowledgeError(error instanceof Error ? error.message : 'Delete failed')
    }
  }

  const activeExecutionClass = useMemo(() => {
    const lastAssistant = [...messages].reverse().find((message) => message.role === 'assistant')
    return lastAssistant?.execution_class
  }, [messages])

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">
            <BrainCircuit size={20} />
          </div>
          <div>
            <div className="brand-name">OpenJM</div>
            <div className="brand-subtitle">Enterprise AI</div>
          </div>
        </div>

        <button className="new-chat-button" onClick={newConversation}>
          <Plus size={16} />
          New conversation
        </button>

        <nav className="primary-nav">
          <button
            className={view === 'chat' ? 'nav-item active' : 'nav-item'}
            onClick={() => setView('chat')}
          >
            <MessageSquareText size={17} />
            Chat
          </button>
          <button
            className={view === 'knowledge' ? 'nav-item active' : 'nav-item'}
            onClick={() => setView('knowledge')}
          >
            <FolderOpen size={17} />
            Knowledge
            <span className="count-pill">{documents.length}</span>
          </button>

          <div className="nav-divider" />
          <div className="nav-section-label">Next capabilities</div>
          {futureNav.map((item) => (
            <button className="nav-item future" key={item.label} disabled>
              <item.icon size={17} />
              {item.label}
              <span className="phase-pill">Phase 2</span>
            </button>
          ))}
        </nav>

        <div className="conversation-section">
          <div className="nav-section-label">Recent conversations</div>
          <div className="conversation-list">
            {conversations.length === 0 ? (
              <div className="sidebar-empty">No conversations yet</div>
            ) : (
              conversations.map((conversation) => (
                <button
                  key={conversation.id}
                  onClick={() => openConversation(conversation.id)}
                  className={
                    activeConversationId === conversation.id
                      ? 'conversation-link selected'
                      : 'conversation-link'
                  }
                >
                  <span>{conversation.title}</span>
                  <small>{relativeDate(conversation.updated_at)}</small>
                </button>
              ))
            )}
          </div>
        </div>

        <div className="sidebar-footer">
          <div className="status-dot" />
          <div>
            <strong>Local workspace</strong>
            <span>DB-GPT knowledge enabled</span>
          </div>
        </div>
      </aside>

      <main className="workspace">
        {view === 'chat' ? (
          <>
            <header className="workspace-header">
              <div>
                <div className="eyebrow">OpenJM workspace</div>
                <h1>{activeConversationId ? 'Conversation' : 'New conversation'}</h1>
              </div>
              <div className="header-badges">
                <span className="system-badge">
                  <ShieldCheck size={14} />
                  Governed
                </span>
                {activeExecutionClass && (
                  <span className={`route-badge ${activeExecutionClass}`}>
                    {activeExecutionClass === 'knowledge' ? (
                      <FileText size={14} />
                    ) : (
                      <Sparkles size={14} />
                    )}
                    {activeExecutionClass}
                  </span>
                )}
              </div>
            </header>

            <section className="chat-stage">
              {messages.length === 0 ? (
                <div className="welcome-panel">
                  <div className="welcome-icon">
                    <Bot size={29} />
                  </div>
                  <div className="eyebrow">OpenJM Enterprise AI</div>
                  <h2>Ask across your work.</h2>
                  <p>
                    Conversations persist on the server. When your question is supported by
                    indexed knowledge, OpenJM retrieves evidence before the model answers.
                  </p>
                  <div className="prompt-grid">
                    {[
                      'What documents do you have loaded?',
                      'Summarize the key points in my indexed documents.',
                      'Hello, my name is Sam. Please remember that for this conversation.',
                    ].map((prompt) => (
                      <button
                        key={prompt}
                        onClick={() => {
                          setInput(prompt)
                          requestAnimationFrame(() => inputRef.current?.focus())
                        }}
                      >
                        {prompt}
                        <ChevronRight size={15} />
                      </button>
                    ))}
                  </div>
                </div>
              ) : (
                <div className="message-list">
                  {messages.map((message) => (
                    <article
                      key={message.id}
                      className={message.role === 'user' ? 'message user-message' : 'message assistant-message'}
                    >
                      <div className="message-avatar">
                        {message.role === 'user' ? 'You' : <BrainCircuit size={16} />}
                      </div>
                      <div className="message-body">
                        <div className="message-role">
                          {message.role === 'user' ? 'You' : 'OpenJM'}
                          {message.execution_class && (
                            <span className={`inline-route ${message.execution_class}`}>
                              {message.execution_class}
                            </span>
                          )}
                        </div>
                        <div className="message-content">{message.content}</div>
                        {message.role === 'assistant' && (
                          <EvidencePanel evidence={message.evidence || []} />
                        )}
                      </div>
                    </article>
                  ))}
                  {sending && (
                    <article className="message assistant-message">
                      <div className="message-avatar"><BrainCircuit size={16} /></div>
                      <div className="message-body">
                        <div className="message-role">OpenJM</div>
                        <div className="thinking">
                          <span />
                          <span />
                          <span />
                          Planning and retrieving evidence
                        </div>
                      </div>
                    </article>
                  )}
                </div>
              )}
            </section>

            <div className="composer-wrap">
              {chatError && <div className="error-banner">{chatError}</div>}
              <form className="composer" onSubmit={submitMessage}>
                <textarea
                  ref={inputRef}
                  value={input}
                  onChange={(event) => setInput(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' && !event.shiftKey) {
                      event.preventDefault()
                      submitMessage()
                    }
                  }}
                  placeholder="Ask OpenJM about your work..."
                  rows={1}
                />
                <button className="send-button" type="submit" disabled={!input.trim() || sending}>
                  <Send size={17} />
                </button>
              </form>
              <div className="composer-caption">
                Server-side conversation history • DB-GPT-backed knowledge retrieval
              </div>
            </div>
          </>
        ) : (
          <>
            <header className="workspace-header">
              <div>
                <div className="eyebrow">Governed knowledge</div>
                <h1>Knowledge</h1>
              </div>
              <button className="primary-action" onClick={() => fileRef.current?.click()} disabled={uploading}>
                <Upload size={16} />
                {uploading ? 'Indexing…' : 'Add document'}
              </button>
              <input
                ref={fileRef}
                className="hidden-input"
                type="file"
                accept=".pdf,.docx,.pptx,.txt,.md,.html,.htm"
                onChange={(event) => uploadDocument(event.target.files?.[0])}
              />
            </header>

            <section className="knowledge-stage">
              <div className="knowledge-intro">
                <div>
                  <div className="eyebrow">Evidence layer</div>
                  <h2>Documents available to OpenJM</h2>
                  <p>
                    Files uploaded here are parsed, chunked and indexed through the DB-GPT
                    knowledge adapter. Chat retrieves from this evidence layer automatically.
                  </p>
                </div>
                <div className="metric-card">
                  <span>Indexed</span>
                  <strong>{documents.filter((document) => document.indexed).length}</strong>
                </div>
              </div>

              {knowledgeError && <div className="error-banner">{knowledgeError}</div>}

              <div className="document-table">
                <div className="table-head">
                  <span>Document</span>
                  <span>Size</span>
                  <span>Status</span>
                  <span />
                </div>
                {documents.length === 0 ? (
                  <div className="document-empty">
                    <FileText size={28} />
                    <strong>No documents indexed yet</strong>
                    <span>Add a document to make it available to OpenJM Chat.</span>
                    <button onClick={() => fileRef.current?.click()}>
                      <Upload size={15} />
                      Add first document
                    </button>
                  </div>
                ) : (
                  documents.map((document) => (
                    <div className="document-row" key={document.id}>
                      <div className="document-name">
                        <div className="file-icon"><FileText size={17} /></div>
                        <div>
                          <strong>{document.original_name}</strong>
                          <span>{document.mime_type || 'document'}</span>
                        </div>
                      </div>
                      <span>{formatBytes(document.size_bytes)}</span>
                      <span className={`doc-status ${document.status}`}>
                        <span />
                        {document.status}
                      </span>
                      <button
                        className="icon-button danger"
                        title="Delete document"
                        onClick={() => deleteDocument(document.id)}
                      >
                        <Trash2 size={15} />
                      </button>
                    </div>
                  ))
                )}
              </div>
            </section>
          </>
        )}
      </main>
    </div>
  )
}
