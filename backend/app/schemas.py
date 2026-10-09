from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


ExecutionMode = Literal["chat", "knowledge", "data", "hybrid"]

# Internal execution classes. The user-facing mode (chat/knowledge/data/hybrid)
# maps onto these authoritative execution classes:
#   chat      -> general
#   knowledge -> knowledge
#   data      -> structured
#   hybrid    -> hybrid
ExecutionClass = Literal["general", "knowledge", "structured", "hybrid"]


class Evidence(BaseModel):
    source_type: str = "document"
    source_id: str
    title: str
    passage: str
    score: float | None = None
    evidence_id: str | None = None
    provenance: dict = Field(default_factory=dict)
    access_context: dict = Field(default_factory=dict)
    processing_location: str | None = None
    observed_at: datetime | None = None
    metadata: dict = Field(default_factory=dict)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=12000)
    conversation_id: str | None = None
    mode: ExecutionMode = "chat"


class ChatResponse(BaseModel):
    conversation_id: str
    message_id: str
    answer: str
    execution_class: ExecutionClass
    mode: ExecutionMode = "chat"
    evidence: list[Evidence] = Field(default_factory=list)


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    execution_class: str | None = None
    requested_mode: ExecutionMode | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    created_at: datetime


class ConversationOut(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]


class DocumentOut(BaseModel):
    id: str
    original_name: str
    mime_type: str | None
    size_bytes: int
    status: str
    indexed: bool
    created_at: datetime
    # BV1-B governed source classification (safe defaults for older clients).
    classification: str = "internal"
    department_id: str | None = None
    tenant_visible: bool = True


class IngestResponse(DocumentOut):
    pass



DataEngine = Literal["sqlite", "postgresql"]


class DataSourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=240)
    engine: DataEngine
    connection_uri: str = Field(min_length=1, max_length=4000)
    revenue_currency: Literal["USD", "JMD"] | None = None
    enabled: bool = True


class DataSourceEnabledUpdate(BaseModel):
    enabled: bool


class DataSourceCurrencyUpdate(BaseModel):
    revenue_currency: Literal["USD", "JMD"] | None


class DataColumnSchema(BaseModel):
    name: str
    type: str
    nullable: bool
    primary_key: bool = False


class DataForeignKeySchema(BaseModel):
    constrained_columns: list[str] = Field(default_factory=list)
    referred_schema: str | None = None
    referred_table: str
    referred_columns: list[str] = Field(default_factory=list)


class DataTableSchema(BaseModel):
    schema_name: str
    name: str
    qualified_name: str
    columns: list[DataColumnSchema] = Field(default_factory=list)
    primary_key: list[str] = Field(default_factory=list)
    foreign_keys: list[DataForeignKeySchema] = Field(default_factory=list)


class DataSourceOut(BaseModel):
    id: str
    name: str
    engine: DataEngine
    revenue_currency: Literal["USD", "JMD"] | None = None
    status: str
    enabled: bool
    tables: list[DataTableSchema] = Field(default_factory=list)
    last_error: str | None = None
    last_schema_refresh: datetime | None = None
    created_at: datetime
    updated_at: datetime
    # BV1-C governed structured source classification (safe defaults).
    classification: str = "internal"
    department_id: str | None = None
    tenant_visible: bool = True


class DataSourceTestResult(BaseModel):
    source_id: str
    status: str
    ok: bool
    detail: str


class DataSourceSchemaRefreshResult(BaseModel):
    source: DataSourceOut
    table_count: int


# VS4-A: snapshots contain persisted Evidence only, never executable query templates.
class SaveReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(min_length=1, max_length=36)
    title: str | None = Field(default=None, min_length=1, max_length=160)


class SavedReportSummary(BaseModel):
    id: str
    title: str
    conversation_id: str
    message_id: str
    execution_class: str
    snapshot_as_of: datetime
    created_at: datetime
    source_count: int
    available: bool


class SavedReportDetail(SavedReportSummary):
    answer: str
    evidence: list[Evidence]
    is_live: Literal[False] = False


class ReportRerunPreview(BaseModel):
    """Read-only proposal, NOT a capability grant or an executed report."""

    report_id: str
    source_message_id: str
    original_question: str
    mode: Literal["knowledge", "data", "hybrid"]
    snapshot_as_of: datetime
    original_source_count: int
    requires_explicit_send: Literal[True] = True
    executes_queries: Literal[False] = False


# VS4-B2A: identity and read-only definition of a bounded, source-pinned report.
# This is deliberately not a report execution request or a SQL template.
class CreateReportDefinitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReportDefinitionVersionOut(BaseModel):
    id: str
    report_id: str
    version: int
    question: str
    mode: Literal["knowledge", "data", "hybrid"]
    pinned_document_ids: list[str]
    pinned_source_tables: dict[str, list[str]]
    created_at: datetime
    executes_queries: Literal[False] = False
    runnable: bool = False


# VS4-B2C1: owner-scoped, read-only report-run history. These never grant
# execution authority; they only describe an already-persisted (or reserved)
# run and, for a succeeded run, its bounded persisted result envelope.
class ReportRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    structured_result: dict = Field(default_factory=dict)
    trace_ids: list[str] = Field(default_factory=list)


class ReportRunSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    report_id: str
    definition_version: int
    requested_mode: str
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    failure_category: str | None = None
    result_size_bytes: int | None = None
    trace_count: int = 0


class ReportRunDetail(ReportRunSummary):
    result: ReportRunResult | None = None


# VS4-B2C2 Phase 1: the client supplies only a canonical reservation token.
class CreateReportRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(
        strict=True,
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    )


# --- BV5-A: Chat artifacts ---------------------------------------------------
# A Chat artifact is a downloadable work product. It is NOT a Governed Saved
# Report: the response types below hard-code ``approved``/``authoritative`` as
# ``False`` so no Chat artifact can ever be presented as approved or
# authoritative, even when it carries evidence-backed provenance.
ArtifactFormat = Literal["html", "markdown", "text", "csv"]


class CreateChatArtifactRequest(BaseModel):
    """Persist one downloadable Chat work product.

    ``content`` is inert data: it is stored verbatim and served only as an
    attachment. It is never executed, interpreted as a template, or rendered
    inline in the application origin. It carries no execution authority.
    """

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    format: ArtifactFormat
    content: str = Field(min_length=1, max_length=4_000_000)
    conversation_id: str | None = Field(default=None, max_length=36)
    message_id: str | None = Field(default=None, max_length=36)
    evidence: list[Evidence] = Field(default_factory=list)


class ChatArtifactOut(BaseModel):
    id: str
    title: str
    filename: str
    mime_type: str
    artifact_format: ArtifactFormat
    size_bytes: int
    state: str
    is_evidence_backed: bool
    conversation_id: str | None = None
    message_id: str | None = None
    created_at: datetime


class ChatArtifactDetail(ChatArtifactOut):
    # Metadata-only provenance (citations + as-of). Never an approval.
    provenance: dict = Field(default_factory=dict)
    approved: Literal[False] = False
    authoritative: Literal[False] = False
