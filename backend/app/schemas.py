from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


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
