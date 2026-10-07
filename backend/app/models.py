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

from app.core.governance import DEFAULT_SOURCE_CLASSIFICATION
from app.core.tenancy import (
    DOC_STATE_FAILED,
    DOC_STATE_INDEXING,
    DOC_STATE_PENDING,
    DOC_STATE_READY,
    LEGACY_TENANT_ID,
)
from app.db import Base


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


# Every tenant-owned table carries this column. The Python-side default keeps
# direct ORM inserts (tests, fixtures, migrations) valid without forcing every
# caller to thread an explicit tenant; the API layer always sets it explicitly
# from the trusted principal context.
def tenant_column():
    return mapped_column(
        String(36), index=True, nullable=False, default=LEGACY_TENANT_ID
    )


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_column()
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


def _derive_lifecycle_state(context) -> str:
    """Derive the lifecycle state when a caller does not set one.

    ``lifecycle_state`` is the authoritative retrieval gate. Rows written by
    callers that still set only the legacy ``status``/``indexed`` pair (the
    VS1-VS4 surfaces, and the migration backfill for pre-VS5 deployments) must
    not silently land in ``pending``, or a ready document would become
    unretrievable. Deriving the state here keeps the two representations in
    agreement at insert time.
    """
    params = context.get_current_parameters()
    status = params.get("status")
    if status == "ready" and params.get("indexed"):
        return DOC_STATE_READY
    if status == "failed":
        return DOC_STATE_FAILED
    if status in ("indexing", "processing"):
        return DOC_STATE_INDEXING
    return DOC_STATE_PENDING


class Document(Base):
    """Knowledge document with an explicit, monotonic lifecycle (#6).

    ``status``/``indexed`` are the legacy VS1 fields kept in sync for the
    existing acceptance surfaces. ``lifecycle_state`` is the authoritative
    state used by the hardened lifecycle and by every retrieval authorization
    check: a document is only retrievable when ``lifecycle_state == 'ready'``,
    ``indexed`` is true and ``deleted_at`` is null.
    """

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_column()
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    original_name: Mapped[str] = mapped_column(String(500))
    stored_path: Mapped[str] = mapped_column(String(1000))
    mime_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="indexing")
    indexed: Mapped[bool] = mapped_column(Boolean, default=False)
    # --- #6 lifecycle hardening ---
    lifecycle_state: Mapped[str] = mapped_column(
        String(32), default=_derive_lifecycle_state, server_default=DOC_STATE_PENDING
    )
    lifecycle_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Identifies the ingestion attempt allowed to publish this document. A
    # crashed or superseded attempt holds a stale token and cannot commit.
    ingest_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    indexed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # --- BV1-B governed source classification ---
    classification: Mapped[str] = mapped_column(
        String(32),
        default=DEFAULT_SOURCE_CLASSIFICATION,
        server_default=DEFAULT_SOURCE_CLASSIFICATION,
        nullable=False,
    )
    department_id: Mapped[str | None] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL"), index=True, nullable=True
    )
    # The tenant-wide fallback. Only the broad classifications may rely on it.
    tenant_visible: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="1", nullable=False
    )
    allowed_group_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class DocumentLease(Base):
    """Cross-process compare-and-swap lease for one document (#6).

    Acquired with a conditional UPDATE / first INSERT so two processes cannot
    both hold it. A lease is always time-bounded: an expired lease can be
    stolen, which is what makes crash recovery deterministic.
    """

    __tablename__ = "document_leases"

    document_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    holder_token: Mapped[str] = mapped_column(String(36), nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    purpose: Mapped[str | None] = mapped_column(String(32), nullable=True)


class DataSource(Base):
    __tablename__ = "data_sources"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_column()
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
    # --- BV1-C governed structured source classification ---
    classification: Mapped[str] = mapped_column(
        String(32),
        default=DEFAULT_SOURCE_CLASSIFICATION,
        server_default=DEFAULT_SOURCE_CLASSIFICATION,
        nullable=False,
    )
    department_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    tenant_visible: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="1", nullable=False
    )
    allowed_group_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc
    )



class ExecutionTrace(Base):
    __tablename__ = "execution_traces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_column()
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
    tenant_id: Mapped[str] = tenant_column()
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
    tenant_id: Mapped[str] = tenant_column()
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
    tenant_id: Mapped[str] = tenant_column()
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


# ---------------------------------------------------------------------------
# VS5 — trusted identity, tenancy and audit
# ---------------------------------------------------------------------------


class Tenant(Base):
    """An isolation boundary. Every owned row carries its ``tenant_id``."""

    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class PrincipalAccount(Base):
    """The OpenJM-owned user record for one OIDC/local subject.

    A principal is tenant-agnostic: tenant scope comes from an active
    :class:`TenantMembership`. ``subject`` is the stable external identifier
    (OIDC ``sub``) used to resolve a validated token to a local account.
    """

    __tablename__ = "principal_accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    subject: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    issuer: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(240), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc
    )


class TenantMembership(Base):
    """Grants one principal a role inside one tenant.

    Re-reading this row on every request is what makes role changes and
    membership revocation take effect immediately, without waiting for any
    token or cache to expire.
    """

    __tablename__ = "tenant_memberships"
    __table_args__ = (
        UniqueConstraint("tenant_id", "principal_id", name="uq_membership_tenant_principal"),
        CheckConstraint(
            "status IN ('active','revoked')", name="ck_membership_status"
        ),
        CheckConstraint(
            "role IN ('viewer','editor','admin','owner')", name="ck_membership_role"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    principal_id: Mapped[str] = mapped_column(
        ForeignKey("principal_accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AuthSession(Base):
    """An OpenJM-native session token minted after a successful OIDC login.

    Only a SHA-256 digest of the opaque token is stored. ``revoked_at`` and
    ``expires_at`` are evaluated on every request so a revoked session stops
    working immediately.
    """

    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    principal_id: Mapped[str] = mapped_column(
        ForeignKey("principal_accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    auth_method: Mapped[str] = mapped_column(String(24), default="oidc", nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AuditRecord(Base):
    """Append-only record of an authorization decision or governed action."""

    __tablename__ = "audit_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    principal_id: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    action: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(240), nullable=True)
    auth_method: Mapped[str | None] = mapped_column(String(24), nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


# ---------------------------------------------------------------------------
# BV1-A: authorization and information-governance foundation
# ---------------------------------------------------------------------------


class Department(Base):
    """A client-defined business domain (HR, Finance, Operations, ...).

    Tenant-scoped, and referenced by id rather than by display name or email
    string. Departments are the grouping layer a data steward's delegated
    authority and, from BV1-B/C, a source's policy are expressed against.
    """

    __tablename__ = "departments"
    __table_args__ = (
        UniqueConstraint("tenant_id", "slug", name="uq_department_tenant_slug"),
        CheckConstraint("status IN ('active','archived')", name="ck_department_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc
    )


class AccessGroup(Base):
    """A client-defined authorization group inside one tenant.

    The unit source policy is granted to from BV1-B/C onwards. A group may belong
    to a department or stand alone as a cross-cutting group. The class is named
    ``AccessGroup`` (table ``access_groups``) to keep the authorization meaning
    explicit rather than an arbitrary grouping.
    """

    __tablename__ = "access_groups"
    __table_args__ = (
        UniqueConstraint("tenant_id", "slug", name="uq_access_group_tenant_slug"),
        CheckConstraint("status IN ('active','archived')", name="ck_access_group_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    department_id: Mapped[str | None] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL"), index=True, nullable=True
    )
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc
    )


class GroupMembership(Base):
    """Grants one principal membership of one group inside one tenant.

    Re-reading this row on every request is what makes group membership and its
    revocation take effect on the next authorized operation, with no cache to
    expire.
    """

    __tablename__ = "group_memberships"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "group_id", "principal_id", name="uq_group_membership_principal"
        ),
        CheckConstraint("status IN ('active','revoked')", name="ck_group_membership_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    group_id: Mapped[str] = mapped_column(
        ForeignKey("access_groups.id", ondelete="CASCADE"), index=True, nullable=False
    )
    principal_id: Mapped[str] = mapped_column(
        ForeignKey("principal_accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


# The capability vocabulary and its DB check constraint must never drift; this
# tuple is the single literal list shared by the model and the 0008 migration.
PLATFORM_OPERATOR_CAPABILITIES_SQL = (
    "('platform:metadata:read','platform:tenants:admin',"
    "'platform:operators:admin','platform:content:support')"
)


class PlatformOperator(Base):
    """One explicit platform capability granted to one principal account.

    Platform authority is never derived from a tenant role. ``capability`` is one
    of :class:`app.core.platform.PlatformCapability`; ``expires_at`` allows a
    support grant to be time bound while metadata grants stay open ended.
    """

    __tablename__ = "platform_operators"
    __table_args__ = (
        UniqueConstraint(
            "principal_id", "capability", name="uq_platform_operator_capability"
        ),
        CheckConstraint(
            f"capability IN {PLATFORM_OPERATOR_CAPABILITIES_SQL}",
            name="ck_platform_operator_capability",
        ),
        CheckConstraint("status IN ('active','revoked')", name="ck_platform_operator_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    principal_id: Mapped[str] = mapped_column(
        ForeignKey("principal_accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    capability: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    granted_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class DataSteward(Base):
    """A delegated data-steward capability over one tenant-scoped scope.

    ``scope_type`` is one of ``tenant``/``department``/``group`` and ``scope_id``
    is the id of that scope (the tenant id itself for a tenant-wide steward).
    Stewardship governs source curation; it is not a tenant role and does not
    grant platform authority.
    """

    __tablename__ = "data_stewards"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "principal_id",
            "scope_type",
            "scope_id",
            name="uq_data_steward_scope",
        ),
        CheckConstraint(
            "scope_type IN ('tenant','department','group')", name="ck_data_steward_scope_type"
        ),
        CheckConstraint("status IN ('active','revoked')", name="ck_data_steward_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    principal_id: Mapped[str] = mapped_column(
        ForeignKey("principal_accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    scope_type: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    granted_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


# ---------------------------------------------------------------------------
# VS6 — bounded action / agent runtime
# ---------------------------------------------------------------------------


class ActionPlan(Base):
    """A server-bounded, deterministic plan proposed by the model.

    The plan records the exact permission context it was created under so that
    execution can prove nothing changed between planning and acting.
    """

    __tablename__ = "action_plans"
    __table_args__ = (
        CheckConstraint(
            "status IN ('proposed','approved','executing','succeeded','failed','rejected','expired')",
            name="ck_action_plan_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    principal_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    goal_text: Mapped[str] = mapped_column(Text, nullable=False)
    steps_json: Mapped[str] = mapped_column(Text, nullable=False)
    max_steps: Mapped[int] = mapped_column(Integer, nullable=False)
    step_count: Mapped[int] = mapped_column(Integer, nullable=False)
    budget_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="proposed", nullable=False)
    plan_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    permissions_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    planner_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ActionApproval(Base):
    """A human approval bound to one exact action fingerprint.

    Changing any approved parameter changes the fingerprint, so a stale
    approval can never authorize a materially different action.
    """

    __tablename__ = "action_approvals"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','approved','rejected','consumed','expired')",
            name="ck_action_approval_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("action_plans.id", ondelete="CASCADE"), index=True, nullable=False
    )
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_name: Mapped[str] = mapped_column(String(120), nullable=False)
    action_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    requested_by: Mapped[str] = mapped_column(String(128), nullable=False)
    decided_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    reason: Mapped[str | None] = mapped_column(String(240), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ActionExecution(Base):
    """Audit row for one executed (or refused) plan step."""

    __tablename__ = "action_executions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_action_execution_idempotency"
        ),
        CheckConstraint(
            "status IN ('started','succeeded','failed','refused','dry_run')",
            name="ck_action_execution_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    plan_id: Mapped[str | None] = mapped_column(
        ForeignKey("action_plans.id", ondelete="CASCADE"), index=True, nullable=True
    )
    step_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    principal_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tool_name: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    operation_class: Mapped[str] = mapped_column(String(16), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False)
    arguments_json: Mapped[str] = mapped_column(Text, nullable=False)
    arguments_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    approval_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="started", nullable=False)
    failure_category: Mapped[str | None] = mapped_column(String(48), nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


# ---------------------------------------------------------------------------
# VS7 — governed connectors, external resources and operational workflows
#
# Every table here is tenant-owned. A connector instance, its credential, its
# cached external resources and its schedules are all scoped by ``tenant_id``,
# and a row belonging to one tenant is indistinguishable from a missing row to
# any other tenant.
# ---------------------------------------------------------------------------


CONNECTOR_STATUS_CONFIGURED = "configured"
CONNECTOR_STATUS_ACTIVE = "active"
CONNECTOR_STATUS_DISABLED = "disabled"
CONNECTOR_STATUS_ERROR = "error"
CONNECTOR_STATUS_DISCONNECTED = "disconnected"

# External-resource lifecycle. ``quarantined`` is the interesting one: the
# content is still physically cached, but it must not be retrievable because
# current authorization could not be proven.
EXTERNAL_STATE_ACTIVE = "active"
EXTERNAL_STATE_QUARANTINED = "quarantined"
EXTERNAL_STATE_DELETED = "deleted"

EXTERNAL_PERMISSION_ALLOWED = "allowed"
EXTERNAL_PERMISSION_REVOKED = "revoked"
EXTERNAL_PERMISSION_UNKNOWN = "unknown"


class ConnectorInstance(Base):
    """One tenant-scoped configured connection to an external system.

    ``config_json`` holds only non-secret configuration (base URL, scope,
    options). Credential material lives in :class:`ConnectorCredential` as
    ciphertext and is referenced, never copied.
    """

    __tablename__ = "connector_instances"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_connector_instance_tenant_name"),
        CheckConstraint(
            "status IN ('configured','active','disabled','error','disconnected')",
            name="ck_connector_instance_status",
        ),
        CheckConstraint(
            "health_status IN ('unknown','healthy','unhealthy')",
            name="ck_connector_instance_health",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_column()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    connector_type: Mapped[str] = mapped_column(String(64), nullable=False)
    connector_version: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str] = mapped_column(String(240), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default=CONNECTOR_STATUS_CONFIGURED, nullable=False
    )
    # Distinct from status: an instance can be configured and enabled but still
    # unhealthy. Disabling is the authorization-relevant switch.
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    config_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    credential_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    health_status: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    health_detail: Mapped[str | None] = mapped_column(String(240), nullable=True)
    last_successful_connection_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_successful_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_reconciliation_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_failure_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_failure_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc
    )


class ConnectorCredential(Base):
    """A tenant-bound credential for one connector instance.

    Only the Fernet ciphertext is stored. Rotation adds a new active row and
    supersedes the previous one, so a connector instance never has to be
    recreated. Revocation sets ``revoked_at``, and resolution refuses to hand
    back a revoked credential, which is what makes revocation fail closed.
    """

    __tablename__ = "connector_credentials"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active','superseded','revoked')", name="ck_connector_credential_status"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_column()
    connector_instance_id: Mapped[str] = mapped_column(
        ForeignKey("connector_instances.id", ondelete="CASCADE"), index=True, nullable=False
    )
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    secret_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class ExternalResource(Base):
    """A connector-owned external resource, normalized.

    ``resource_namespace`` namespaces the identity of every row so a connector
    resource can never collide with an uploaded document, a structured data
    source, another connector, or another tenant. ``document_id`` links the row
    to the Knowledge document that carries its content through the accepted
    VS1 document lifecycle; the content itself always lives there.
    """

    __tablename__ = "external_resources"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "connector_instance_id",
            "resource_namespace",
            "external_id",
            name="uq_external_resource_identity",
        ),
        CheckConstraint(
            "lifecycle_state IN ('active','quarantined','deleted')",
            name="ck_external_resource_lifecycle",
        ),
        CheckConstraint(
            "permission_state IN ('allowed','revoked','unknown')",
            name="ck_external_resource_permission",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_column()
    connector_instance_id: Mapped[str] = mapped_column(
        ForeignKey("connector_instances.id", ondelete="CASCADE"), index=True, nullable=False
    )
    resource_namespace: Mapped[str] = mapped_column(String(120), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    external_revision: Mapped[str | None] = mapped_column(String(128), nullable=True)
    external_parent_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lifecycle_state: Mapped[str] = mapped_column(
        String(32), default=EXTERNAL_STATE_ACTIVE, nullable=False
    )
    permission_state: Mapped[str] = mapped_column(
        String(32), default=EXTERNAL_PERMISSION_UNKNOWN, nullable=False
    )
    document_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    source_metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    provenance_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_reconciled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    quarantine_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    quarantined_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc
    )


class ConnectorCursor(Base):
    """A persisted checkpoint for one stream of one connector instance.

    Cursors are opaque provider values. ``last_event_id`` supports deterministic
    deduplication of overlapping reads without depending on the cursor format.
    """

    __tablename__ = "connector_cursors"
    __table_args__ = (
        UniqueConstraint(
            "connector_instance_id", "stream", name="uq_connector_cursor_stream"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_column()
    connector_instance_id: Mapped[str] = mapped_column(
        ForeignKey("connector_instances.id", ondelete="CASCADE"), index=True, nullable=False
    )
    stream: Mapped[str] = mapped_column(String(64), nullable=False)
    cursor: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_event_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc
    )


class ConnectorSyncRun(Base):
    """Operator-visible record of one sync, reconcile or lifecycle execution.

    Counters and a safe failure category only. Provider error text is sanitized
    before it is stored, so a provider echoing a secret in an error cannot leak
    it into operational state.
    """

    __tablename__ = "connector_sync_runs"
    __table_args__ = (
        CheckConstraint(
            "run_type IN ('initial','incremental','reconcile','test','purge')",
            name="ck_connector_sync_run_type",
        ),
        CheckConstraint(
            "status IN ('running','succeeded','failed','skipped')",
            name="ck_connector_sync_run_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_column()
    connector_instance_id: Mapped[str] = mapped_column(
        ForeignKey("connector_instances.id", ondelete="CASCADE"), index=True, nullable=False
    )
    run_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="running", nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    items_scanned: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    items_created: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    items_updated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    items_deleted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    items_quarantined: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    items_skipped: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cursor_before: Mapped[str | None] = mapped_column(Text, nullable=True)
    cursor_after: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[str | None] = mapped_column(String(500), nullable=True)


class WorkspaceUserMapping(Base):
    """An explicit, auditable mapping between one external user and one principal.

    Uniqueness is enforced on both directions for a connector instance, so an
    ambiguous mapping is impossible to create rather than merely discouraged.
    A missing mapping fails closed for user-specific evidence.
    """

    __tablename__ = "workspace_user_mappings"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "connector_instance_id",
            "principal_id",
            name="uq_workspace_mapping_principal",
        ),
        UniqueConstraint(
            "tenant_id",
            "connector_instance_id",
            "external_user_id",
            name="uq_workspace_mapping_external",
        ),
        CheckConstraint("status IN ('active','revoked')", name="ck_workspace_mapping_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_column()
    connector_instance_id: Mapped[str] = mapped_column(
        ForeignKey("connector_instances.id", ondelete="CASCADE"), index=True, nullable=False
    )
    principal_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    external_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Schedule(Base):
    """A persisted, bounded schedule definition.

    Deliberately not an arbitrary cron expression: the interval is an integer
    number of seconds plus a timezone, and the ``operation`` must name a
    registered operation. A model cannot invent a schedule target.
    """

    __tablename__ = "schedules"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_schedule_tenant_name"),
        CheckConstraint(
            "schedule_type IN "
            "('connector_sync','connector_reconcile','report_rerun','notification_retry','workflow')",
            name="ck_schedule_type",
        ),
        CheckConstraint("status IN ('active','paused','disabled')", name="ck_schedule_status"),
        CheckConstraint("misfire_policy IN ('skip','run_once')", name="ck_schedule_misfire"),
        CheckConstraint("interval_seconds >= 60", name="ck_schedule_interval"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_column()
    owner_principal_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    schedule_type: Mapped[str] = mapped_column(String(32), nullable=False)
    operation: Mapped[str] = mapped_column(String(120), nullable=False)
    target_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, nullable=True
    )
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_result: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_failure_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    misfire_policy: Mapped[str] = mapped_column(String(16), default="skip", nullable=False)
    max_retries: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc
    )


class ScheduleRun(Base):
    """One claimed occurrence of one schedule.

    ``uq_schedule_run_occurrence`` on (schedule_id, scheduled_for) is what makes
    claiming idempotent across concurrent schedulers and across a restart: the
    second claimer loses on the unique constraint instead of running the job a
    second time.
    """

    __tablename__ = "schedule_runs"
    __table_args__ = (
        UniqueConstraint(
            "schedule_id", "scheduled_for", name="uq_schedule_run_occurrence"
        ),
        CheckConstraint(
            "status IN ('claimed','running','succeeded','failed','skipped')",
            name="ck_schedule_run_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_column()
    schedule_id: Mapped[str] = mapped_column(
        ForeignKey("schedules.id", ondelete="CASCADE"), index=True, nullable=False
    )
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="claimed", nullable=False)
    claim_token: Mapped[str] = mapped_column(String(36), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failure_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[str | None] = mapped_column(String(500), nullable=True)


class NotificationChannel(Base):
    """A registered notification destination.

    Only channel types with a registered implementation may be created. There is
    no free-form URL field, because a channel with an arbitrary destination is
    an arbitrary network call wearing a different name.
    """

    __tablename__ = "notification_channels"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_notification_channel_tenant_name"),
        CheckConstraint("status IN ('active','disabled')", name="ck_notification_channel_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_column()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    channel_type: Mapped[str] = mapped_column(String(64), nullable=False)
    config_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_delivery_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_failure_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc
    )


class Notification(Base):
    """A notification for one recipient, with bounded delivery state.

    ``status='suppressed'`` is a first-class outcome: it means the notification
    was generated but delivery was refused because the recipient could no longer
    see the evidence it referenced.
    """

    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','delivered','failed','suppressed')",
            name="ck_notification_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_column()
    principal_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    channel_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    subject: Mapped[str] = mapped_column(String(240), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failure_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc
    )


# ---------------------------------------------------------------------------
# #46 M1: immutable LLM usage metering
# ---------------------------------------------------------------------------


class ModelUsageEvent(Base):
    """One finalized model invocation, append-only.

    A row is written once per provider invocation and is never mutated: a
    correction is a new adjustment row, not an edit. Exactly-once finalization
    comes from the unique ``idempotency_key`` (request id + attempt role), so a
    retried request cannot double count.

    ``parent_call_id`` links a retry/fallback to the attempt it replaces, so a
    multi-call request is attributable without being collapsed into one row.
    """

    __tablename__ = "model_usage_events"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_model_usage_idempotency"),
        CheckConstraint(
            "usage_source IN ('provider_reported','estimated')",
            name="ck_model_usage_source",
        ),
        CheckConstraint(
            "call_role IN ('primary','retry','fallback')", name="ck_model_usage_call_role"
        ),
        CheckConstraint(
            "status IN ('succeeded','failed')", name="ck_model_usage_status"
        ),
        CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 AND total_tokens >= 0",
            name="ck_model_usage_tokens",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_column()
    principal_id: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    # Parent request/workflow correlation shared by every call of one request.
    request_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    message_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    execution_class: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provider_route: Mapped[str] = mapped_column(String(32), nullable=False)
    model_name: Mapped[str] = mapped_column(String(240), nullable=False)
    usage_source: Mapped[str] = mapped_column(String(32), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cached_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reasoning_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    call_role: Mapped[str] = mapped_column(String(16), nullable=False, default="primary")
    parent_call_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # Exactly-once key: request id + call role + attempt ordinal.
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    failure_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
