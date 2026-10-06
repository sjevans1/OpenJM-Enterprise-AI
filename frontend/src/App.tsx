import { FormEvent, useEffect, useMemo, useRef, useState } from 'react'
import {
  Bot,
  BrainCircuit,
  ChevronDown,
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
  ApiError,
  type Conversation,
  type DataSourceRecord,
  type DocumentRecord,
  type Evidence,
  type ExecutionMode,
  type Message,
  type SavedReportSummary,
  type SavedReportDetail,
  type ReportDefinition,
  type ReportRunSummary,
  type ReportRunDetail,
} from './api'

type View = 'chat' | 'knowledge' | 'data' | 'reports'

type ReportRunIntent = {
  reportId: string
  version: number
  idempotencyKey: string
}

const MODE_OPTIONS: { value: ExecutionMode; label: string }[] = [
  { value: 'chat', label: 'Chat' },
  { value: 'knowledge', label: 'Knowledge' },
  { value: 'data', label: 'Data' },
  { value: 'hybrid', label: 'Hybrid' },
]

const futureNav = [
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

function StructuredEvidenceBody({ item }: { item: Evidence }) {
  let payload: { columns?: unknown; rows?: unknown; row_count?: unknown; truncated?: unknown } = {}
  try {
    payload = JSON.parse(item.passage)
  } catch {
    return <div className="evidence-passage">{item.passage}</div>
  }

  const columns = Array.isArray(payload.columns)
    ? payload.columns.map((value) => String(value))
    : []
  const rows = Array.isArray(payload.rows) ? payload.rows : []
  const sql = typeof item.metadata?.sql === 'string' ? item.metadata.sql : null
  const rowCount = typeof item.metadata?.row_count === 'number'
    ? item.metadata.row_count
    : typeof payload.row_count === 'number'
      ? payload.row_count
      : rows.length

  return (
    <div className="structured-evidence">
      <div className="structured-evidence-meta">
        <span>{rowCount} row{rowCount === 1 ? '' : 's'}</span>
        {item.processing_location && <span>{item.processing_location}</span>}
        {payload.truncated === true && <span>Result bounded</span>}
      </div>
      {columns.length > 0 && rows.length > 0 && (
        <div className="structured-result-scroll">
          <table className="structured-result-table">
            <thead>
              <tr>{columns.map((column) => <th key={column}>{column}</th>)}</tr>
            </thead>
            <tbody>
              {rows.slice(0, 20).map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {(Array.isArray(row) ? row : [row]).map((cell, cellIndex) => (
                    <td key={cellIndex}>{String(cell ?? '')}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {sql && (
        <div className="sql-provenance">
          <span>Executed read-only query</span>
          <code>{sql}</code>
        </div>
      )}
    </div>
  )
}

export function EvidencePanel({ evidence }: { evidence: Evidence[] }) {
  if (!evidence.length) return null

  // Count documents and data evidence separately for independent numbering
  let docCount = 0
  let dataCount = 0

  return (
    <div className="evidence-stack">
      <div className="evidence-heading">
        <ShieldCheck size={14} />
        Evidence used
      </div>
      {evidence.map((item, index) => {
        let citationPrefix: string
        if (item.source_type === 'document') {
          docCount++
          citationPrefix = `[DOC ${docCount}]`
        } else if (item.source_type === 'structured_query') {
          dataCount++
          citationPrefix = `[DATA ${dataCount}]`
        } else {
          // Fallback for any other source types
          citationPrefix = `[EVIDENCE ${index + 1}]`
        }
        return (
          <details className="evidence-card" key={`${item.source_id}-${index}`}>
            <summary>
              <span className="citation-index">{citationPrefix}</span>
              <span className="evidence-title">{item.title}</span>
              <ChevronRight size={14} className="summary-chevron" />
            </summary>
            {item.source_type === 'structured_query' ? (
              <StructuredEvidenceBody item={item} />
            ) : (
              <div className="evidence-passage">{item.passage}</div>
            )}
          </details>
        );
      })}
    </div>
  );
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
  const [dataSources, setDataSources] = useState<DataSourceRecord[]>([])
  const [dataError, setDataError] = useState<string | null>(null)
  const [dataBusyId, setDataBusyId] = useState<string | null>(null)
  const [creatingSource, setCreatingSource] = useState(false)
  const [sourceName, setSourceName] = useState('')
  const [sourceEngine, setSourceEngine] = useState<'sqlite' | 'postgresql'>('sqlite')
  const [sourceUri, setSourceUri] = useState('')
  const [reports, setReports] = useState<SavedReportSummary[]>([])
  const [activeReport, setActiveReport] = useState<SavedReportDetail | null>(null)
  const [reportError, setReportError] = useState<string | null>(null)
  const [reportBusyId, setReportBusyId] = useState<string | null>(null)
  const [reportDefinitions, setReportDefinitions] = useState<ReportDefinition[]>([])
  const [activeDefinition, setActiveDefinition] = useState<ReportDefinition | null>(null)
  const [reportRuns, setReportRuns] = useState<ReportRunSummary[]>([])
  const [activeRun, setActiveRun] = useState<ReportRunDetail | null>(null)
  const [reportExecutionError, setReportExecutionError] = useState<string | null>(null)
  const [reportExecutionBusy, setReportExecutionBusy] = useState(false)
  const [confirmingRun, setConfirmingRun] = useState(false)
  const [pendingRunIntent, setPendingRunIntent] = useState<ReportRunIntent | null>(null)
  const [rerunNotice, setRerunNotice] = useState(false)
  const [selectedMode, setSelectedMode] = useState<ExecutionMode>('chat')
  const [modeMenuOpen, setModeMenuOpen] = useState(false)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const reportOpenSequence = useRef(0)
  const reportExecutionSequence = useRef(0)
  const reportExecutionInFlight = useRef(false)
  const pendingRunIntentRef = useRef<ReportRunIntent | null>(null)
  const pendingReportId = useRef<string | null>(null)

  // Persist selected mode across sessions via localStorage.
  useEffect(() => {
    const stored = localStorage.getItem('openjm_chat_mode') as ExecutionMode | null
    if (stored && MODE_OPTIONS.some((option) => option.value === stored)) {
      setSelectedMode(stored)
    }
  }, [])

  useEffect(() => {
    localStorage.setItem('openjm_chat_mode', selectedMode)
  }, [selectedMode])

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

  const loadDataSources = async () => {
    try {
      setDataSources(await api.dataSources())
    } catch (error) {
      setDataError(error instanceof Error ? error.message : 'Unable to load data sources')
    }
  }

  const loadReports = async () => {
    try {
      const loaded = await api.reports()
      setReports(loaded)
      const pendingId = pendingReportId.current
      if (pendingId) {
        const pendingSummary = loaded.find((report) => report.id === pendingId)
        if (!pendingSummary?.available) {
          reportOpenSequence.current += 1
          pendingReportId.current = null
          setReportBusyId((current) => current === pendingId ? null : current)
        }
      }
      setActiveReport((current) => {
        if (!current) return null
        const summary = loaded.find((report) => report.id === current.id)
        return summary?.available ? current : null
      })
    } catch (error) {
      setReportError(error instanceof Error ? error.message : 'Unable to load saved reports')
    }
  }

  useEffect(() => {
    loadConversations()
    loadDocuments()
    loadDataSources()
    loadReports()
  }, [])

  const openConversation = async (id: string) => {
    setRerunNotice(false)
    setView('chat')
    setChatError(null)
    setActiveConversationId(id)
    try {
      const conversation = await api.conversation(id)
      setMessages(conversation.messages)
      // Restore sticky mode from the last assistant turn's requested_mode.
      const lastAssistant = [...conversation.messages]
        .reverse()
        .find((message) => message.role === 'assistant')
      if (lastAssistant?.requested_mode) {
        setSelectedMode(lastAssistant.requested_mode)
      }
    } catch (error) {
      setChatError(error instanceof Error ? error.message : 'Unable to load conversation')
    }
  }

  const newConversation = () => {
    setRerunNotice(false)
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
    setRerunNotice(false)
    setChatError(null)

    const optimisticUser: Message = {
      id: `temp-${Date.now()}`,
      role: 'user',
      content: message,
      requested_mode: selectedMode,
      evidence: [],
      created_at: new Date().toISOString(),
    }
    setMessages((current) => [...current, optimisticUser])

    try {
      const response = await api.chat(message, activeConversationId, selectedMode)
      const assistant: Message = {
        id: response.message_id,
        role: 'assistant',
        content: response.answer,
        execution_class: response.execution_class,
        requested_mode: response.mode,
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

  const createDataSource = async (event: FormEvent) => {
    event.preventDefault()
    if (!sourceName.trim() || !sourceUri.trim() || creatingSource) return

    setCreatingSource(true)
    setDataError(null)
    try {
      const created = await api.createDataSource({
        name: sourceName.trim(),
        engine: sourceEngine,
        connection_uri: sourceUri.trim(),
        enabled: true,
      })
      const test = await api.testDataSource(created.id)
      if (test.ok) {
        await api.refreshDataSource(created.id)
      } else {
        setDataError(test.detail)
      }
      setSourceName('')
      setSourceUri('')
      await loadDataSources()
    } catch (error) {
      setDataError(error instanceof Error ? error.message : 'Unable to connect data source')
    } finally {
      setCreatingSource(false)
    }
  }

  const runSourceAction = async (
    sourceId: string,
    action: 'test' | 'refresh' | 'toggle' | 'delete',
    enabled?: boolean,
  ) => {
    setDataBusyId(sourceId)
    setDataError(null)
    try {
      if (action === 'test') {
        const result = await api.testDataSource(sourceId)
        if (!result.ok) setDataError(result.detail)
      } else if (action === 'refresh') {
        await api.refreshDataSource(sourceId)
      } else if (action === 'toggle') {
        await api.setDataSourceEnabled(sourceId, Boolean(enabled))
      } else if (action === 'delete') {
        await api.deleteDataSource(sourceId)
      }
      await loadDataSources()
    } catch (error) {
      setDataError(error instanceof Error ? error.message : 'Data source action failed')
    } finally {
      setDataBusyId(null)
    }
  }

  const saveReport = async (message: Message) => {
    if (reportBusyId || !message.evidence?.length) return
    setReportBusyId(message.id)
    setChatError(null)
    try {
      const saved = await api.saveReport(message.id)
      setReports((current) => [saved, ...current.filter((report) => report.id !== saved.id)])
      await loadReports()
    } catch (error) {
      setChatError(error instanceof Error ? error.message : 'Unable to save report')
    } finally {
      setReportBusyId(null)
    }
  }

  const isRevocationError = (error: unknown) => (
    error instanceof ApiError
    && error.status === 409
    && /unavailable|authorized|revoked|scope|grant/i.test(error.message)
  )

  const clearReportExecution = () => {
    reportExecutionSequence.current += 1
    setReportDefinitions([])
    setActiveDefinition(null)
    setReportRuns([])
    setActiveRun(null)
    setReportExecutionError(null)
    reportExecutionInFlight.current = false
    pendingRunIntentRef.current = null
    setReportExecutionBusy(false)
    setConfirmingRun(false)
    setPendingRunIntent(null)
  }

  const loadReportExecution = async (reportId: string, sequence = reportExecutionSequence.current) => {
    try {
      const [definitions, runs] = await Promise.all([
        api.reportDefinitions(reportId),
        api.reportRuns(reportId),
      ])
      if (sequence !== reportExecutionSequence.current) return
      setReportDefinitions(definitions)
      setActiveDefinition(definitions[0] || null)
      setReportRuns(runs)
      setReportExecutionError(null)
    } catch (error) {
      if (sequence !== reportExecutionSequence.current) return
      setReportDefinitions([])
      setActiveDefinition(null)
      setReportRuns([])
      setActiveRun(null)
      setReportExecutionError(
        error instanceof Error ? error.message : 'Live report execution information is unavailable',
      )
      if (isRevocationError(error)) {
        setReportError(error instanceof Error ? error.message : 'Report access was revoked')
        setActiveReport(null)
        await loadReports()
      }
    }
  }

  const createReportDefinition = async () => {
    if (!activeReport || reportExecutionBusy) return
    const sequence = reportExecutionSequence.current
    setReportExecutionBusy(true)
    setReportExecutionError(null)
    try {
      const definition = await api.createReportDefinition(activeReport.id)
      if (sequence !== reportExecutionSequence.current) return
      setReportDefinitions([definition])
      setActiveDefinition(definition)
    } catch (error) {
      if (sequence === reportExecutionSequence.current) {
        setReportExecutionError(error instanceof Error ? error.message : 'Unable to create pinned definition')
        if (isRevocationError(error)) {
          setReportError(error instanceof Error ? error.message : 'Report access was revoked')
          setActiveReport(null)
          await loadReports()
        }
      }
    } finally {
      if (sequence === reportExecutionSequence.current) setReportExecutionBusy(false)
    }
  }

  const openReportRun = async (runId: string) => {
    const sequence = reportExecutionSequence.current
    setReportExecutionBusy(true)
    setReportExecutionError(null)
    try {
      const detail = await api.reportRun(runId)
      if (sequence === reportExecutionSequence.current) setActiveRun(detail)
    } catch (error) {
      if (sequence === reportExecutionSequence.current) {
        setActiveRun(null)
        setReportExecutionError(error instanceof Error ? error.message : 'Report run is unavailable')
        if (isRevocationError(error)) {
          setReportError(error instanceof Error ? error.message : 'Report access was revoked')
          setActiveReport(null)
          await loadReports()
        }
      }
    } finally {
      if (sequence === reportExecutionSequence.current) setReportExecutionBusy(false)
    }
  }

  const loadOlderRuns = async () => {
    if (!activeReport || reportExecutionBusy) return
    const sequence = reportExecutionSequence.current
    setReportExecutionBusy(true)
    try {
      const older = await api.reportRuns(activeReport.id, reportRuns.length, 20)
      if (sequence === reportExecutionSequence.current) {
        setReportRuns((current) => [...current, ...older.filter(
          (item) => !current.some((existing) => existing.id === item.id),
        )])
      }
    } catch (error) {
      if (sequence === reportExecutionSequence.current) {
        if (isRevocationError(error)) {
          const message = error instanceof Error ? error.message : 'Report access was revoked'
          setActiveRun(null)
          setActiveReport(null)
          setReportExecutionError(message)
          setReportError(message)
          await loadReports()
        } else {
          setReportExecutionError(error instanceof Error ? error.message : 'Unable to load older runs')
        }
      }
    } finally {
      if (sequence === reportExecutionSequence.current) setReportExecutionBusy(false)
    }
  }

  const submitPinnedRun = async (reusePending = false) => {
    if (!activeReport || !activeDefinition || reportExecutionInFlight.current) return
    const reportId = activeReport.id
    const version = activeDefinition.version
    const existing = pendingRunIntentRef.current
    const intent: ReportRunIntent = (
      reusePending
      && existing
      && existing.reportId === reportId
      && existing.version === version
    ) ? existing : {
      reportId,
      version,
      idempotencyKey: crypto.randomUUID(),
    }
    const sequence = reportExecutionSequence.current

    // Refs close the pre-rerender double-click window. State is for rendering.
    reportExecutionInFlight.current = true
    pendingRunIntentRef.current = intent
    setPendingRunIntent(intent)
    setConfirmingRun(false)
    setReportExecutionBusy(true)
    setReportExecutionError(null)

    try {
      let detail: ReportRunDetail
      try {
        detail = await api.submitReportRun(reportId, version, intent.idempotencyKey)
      } catch (error) {
        if (sequence === reportExecutionSequence.current) {
          if (isRevocationError(error)) {
            const message = error instanceof Error ? error.message : 'Report access was revoked'
            pendingRunIntentRef.current = null
            setPendingRunIntent(null)
            setActiveRun(null)
            setActiveReport(null)
            setReportExecutionError(message)
            setReportError(message)
            await loadReports()
          } else {
            // Preserve the exact key only when the submission response itself is uncertain.
            setReportExecutionError(
              error instanceof Error
                ? `${error.message}. If the response was interrupted, retrying below reuses the same run request.`
                : 'Run response was not confirmed. Retry the same run request.',
            )
          }
        }
        return
      }

      if (sequence !== reportExecutionSequence.current) return
      setActiveRun(detail)
      pendingRunIntentRef.current = null
      setPendingRunIntent(null)

      try {
        const refreshed = await api.reportRuns(reportId)
        if (sequence === reportExecutionSequence.current) setReportRuns(refreshed)
      } catch (error) {
        if (sequence !== reportExecutionSequence.current) return
        if (isRevocationError(error)) {
          const message = error instanceof Error ? error.message : 'Report access was revoked'
          setActiveRun(null)
          setActiveReport(null)
          setReportExecutionError(message)
          setReportError(message)
          await loadReports()
        } else {
          // The run response is authoritative; a later history-refresh failure must
          // never be presented as an uncertain submission or invite key replay.
          setReportExecutionError(
            error instanceof Error
              ? `Run completed, but history could not refresh: ${error.message}`
              : 'Run completed, but history could not refresh.',
          )
        }
      }
    } finally {
      if (sequence === reportExecutionSequence.current) {
        reportExecutionInFlight.current = false
        setReportExecutionBusy(false)
      }
    }
  }

  const openReport = async (id: string) => {
    const requestSequence = ++reportOpenSequence.current
    clearReportExecution()
    const executionSequence = reportExecutionSequence.current
    pendingReportId.current = id
    setActiveReport(null)
    setReportError(null)
    setReportBusyId(id)
    try {
      const report = await api.report(id)
      if (
        requestSequence === reportOpenSequence.current
        && pendingReportId.current === id
      ) {
        setActiveReport(report)
        pendingReportId.current = null
        await loadReportExecution(id, executionSequence)
      }
    } catch (error) {
      // Never render a cached snapshot when server revocation checks fail.
      if (requestSequence === reportOpenSequence.current) {
        pendingReportId.current = null
        setReportError(error instanceof Error ? error.message : 'Report unavailable')
        await loadReports()
      }
    } finally {
      setReportBusyId((current) => current === id ? null : current)
    }
  }

  const deleteReport = async (id: string) => {
    setReportError(null)
    setReportBusyId(id)
    try {
      await api.deleteReport(id)
      if (pendingReportId.current === id) {
        reportOpenSequence.current += 1
        pendingReportId.current = null
      }
      if (activeReport?.id === id) clearReportExecution()
      setActiveReport((current) => current?.id === id ? null : current)
      setReports((current) => current.filter((report) => report.id !== id))
      await loadReports()
    } catch (error) {
      setReportError(error instanceof Error ? error.message : 'Unable to delete report')
    } finally {
      setReportBusyId((current) => current === id ? null : current)
    }
  }

  const prepareReportRerun = async (reportId: string) => {
    if (reportBusyId || sending) return
    setReportBusyId(reportId)
    setReportError(null)
    try {
      // This GET only checks sources and recovers a persisted question. It must
      // never execute a query or submit Chat on the user's behalf.
      const preview = await api.reportRerunPreview(reportId)
      if (!preview.requires_explicit_send || preview.executes_queries) {
        throw new Error('Rerun requires an explicit reviewed Chat request')
      }
      reportOpenSequence.current += 1
      pendingReportId.current = null
      setActiveReport(null)
      setActiveConversationId(null)
      setMessages([])
      setChatError(null)
      setSelectedMode(preview.mode)
      setInput(preview.original_question)
      setRerunNotice(true)
      setView('chat')
      requestAnimationFrame(() => inputRef.current?.focus())
    } catch (error) {
      // A revoked report must never leave a cached answer visible after 409.
      setActiveReport(null)
      setReportError(error instanceof Error ? error.message : 'Rerun preparation unavailable')
      await loadReports()
    } finally {
      setReportBusyId((current) => current === reportId ? null : current)
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
          <button
            className={view === 'data' ? 'nav-item active' : 'nav-item'}
            onClick={() => setView('data')}
          >
            <Database size={17} />
            Data
            <span className="count-pill">{dataSources.length}</span>
          </button>

          <button
            className={view === 'reports' ? 'nav-item active' : 'nav-item'}
            onClick={() => {
              reportOpenSequence.current += 1
              pendingReportId.current = null
              setView('reports')
              setActiveReport(null)
              clearReportExecution()
              setReportError(null)
              loadReports()
            }}
          >
            <Gauge size={17} />
            Reports
            <span className="count-pill">{reports.length}</span>
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
            <span>Knowledge + structured data enabled</span>
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
                    ) : activeExecutionClass === 'structured' ? (
                      <Database size={14} />
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
                    Conversations persist on the server. OpenJM can ground answers in indexed
                    knowledge or authorized read-only business data before the model responds.
                  </p>
                  <div className="prompt-grid">
                    {[
                      'What documents do you have loaded?',
                      'Summarize the key points in my indexed documents.',
                      'What is the total revenue for Blue Mountain Cafe?',
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
                          {message.requested_mode && message.requested_mode !== 'chat' && (
                            <span className={`mode-badge ${message.requested_mode}`}>
                              {MODE_OPTIONS.find((option) => option.value === message.requested_mode)?.label || message.requested_mode}
                            </span>
                          )}
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
                        {message.role === 'assistant' && (message.evidence?.length || 0) > 0 && (
                          <div className="snapshot-actions">
                            <button
                              type="button"
                              disabled={Boolean(reportBusyId) || reports.some((report) => report.message_id === message.id)}
                              onClick={() => saveReport(message)}
                            >
                              {reports.some((report) => report.message_id === message.id)
                                ? 'Report saved'
                                : reportBusyId === message.id ? 'Saving…' : 'Save as report'}
                            </button>
                          </div>
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
              {rerunNotice && (
                <div className="rerun-notice" role="status">
                  Review this historical report question and its original mode before pressing Send.
                  Sending starts a NEW governed conversation with current sources and policies;
                  it will not update the saved snapshot.
                </div>
              )}
              <form className="composer" onSubmit={submitMessage}>
                <div className="mode-selector-wrap">
                  <div className="mode-selector" onClick={() => setModeMenuOpen(!modeMenuOpen)}>
                    <span className="mode-label">{MODE_OPTIONS.find((option) => option.value === selectedMode)?.label || 'Chat'}</span>
                    <ChevronDown size={14} className="mode-chevron" />
                    {modeMenuOpen && (
                      <div className="mode-dropdown">
                        {MODE_OPTIONS.map((option) => (
                          <button
                            key={option.value}
                            type="button"
                            className={selectedMode === option.value ? 'mode-option selected' : 'mode-option'}
                            onClick={() => {
                              setSelectedMode(option.value)
                              setModeMenuOpen(false)
                            }}
                          >
                            {option.label}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
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
                Server-side conversation history • Governed knowledge • Read-only structured data
              </div>
            </div>
          </>
        ) : view === 'knowledge' ? (
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
                    Files uploaded here are parsed, chunked and indexed into OpenJM’s governed
                    knowledge layer. Chat retrieves from this evidence layer automatically.
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
        ) : view === 'reports' ? (
          <>
            <header className="workspace-header">
              <div>
                <div className="eyebrow">Governed evidence snapshots</div>
                <h1>Saved Reports</h1>
              </div>
              <span className="system-badge"><ShieldCheck size={14} /> Read-only snapshots</span>
            </header>
            <section className="reports-stage">
              <div className="reports-explainer">
                Reports preserve previously completed, evidence-backed answers.
                They are not live dashboards and never rerun Knowledge or SQL.
                Opening a report checks current source availability.
              </div>
              {reportError && <div className="error-banner">{reportError}</div>}
              <div className="reports-layout">
                <div className="reports-list">
                  {reports.length === 0 ? (
                    <div className="reports-empty">
                      No reports saved yet. In Chat, choose “Save as report” on an evidence-backed answer.
                    </div>
                  ) : reports.map((report) => (
                    <div className="reports-list-item" key={report.id}>
                      <button
                        className={activeReport?.id === report.id ? 'report-open selected' : 'report-open'}
                        disabled={!report.available || reportBusyId === report.id}
                        onClick={() => openReport(report.id)}
                      >
                        <strong>{report.title}</strong>
                        <small>{new Date(report.snapshot_as_of).toLocaleString()}</small>
                        <small>{report.available ? `${report.source_count} source(s) · Snapshot` : 'Source unavailable'}</small>
                      </button>
                      <button
                        className="icon-button danger"
                        type="button"
                        title="Delete report snapshot"
                        aria-label="Delete report snapshot"
                        disabled={Boolean(reportBusyId)}
                        onClick={() => deleteReport(report.id)}
                      ><Trash2 size={15} /></button>
                    </div>
                  ))}
                </div>
                <div className="report-detail">
                  {activeReport ? (
                    <>
                      <div className="report-as-of">
                        Saved evidence snapshot · As of {new Date(activeReport.snapshot_as_of).toLocaleString()}
                        <br />Not live · No queries executed when viewing
                      </div>
                      <h2>{activeReport.title}</h2>
                      <button
                        className="primary-action"
                        type="button"
                        disabled={Boolean(reportBusyId) || sending}
                        onClick={() => prepareReportRerun(activeReport.id)}
                      >
                        <ShieldCheck size={15} />
                        {reportBusyId === activeReport.id ? 'Checking sources…' : 'Prepare rerun in Chat'}
                      </button>
                      <p className="report-rerun-explainer">
                        Nothing executes until you review the question and press Send in Chat.
                        The saved report remains an unchanged historical snapshot.
                      </p>

                      <section className="report-execution-panel" aria-label="Fresh pinned report run">
                        <div className="report-execution-heading">
                          <div>
                            <span>Fresh governed run</span>
                            <strong>Run the pinned report definition</strong>
                          </div>
                          {activeDefinition && (
                            <span className={activeDefinition.runnable ? 'run-state ready' : 'run-state disabled'}>
                              {activeDefinition.runnable ? 'Ready' : 'Unavailable'}
                            </span>
                          )}
                        </div>

                        {reportExecutionError && (
                          <div className="error-banner report-execution-error">{reportExecutionError}</div>
                        )}

                        {!activeDefinition ? (
                          <div className="definition-empty">
                            <p>
                              Create an immutable definition from this snapshot’s current authorized sources.
                              Creating it does not execute Knowledge, SQL, or a model.
                            </p>
                            <button
                              className="secondary-action"
                              type="button"
                              disabled={reportExecutionBusy}
                              onClick={createReportDefinition}
                            >
                              {reportExecutionBusy ? 'Creating…' : 'Create pinned definition'}
                            </button>
                          </div>
                        ) : (
                          <>
                            {reportDefinitions.length > 1 && (
                              <label className="definition-selector">
                                <span>Definition version</span>
                                <select
                                  value={activeDefinition.id}
                                  onChange={(event) => {
                                    const next = reportDefinitions.find(
                                      (definition) => definition.id === event.target.value,
                                    )
                                    if (!next) return
                                    pendingRunIntentRef.current = null
                                    setPendingRunIntent(null)
                                    setConfirmingRun(false)
                                    setActiveRun(null)
                                    setActiveDefinition(next)
                                  }}
                                >
                                  {reportDefinitions.map((definition) => (
                                    <option key={definition.id} value={definition.id}>
                                      Version {definition.version} · {definition.mode}
                                    </option>
                                  ))}
                                </select>
                              </label>
                            )}
                            <div className="definition-summary">
                              <span>Version {activeDefinition.version}</span>
                              <span>{activeDefinition.mode}</span>
                              <span>
                                {activeDefinition.pinned_document_ids.length} document(s)
                                {' · '}
                                {Object.values(activeDefinition.pinned_source_tables).reduce(
                                  (count, tables) => count + tables.length, 0,
                                )} table(s)
                              </span>
                            </div>
                            <div className="definition-question">{activeDefinition.question}</div>
                            <div className="run-actions">
                              <button
                                className="primary-action"
                                type="button"
                                disabled={!activeDefinition.runnable || reportExecutionBusy || confirmingRun}
                                onClick={() => setConfirmingRun(true)}
                              >
                                {reportExecutionBusy ? 'Running…' : 'Run fresh report'}
                              </button>
                              {!activeDefinition.runnable && (
                                <span>Manual execution is not enabled for this environment.</span>
                              )}
                            </div>

                            {confirmingRun && (
                              <div className="run-confirmation" role="group" aria-label="Confirm fresh report run">
                                <strong>Run this pinned definition now?</strong>
                                <p>
                                  This explicitly starts fresh governed retrieval/model/SQL work.
                                  The historical snapshot above will not change.
                                </p>
                                <div>
                                  <button
                                    type="button"
                                    className="secondary-action"
                                    onClick={() => setConfirmingRun(false)}
                                  >Cancel</button>
                                  <button
                                    type="button"
                                    className="primary-action"
                                    onClick={() => submitPinnedRun(false)}
                                  >Confirm and run</button>
                                </div>
                              </div>
                            )}

                            {pendingRunIntent && !reportExecutionBusy && (
                              <button
                                type="button"
                                className="secondary-action retry-same-run"
                                onClick={() => submitPinnedRun(true)}
                              >
                                Retry same run request
                              </button>
                            )}
                          </>
                        )}

                        <div className="run-history">
                          <div className="run-history-heading">
                            <strong>Immutable run history</strong>
                            <span>{reportRuns.length} loaded</span>
                          </div>
                          {reportRuns.length === 0 ? (
                            <p className="run-history-empty">No fresh runs recorded for this report yet.</p>
                          ) : (
                            reportRuns.map((run) => (
                              <button
                                key={run.id}
                                type="button"
                                className={activeRun?.id === run.id ? 'run-history-item selected' : 'run-history-item'}
                                disabled={reportExecutionBusy}
                                onClick={() => openReportRun(run.id)}
                              >
                                <span>
                                  <strong>{run.status}</strong>
                                  <small>v{run.definition_version} · {run.requested_mode}</small>
                                </span>
                                <span>
                                  <small>{new Date(run.started_at).toLocaleString()}</small>
                                  {run.failure_category && <small>{run.failure_category}</small>}
                                </span>
                              </button>
                            ))
                          )}
                          {reportRuns.length > 0 && reportRuns.length % 20 === 0 && (
                            <button
                              className="secondary-action load-more-runs"
                              type="button"
                              disabled={reportExecutionBusy}
                              onClick={loadOlderRuns}
                            >Load older runs</button>
                          )}
                        </div>

                        {activeRun && (
                          <div className="run-detail">
                            <div className="report-as-of">
                              Fresh pinned run · Started {new Date(activeRun.started_at).toLocaleString()}
                              {activeRun.finished_at && <> · Completed {new Date(activeRun.finished_at).toLocaleString()}</>}
                              <br />Status: {activeRun.status}
                              {activeRun.failure_category && <> · {activeRun.failure_category}</>}
                            </div>
                            {activeRun.status === 'succeeded' && activeRun.result ? (
                              <>
                                <div className="message-content">{activeRun.result.answer}</div>
                                <EvidencePanel evidence={activeRun.result.evidence} />
                              </>
                            ) : (
                              <div className="run-terminal-state">
                                {activeRun.status === 'running'
                                  ? 'This run is still in progress on the server.'
                                  : 'This run has no deliverable result. Start a new run only with another explicit confirmation.'}
                              </div>
                            )}
                          </div>
                        )}
                      </section>

                      <div className="snapshot-divider"><span>Historical saved snapshot</span></div>
                      <div className="message-content">{activeReport.answer}</div>
                      <EvidencePanel evidence={activeReport.evidence} />
                    </>
                  ) : (
                    <div className="reports-empty">
                      Select an available report to view its historical answer and citations.
                    </div>
                  )}
                </div>
              </div>
            </section>
          </>
) : (
          <>
            <header className="workspace-header">
              <div>
                <div className="eyebrow">Governed structured data</div>
                <h1>Data</h1>
              </div>
              <span className="system-badge">
                <ShieldCheck size={14} />
                Read-only execution
              </span>
            </header>

            <section className="data-stage">
              <div className="data-intro">
                <div>
                  <div className="eyebrow">Authorized sources</div>
                  <h2>Connect business data to OpenJM</h2>
                  <p>
                    OpenJM stores source credentials encrypted, discovers schema metadata, and
                    executes only validated read-only queries through its governed data layer.
                    Saved credentials are never returned to the browser.
                  </p>
                </div>
                <div className="data-metrics">
                  <div>
                    <span>Sources</span>
                    <strong>{dataSources.length}</strong>
                  </div>
                  <div>
                    <span>Connected</span>
                    <strong>{dataSources.filter((source) => source.status === 'connected').length}</strong>
                  </div>
                </div>
              </div>

              {dataError && <div className="error-banner data-error">{dataError}</div>}

              <form className="source-form" onSubmit={createDataSource}>
                <div className="source-form-heading">
                  <div>
                    <strong>Add data source</strong>
                    <span>SQLite and PostgreSQL are supported in Vertical Slice 2.</span>
                  </div>
                </div>
                <label>
                  <span>Display name</span>
                  <input
                    value={sourceName}
                    onChange={(event) => setSourceName(event.target.value)}
                    placeholder="e.g. Finance warehouse"
                    required
                  />
                </label>
                <label>
                  <span>Engine</span>
                  <select
                    value={sourceEngine}
                    onChange={(event) => setSourceEngine(event.target.value as 'sqlite' | 'postgresql')}
                  >
                    <option value="sqlite">SQLite</option>
                    <option value="postgresql">PostgreSQL</option>
                  </select>
                </label>
                <label className="source-uri-field">
                  <span>Connection URI</span>
                  <input
                    type="password"
                    autoComplete="new-password"
                    value={sourceUri}
                    onChange={(event) => setSourceUri(event.target.value)}
                    placeholder={sourceEngine === 'sqlite' ? 'sqlite:///data/client.db' : 'postgresql://user:password@host/database'}
                    required
                  />
                  <small>The URI is encrypted server-side and is not displayed again after submission.</small>
                </label>
                <button className="primary-action source-submit" type="submit" disabled={creatingSource}>
                  <Plus size={16} />
                  {creatingSource ? 'Connecting…' : 'Connect source'}
                </button>
              </form>

              <div className="source-list">
                {dataSources.length === 0 ? (
                  <div className="data-empty">
                    <Database size={30} />
                    <strong>No data sources connected</strong>
                    <span>Add an authorized relational source to enable grounded structured questions in Chat.</span>
                  </div>
                ) : (
                  dataSources.map((source) => (
                    <article className="source-card" key={source.id}>
                      <div className="source-card-header">
                        <div className="source-identity">
                          <div className="source-icon"><Database size={18} /></div>
                          <div>
                            <strong>{source.name}</strong>
                            <span>{source.engine === 'postgresql' ? 'PostgreSQL' : 'SQLite'} · {source.tables.length} table{source.tables.length === 1 ? '' : 's'}</span>
                          </div>
                        </div>
                        <div className="source-state">
                          <span className={source.enabled ? 'source-enabled' : 'source-disabled'}>
                            {source.enabled ? 'Enabled' : 'Disabled'}
                          </span>
                          <span className={`source-status ${source.status}`}>{source.status}</span>
                        </div>
                      </div>

                      {source.last_error && <div className="source-error">{source.last_error}</div>}

                      <div className="source-actions">
                        <button
                          onClick={() => runSourceAction(source.id, 'test')}
                          disabled={dataBusyId === source.id}
                        >
                          Test connection
                        </button>
                        <button
                          onClick={() => runSourceAction(source.id, 'refresh')}
                          disabled={dataBusyId === source.id}
                        >
                          Refresh schema
                        </button>
                        <button
                          onClick={() => runSourceAction(source.id, 'toggle', !source.enabled)}
                          disabled={dataBusyId === source.id}
                        >
                          {source.enabled ? 'Disable' : 'Enable'}
                        </button>
                        <button
                          className="danger-text"
                          onClick={() => runSourceAction(source.id, 'delete')}
                          disabled={dataBusyId === source.id}
                        >
                          Delete
                        </button>
                      </div>

                      <div className="source-schema">
                        {source.tables.length === 0 ? (
                          <span className="schema-empty">No schema discovered yet. Test the connection and refresh schema.</span>
                        ) : (
                          source.tables.map((table) => (
                            <details className="schema-table-card" key={table.qualified_name}>
                              <summary>
                                <span>{table.qualified_name}</span>
                                <small>{table.columns.length} columns</small>
                                <ChevronRight size={14} className="summary-chevron" />
                              </summary>
                              <div className="schema-columns">
                                {table.columns.map((column) => (
                                  <div key={column.name}>
                                    <code>{column.name}</code>
                                    <span>{column.type}</span>
                                    {column.primary_key && <strong>PK</strong>}
                                  </div>
                                ))}
                              </div>
                            </details>
                          ))
                        )}
                      </div>
                    </article>
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
