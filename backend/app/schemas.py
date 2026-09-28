from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


ExecutionClass = Literal["general", "knowledge"]


class Evidence(BaseModel):
    source_type: str = "document"
    source_id: str
    title: str
    passage: str
    score: float | None = None
    metadata: dict = Field(default_factory=dict)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=12000)
    conversation_id: str | None = None


class ChatResponse(BaseModel):
    conversation_id: str
    message_id: str
    answer: str
    execution_class: ExecutionClass
    evidence: list[Evidence] = Field(default_factory=list)


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    execution_class: str | None = None
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
