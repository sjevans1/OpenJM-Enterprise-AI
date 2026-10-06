"""VS7 — governed connectors, external resources and operational workflows.

Revision ID: 0007_vs7_connectors
Revises: 0006_guard_triggers
Create Date: 2026-10-06

Ten new tenant-owned tables:

``connector_instances``      a configured connection to an external system
``connector_credentials``    tenant-bound credential references (ciphertext only)
``external_resources``       normalized connector-owned resources
``connector_cursors``        persisted sync checkpoints
``connector_sync_runs``      operator-visible sync/reconcile records
``workspace_user_mappings``  explicit external-user to principal mapping
``schedules``                bounded schedule definitions
``schedule_runs``            claimed occurrences (the idempotency primitive)
``notification_channels``    registered delivery destinations
``notifications``            per-recipient delivery state

Only three of these carry a uniqueness rule that is load-bearing for security,
and each is deliberate:

* ``external_resources`` is unique on
  (tenant, instance, namespace, external_id), which is the namespacing that keeps
  a connector resource from ever colliding with an uploaded document, a
  structured source, another connector or another tenant;
* ``workspace_user_mappings`` is unique on both the principal and the external
  user id, so an ambiguous mapping cannot be created in the first place;
* ``schedule_runs`` is unique on (schedule_id, scheduled_for), which is what
  makes claiming an occurrence idempotent across concurrent schedulers and
  across a restart.

Every revision in this framework guards its own DDL, so this file is safe to
re-run against a database that already has the tables.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.migrations_util import has_table

revision = "0007_vs7_connectors"
down_revision = "0006_guard_triggers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if has_table(bind, "connector_instances"):
        return

    op.create_table(
        "connector_instances",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("connector_type", sa.String(64), nullable=False),
        sa.Column("connector_version", sa.String(32), nullable=False),
        sa.Column("display_name", sa.String(240), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="configured"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("config_json", sa.Text(), nullable=True),
        sa.Column("credential_id", sa.String(36), nullable=True),
        sa.Column("health_status", sa.String(32), nullable=False, server_default="unknown"),
        sa.Column("health_detail", sa.String(240), nullable=True),
        sa.Column("last_successful_connection_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_successful_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_reconciliation_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_category", sa.String(64), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('configured','active','disabled','error','disconnected')",
            name="ck_connector_instance_status",
        ),
        sa.CheckConstraint(
            "health_status IN ('unknown','healthy','unhealthy')",
            name="ck_connector_instance_health",
        ),
        sa.UniqueConstraint("tenant_id", "name", name="uq_connector_instance_tenant_name"),
    )
    op.create_index("ix_connector_instances_tenant_id", "connector_instances", ["tenant_id"])

    op.create_table(
        "connector_credentials",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("connector_instance_id", sa.String(36), nullable=False),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("secret_ciphertext", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.String(128), nullable=True),
        sa.CheckConstraint(
            "status IN ('active','superseded','revoked')", name="ck_connector_credential_status"
        ),
        sa.ForeignKeyConstraint(
            ["connector_instance_id"],
            ["connector_instances.id"],
            name="fk_connector_credentials_instance",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_connector_credentials_tenant_id", "connector_credentials", ["tenant_id"]
    )
    op.create_index(
        "ix_connector_credentials_connector_instance_id",
        "connector_credentials",
        ["connector_instance_id"],
    )

    op.create_table(
        "external_resources",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("connector_instance_id", sa.String(36), nullable=False),
        sa.Column("resource_namespace", sa.String(120), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("resource_type", sa.String(64), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("external_revision", sa.String(128), nullable=True),
        sa.Column("external_parent_id", sa.String(255), nullable=True),
        sa.Column("title", sa.String(500), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("lifecycle_state", sa.String(32), nullable=False, server_default="active"),
        sa.Column(
            "permission_state", sa.String(32), nullable=False, server_default="unknown"
        ),
        sa.Column("document_id", sa.String(36), nullable=True),
        sa.Column("source_metadata_json", sa.Text(), nullable=True),
        sa.Column("provenance_json", sa.Text(), nullable=True),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("quarantine_reason", sa.String(64), nullable=True),
        sa.Column("quarantined_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "lifecycle_state IN ('active','quarantined','deleted')",
            name="ck_external_resource_lifecycle",
        ),
        sa.CheckConstraint(
            "permission_state IN ('allowed','revoked','unknown')",
            name="ck_external_resource_permission",
        ),
        sa.ForeignKeyConstraint(
            ["connector_instance_id"],
            ["connector_instances.id"],
            name="fk_external_resources_instance",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "connector_instance_id",
            "resource_namespace",
            "external_id",
            name="uq_external_resource_identity",
        ),
    )
    op.create_index("ix_external_resources_tenant_id", "external_resources", ["tenant_id"])
    op.create_index(
        "ix_external_resources_connector_instance_id",
        "external_resources",
        ["connector_instance_id"],
    )
    op.create_index("ix_external_resources_document_id", "external_resources", ["document_id"])

    op.create_table(
        "connector_cursors",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("connector_instance_id", sa.String(36), nullable=False),
        sa.Column("stream", sa.String(64), nullable=False),
        sa.Column("cursor", sa.Text(), nullable=True),
        sa.Column("last_event_id", sa.String(128), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["connector_instance_id"],
            ["connector_instances.id"],
            name="fk_connector_cursors_instance",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("connector_instance_id", "stream", name="uq_connector_cursor_stream"),
    )
    op.create_index("ix_connector_cursors_tenant_id", "connector_cursors", ["tenant_id"])
    op.create_index(
        "ix_connector_cursors_connector_instance_id", "connector_cursors", ["connector_instance_id"]
    )

    op.create_table(
        "connector_sync_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("connector_instance_id", sa.String(36), nullable=False),
        sa.Column("run_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="running"),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("items_scanned", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_updated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_deleted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_quarantined", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_skipped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cursor_before", sa.Text(), nullable=True),
        sa.Column("cursor_after", sa.Text(), nullable=True),
        sa.Column("failure_category", sa.String(64), nullable=True),
        sa.Column("detail", sa.String(500), nullable=True),
        sa.CheckConstraint(
            "run_type IN ('initial','incremental','reconcile','test','purge')",
            name="ck_connector_sync_run_type",
        ),
        sa.CheckConstraint(
            "status IN ('running','succeeded','failed','skipped')",
            name="ck_connector_sync_run_status",
        ),
        sa.ForeignKeyConstraint(
            ["connector_instance_id"],
            ["connector_instances.id"],
            name="fk_connector_sync_runs_instance",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_connector_sync_runs_tenant_id", "connector_sync_runs", ["tenant_id"])
    op.create_index(
        "ix_connector_sync_runs_connector_instance_id",
        "connector_sync_runs",
        ["connector_instance_id"],
    )
    op.create_index(
        "ix_connector_sync_runs_idempotency_key", "connector_sync_runs", ["idempotency_key"]
    )

    op.create_table(
        "workspace_user_mappings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("connector_instance_id", sa.String(36), nullable=False),
        sa.Column("principal_id", sa.String(36), nullable=False),
        sa.Column("external_user_id", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("created_by", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('active','revoked')", name="ck_workspace_mapping_status"),
        sa.ForeignKeyConstraint(
            ["connector_instance_id"],
            ["connector_instances.id"],
            name="fk_workspace_mappings_instance",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "connector_instance_id",
            "principal_id",
            name="uq_workspace_mapping_principal",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "connector_instance_id",
            "external_user_id",
            name="uq_workspace_mapping_external",
        ),
    )
    op.create_index(
        "ix_workspace_user_mappings_tenant_id", "workspace_user_mappings", ["tenant_id"]
    )
    op.create_index(
        "ix_workspace_user_mappings_connector_instance_id",
        "workspace_user_mappings",
        ["connector_instance_id"],
    )
    op.create_index(
        "ix_workspace_user_mappings_principal_id", "workspace_user_mappings", ["principal_id"]
    )

    op.create_table(
        "schedules",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("owner_principal_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("schedule_type", sa.String(32), nullable=False),
        sa.Column("operation", sa.String(120), nullable=False),
        sa.Column("target_json", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_result", sa.String(32), nullable=True),
        sa.Column("last_failure_category", sa.String(64), nullable=True),
        sa.Column("misfire_policy", sa.String(16), nullable=False, server_default="skip"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "schedule_type IN "
            "('connector_sync','connector_reconcile','report_rerun','notification_retry','workflow')",
            name="ck_schedule_type",
        ),
        sa.CheckConstraint("status IN ('active','paused','disabled')", name="ck_schedule_status"),
        sa.CheckConstraint("misfire_policy IN ('skip','run_once')", name="ck_schedule_misfire"),
        sa.CheckConstraint("interval_seconds >= 60", name="ck_schedule_interval"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_schedule_tenant_name"),
    )
    op.create_index("ix_schedules_tenant_id", "schedules", ["tenant_id"])
    op.create_index("ix_schedules_owner_principal_id", "schedules", ["owner_principal_id"])
    op.create_index("ix_schedules_next_run_at", "schedules", ["next_run_at"])

    op.create_table(
        "schedule_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("schedule_id", sa.String(36), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="claimed"),
        sa.Column("claim_token", sa.String(36), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_category", sa.String(64), nullable=True),
        sa.Column("detail", sa.String(500), nullable=True),
        sa.CheckConstraint(
            "status IN ('claimed','running','succeeded','failed','skipped')",
            name="ck_schedule_run_status",
        ),
        sa.ForeignKeyConstraint(
            ["schedule_id"], ["schedules.id"], name="fk_schedule_runs_schedule", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("schedule_id", "scheduled_for", name="uq_schedule_run_occurrence"),
    )
    op.create_index("ix_schedule_runs_tenant_id", "schedule_runs", ["tenant_id"])
    op.create_index("ix_schedule_runs_schedule_id", "schedule_runs", ["schedule_id"])

    op.create_table(
        "notification_channels",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("channel_type", sa.String(64), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_delivery_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_category", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('active','disabled')", name="ck_notification_channel_status"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_notification_channel_tenant_name"),
    )
    op.create_index("ix_notification_channels_tenant_id", "notification_channels", ["tenant_id"])

    op.create_table(
        "notifications",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("principal_id", sa.String(36), nullable=False),
        sa.Column("channel_id", sa.String(36), nullable=True),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("subject", sa.String(240), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("resource_type", sa.String(64), nullable=True),
        sa.Column("resource_id", sa.String(128), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_category", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending','delivered','failed','suppressed')",
            name="ck_notification_status",
        ),
    )
    op.create_index("ix_notifications_tenant_id", "notifications", ["tenant_id"])
    op.create_index("ix_notifications_principal_id", "notifications", ["principal_id"])
    op.create_index("ix_notifications_channel_id", "notifications", ["channel_id"])


def downgrade() -> None:
    for table in (
        "notifications",
        "notification_channels",
        "schedule_runs",
        "schedules",
        "workspace_user_mappings",
        "connector_sync_runs",
        "connector_cursors",
        "external_resources",
        "connector_credentials",
        "connector_instances",
    ):
        op.drop_table(table)
