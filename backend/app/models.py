from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    title: Mapped[str] = mapped_column(String(240), default="New conversation")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc
    )

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    execution_class: Mapped[str | None] = mapped_column(String(32), nullable=True)
    requested_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    evidence_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    original_name: Mapped[str] = mapped_column(String(500))
    stored_path: Mapped[str] = mapped_column(String(1000))
    mime_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="indexing")
    indexed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)



class DataSource(Base):
    __tablename__ = "data_sources"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    name: Mapped[str] = mapped_column(String(240))
    engine: Mapped[str] = mapped_column(String(32))
    connection_secret: Mapped[str] = mapped_column(Text)
    revenue_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="untested")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    schema_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    authorized_objects_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_schema_refresh: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc
    )



class ExecutionTrace(Base):
    __tablename__ = "execution_traces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    request_id: Mapped[str] = mapped_column(String(36), index=True, default=new_id)
    tool_invocation_id: Mapped[str] = mapped_column(String(36), default=new_id)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    route: Mapped[str | None] = mapped_column(String(32), nullable=True)
    requested_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    tool_name: Mapped[str] = mapped_column(String(128), index=True)
    operation_class: Mapped[str] = mapped_column(String(32))
    risk_level: Mapped[str] = mapped_column(String(32))
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    source_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(240), nullable=True)
    input_hash: Mapped[str] = mapped_column(String(64))
    planned_sql: Mapped[str | None] = mapped_column(Text, nullable=True)
    executed_sql: Mapped[str | None] = mapped_column(Text, nullable=True)
    validation_decision: Mapped[str | None] = mapped_column(String(64), nullable=True)
    policy_decision: Mapped[str | None] = mapped_column(String(64), nullable=True)
    row_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="started")
    error_class: Mapped[str | None] = mapped_column(String(240), nullable=True)
    evidence_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    processing_location: Mapped[str | None] = mapped_column(String(128), nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class SavedReport(Base):
    """Read-only persisted snapshot; never a stored executable SQL plan."""

    __tablename__ = "saved_reports"
    __table_args__ = (UniqueConstraint("user_id", "message_id", name="uq_saved_report_owner_message"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    message_id: Mapped[str] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), index=True, nullable=False
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    answer_text: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_json: Mapped[str] = mapped_column(Text, nullable=False)
    execution_class: Mapped[str] = mapped_column(String(32), nullable=False)
    requested_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    source_count: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ReportDefinitionVersion(Base):
    """Immutable scoped definition. No SQL, model output or execution authority."""

    __tablename__ = "report_definition_versions"
    __table_args__ = (
        UniqueConstraint("report_id", "version", name="uq_report_definition_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    report_id: Mapped[str] = mapped_column(
        ForeignKey("saved_reports.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    requested_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    pinned_document_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    pinned_source_tables_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ReportRun(Base):
    """VS4-B2C1: immutable execution history row. No SQL/model output authority.

    Intent and identity are immutable. Lifecycle transitions are monotonic:
    running -> succeeded|failed|interrupted. Terminal result/evidence are
    written once. The internal reserve/finalize API is exercised only by tests
    in this batch; no public submission endpoint exists yet.
    """

    __tablename__ = "report_runs"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_report_run_owner_key"),
        UniqueConstraint("id", name="uq_report_run_id"),
        CheckConstraint(
            "status IN ('running','succeeded','failed','interrupted')",
            name="ck_report_run_status",
        ),
        CheckConstraint(
            "(status = 'running') = (finished_at IS NULL)",
            name="ck_report_run_running_unfinished",
        ),
        CheckConstraint(
            "NOT (status = 'running' AND failure_category IS NOT NULL)",
            name="ck_report_run_running_no_failure",
        ),
        CheckConstraint(
            "(status IN ('succeeded','failed','interrupted')) = (finished_at IS NOT NULL)",
            name="ck_report_run_terminal_finished",
        ),
        CheckConstraint(
            "(status = 'succeeded') = (result_json IS NOT NULL)",
            name="ck_report_run_succeeded_has_result",
        ),
        CheckConstraint(
            "(status IN ('failed','interrupted')) = (failure_category IS NOT NULL)",
            name="ck_report_run_failed_has_category",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    report_id: Mapped[str] = mapped_column(
        ForeignKey("saved_reports.id", ondelete="CASCADE"), index=True, nullable=False
    )
    definition_id: Mapped[str] = mapped_column(
        ForeignKey("report_definition_versions.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    definition_version: Mapped[int] = mapped_column(Integer, nullable=False)
    requested_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failure_category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    trace_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
