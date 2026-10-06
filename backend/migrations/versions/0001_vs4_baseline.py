"""VS4 baseline schema.

Revision ID: 0001_vs4_baseline
Revises:
Create Date: 2026-10-06

This revision reproduces the accepted VS1-VS4 application-metadata schema
exactly as it existed when the migration framework was introduced.

It exists so that:

* a brand-new database is created by migrations and matches the ORM metadata;
* an existing deployment (created by the previous ``create_all`` + ad-hoc
  ``ALTER TABLE`` bootstrap) can be *stamped* at this revision and then be
  upgraded by reviewed, incremental revisions without rewriting any row.

Absolutely no data is moved or rewritten by this revision.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_vs4_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_conversations_user_id", "conversations", ["user_id"])

    op.create_table(
        "messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.String(36),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("execution_class", sa.String(32), nullable=True),
        sa.Column("requested_mode", sa.String(16), nullable=True),
        sa.Column("evidence_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])

    op.create_table(
        "documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("original_name", sa.String(500), nullable=False),
        sa.Column("stored_path", sa.String(1000), nullable=False),
        sa.Column("mime_type", sa.String(255), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("indexed", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_documents_user_id", "documents", ["user_id"])

    op.create_table(
        "data_sources",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("engine", sa.String(32), nullable=False),
        sa.Column("connection_secret", sa.Text(), nullable=False),
        sa.Column("revenue_currency", sa.String(3), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("schema_json", sa.Text(), nullable=True),
        sa.Column("authorized_objects_json", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_schema_refresh", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_data_sources_user_id", "data_sources", ["user_id"])

    op.create_table(
        "execution_traces",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("tool_invocation_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("conversation_id", sa.String(36), nullable=True),
        sa.Column("route", sa.String(32), nullable=True),
        sa.Column("requested_mode", sa.String(16), nullable=True),
        sa.Column("tool_name", sa.String(128), nullable=False),
        sa.Column("operation_class", sa.String(32), nullable=False),
        sa.Column("risk_level", sa.String(32), nullable=False),
        sa.Column("requires_approval", sa.Boolean(), nullable=False),
        sa.Column("source_id", sa.String(128), nullable=True),
        sa.Column("model_name", sa.String(240), nullable=True),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("planned_sql", sa.Text(), nullable=True),
        sa.Column("executed_sql", sa.Text(), nullable=True),
        sa.Column("validation_decision", sa.String(64), nullable=True),
        sa.Column("policy_decision", sa.String(64), nullable=True),
        sa.Column("row_limit", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error_class", sa.String(240), nullable=True),
        sa.Column("evidence_ids_json", sa.Text(), nullable=True),
        sa.Column("processing_location", sa.String(128), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_execution_traces_request_id", "execution_traces", ["request_id"])
    op.create_index("ix_execution_traces_user_id", "execution_traces", ["user_id"])
    op.create_index(
        "ix_execution_traces_conversation_id", "execution_traces", ["conversation_id"]
    )
    op.create_index("ix_execution_traces_tool_name", "execution_traces", ["tool_name"])

    op.create_table(
        "saved_reports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column(
            "conversation_id",
            sa.String(36),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "message_id",
            sa.String(36),
            sa.ForeignKey("messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("answer_text", sa.Text(), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False),
        sa.Column("execution_class", sa.String(32), nullable=False),
        sa.Column("requested_mode", sa.String(16), nullable=True),
        sa.Column("source_count", sa.Integer(), nullable=False),
        sa.Column("snapshot_as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "message_id", name="uq_saved_report_owner_message"),
    )
    op.create_index("ix_saved_reports_user_id", "saved_reports", ["user_id"])
    op.create_index("ix_saved_reports_conversation_id", "saved_reports", ["conversation_id"])
    op.create_index("ix_saved_reports_message_id", "saved_reports", ["message_id"])

    op.create_table(
        "report_definition_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "report_id",
            sa.String(36),
            sa.ForeignKey("saved_reports.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("requested_mode", sa.String(16), nullable=False),
        sa.Column("pinned_document_ids_json", sa.Text(), nullable=False),
        sa.Column("pinned_source_tables_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("report_id", "version", name="uq_report_definition_version"),
    )
    op.create_index(
        "ix_report_definition_versions_report_id", "report_definition_versions", ["report_id"]
    )
    op.create_index(
        "ix_report_definition_versions_user_id", "report_definition_versions", ["user_id"]
    )

    op.create_table(
        "report_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column(
            "report_id",
            sa.String(36),
            sa.ForeignKey("saved_reports.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "definition_id",
            sa.String(36),
            sa.ForeignKey("report_definition_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("definition_version", sa.Integer(), nullable=False),
        sa.Column("requested_mode", sa.String(16), nullable=False),
        sa.Column("idempotency_key", sa.String(36), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_category", sa.String(32), nullable=True),
        sa.Column("trace_ids_json", sa.Text(), nullable=True),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("result_size_bytes", sa.Integer(), nullable=True),
        sa.Column("result_sha256", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_report_run_owner_key"),
        sa.UniqueConstraint("id", name="uq_report_run_id"),
        sa.CheckConstraint(
            "status IN ('running','succeeded','failed','interrupted')",
            name="ck_report_run_status",
        ),
        sa.CheckConstraint(
            "(status = 'running') = (finished_at IS NULL)",
            name="ck_report_run_running_unfinished",
        ),
        sa.CheckConstraint(
            "NOT (status = 'running' AND failure_category IS NOT NULL)",
            name="ck_report_run_running_no_failure",
        ),
        sa.CheckConstraint(
            "(status IN ('succeeded','failed','interrupted')) = (finished_at IS NOT NULL)",
            name="ck_report_run_terminal_finished",
        ),
        sa.CheckConstraint(
            "(status = 'succeeded') = (result_json IS NOT NULL)",
            name="ck_report_run_succeeded_has_result",
        ),
        sa.CheckConstraint(
            "(status IN ('failed','interrupted')) = (failure_category IS NOT NULL)",
            name="ck_report_run_failed_has_category",
        ),
    )
    op.create_index("ix_report_runs_user_id", "report_runs", ["user_id"])
    op.create_index("ix_report_runs_report_id", "report_runs", ["report_id"])
    op.create_index("ix_report_runs_definition_id", "report_runs", ["definition_id"])
    op.create_index("ix_report_runs_idempotency_key", "report_runs", ["idempotency_key"])

    # At most one active ('running') run per owner/report/definition version.
    op.create_index(
        "uq_report_run_active_per_definition",
        "report_runs",
        ["user_id", "report_id", "definition_id", "definition_version"],
        unique=True,
        sqlite_where=sa.text("status = 'running'"),
        postgresql_where=sa.text("status = 'running'"),
    )


def downgrade() -> None:
    op.drop_table("report_runs")
    op.drop_table("report_definition_versions")
    op.drop_table("saved_reports")
    op.drop_table("execution_traces")
    op.drop_table("data_sources")
    op.drop_table("documents")
    op.drop_table("messages")
    op.drop_table("conversations")
