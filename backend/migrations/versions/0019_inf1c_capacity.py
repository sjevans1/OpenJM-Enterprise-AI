"""INF1-C: application/platform capacity telemetry and reconciliation.

Revision ID: 0019_inf1c_capacity
Revises: 0017_inf1b_admission
Create Date: 2026-10-09

Additive only. Creates four tables and nothing else:

* ``inference_metric_definitions`` pinned, bounded-cardinality metric identities
  (key + revision + scope + value kind + origin + method);
* ``inference_metric_samples`` integer-valued observations per subject and
  window, with an explicit missing-value NULL and an explicit counter-reset flag;
* ``platform_telemetry_exports`` recorded aggregate-only export envelopes,
  idempotent on the export id and on the installation sequence;
* ``platform_telemetry_imports`` recorded aggregate-only imports, idempotent on
  the source export id.

No existing table is altered, no column is added elsewhere and no historical row
is rewritten. The M1 usage ledger, the INF1-A registry and attribution tables and
the INF1-B capacity tables are untouched.

Rollback notes: the downgrade drops only these four tables. Where telemetry
evidence must be preserved, revert executable code and leave the tables in place,
as the accepted plan requires.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.migrations_util import create_table_if_missing, has_table

revision = "0019_inf1c_capacity"
down_revision = "0018_chat_artifacts"
branch_labels = None
depends_on = None

METRIC_SCOPES = ("attempt", "pool_window")
VALUE_KINDS = ("counter", "gauge", "duration_ms", "gpu_ms", "money_minor")
ORIGINS = ("gateway", "runtime", "collector")
METHODS = ("measured", "allocated", "estimated", "unknown")
QUALITIES = ("complete", "partial", "unknown")
SUBJECT_KINDS = ("runtime", "deployment", "pool", "tenant")


def _sql(values: tuple[str, ...]) -> str:
    return "(" + ",".join(f"'{value}'" for value in values) + ")"


def _tables() -> dict[str, sa.Table]:
    """One shared MetaData so upgrade and downgrade agree on the shape."""
    metadata = sa.MetaData()
    return {
        "inference_metric_definitions": sa.Table(
            "inference_metric_definitions",
            metadata,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("metric_key", sa.String(64), nullable=False),
            sa.Column("definition_revision", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("metric_scope", sa.String(16), nullable=False),
            sa.Column("subject_kind", sa.String(16), nullable=False),
            sa.Column("value_kind", sa.String(16), nullable=False),
            sa.Column("unit", sa.String(32), nullable=False),
            sa.Column("origin", sa.String(16), nullable=False),
            sa.Column("gpu_method", sa.String(16), nullable=True),
            sa.Column("cardinality_class", sa.String(16), nullable=False, server_default="bounded"),
            sa.Column("status", sa.String(16), nullable=False, server_default="active"),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "metric_key", "definition_revision", name="uq_inference_metric_key_rev"
            ),
            sa.CheckConstraint(
                f"metric_scope IN {_sql(METRIC_SCOPES)}", name="ck_inference_metric_scope"
            ),
            sa.CheckConstraint(
                f"value_kind IN {_sql(VALUE_KINDS)}", name="ck_inference_metric_value_kind"
            ),
            sa.CheckConstraint(
                f"origin IN {_sql(ORIGINS)}", name="ck_inference_metric_origin"
            ),
            sa.CheckConstraint(
                f"subject_kind IN {_sql(SUBJECT_KINDS)}",
                name="ck_inference_metric_subject_kind",
            ),
            sa.CheckConstraint(
                "cardinality_class IN ('bounded','high')",
                name="ck_inference_metric_cardinality",
            ),
            sa.CheckConstraint(
                "status IN ('active','retired')", name="ck_inference_metric_status"
            ),
            sa.CheckConstraint("definition_revision >= 1", name="ck_inference_metric_revision"),
            sa.CheckConstraint(
                f"gpu_method IS NULL OR gpu_method IN {_sql(METHODS)}",
                name="ck_inference_metric_gpu_method",
            ),
        ),
        "inference_metric_samples": sa.Table(
            "inference_metric_samples",
            metadata,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "metric_definition_id",
                sa.String(36),
                sa.ForeignKey("inference_metric_definitions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("metric_key", sa.String(64), nullable=False),
            sa.Column("metric_revision", sa.Integer(), nullable=False),
            sa.Column("subject_kind", sa.String(16), nullable=False),
            sa.Column("subject_ref", sa.String(64), nullable=False),
            sa.Column("tenant_id", sa.String(36), nullable=True),
            sa.Column("pool_id", sa.String(64), nullable=True),
            sa.Column("deployment_id", sa.String(64), nullable=True),
            sa.Column("attempt_id", sa.String(64), nullable=True),
            sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
            sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
            sa.Column("value_int", sa.BigInteger(), nullable=True),
            sa.Column("currency", sa.String(8), nullable=True),
            sa.Column("origin", sa.String(16), nullable=False),
            sa.Column("method", sa.String(16), nullable=False, server_default="unknown"),
            sa.Column("quality", sa.String(16), nullable=False, server_default="unknown"),
            sa.Column("sequence", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("counter_reset", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "metric_definition_id",
                "subject_kind",
                "subject_ref",
                "window_start",
                "window_end",
                name="uq_inference_metric_sample",
            ),
            sa.CheckConstraint(
                f"subject_kind IN {_sql(SUBJECT_KINDS)}",
                name="ck_inference_metric_sample_subject_kind",
            ),
            sa.CheckConstraint(
                f"origin IN {_sql(ORIGINS)}", name="ck_inference_metric_sample_origin"
            ),
            sa.CheckConstraint(
                f"method IN {_sql(METHODS)}", name="ck_inference_metric_sample_method"
            ),
            sa.CheckConstraint(
                f"quality IN {_sql(QUALITIES)}", name="ck_inference_metric_sample_quality"
            ),
            sa.CheckConstraint(
                "value_int IS NULL OR value_int >= 0", name="ck_inference_metric_sample_value"
            ),
            sa.CheckConstraint("sequence >= 0", name="ck_inference_metric_sample_sequence"),
        ),
        "platform_telemetry_exports": sa.Table(
            "platform_telemetry_exports",
            metadata,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("export_id", sa.String(64), nullable=False),
            sa.Column("installation_id", sa.String(64), nullable=False),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column("commercial_tenant_ref", sa.String(64), nullable=True),
            sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
            sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
            sa.Column("schema_version", sa.String(32), nullable=False),
            sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("payload_digest", sa.String(128), nullable=True),
            sa.Column("signing_key_id", sa.String(64), nullable=True),
            sa.Column("telemetry_policy_revision", sa.String(64), nullable=True),
            sa.Column("correction_of", sa.String(64), nullable=True),
            sa.Column("status", sa.String(16), nullable=False, server_default="built"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("export_id", name="uq_platform_telemetry_export_id"),
            sa.UniqueConstraint(
                "installation_id", "sequence", name="uq_platform_telemetry_export_sequence"
            ),
            sa.CheckConstraint("sequence >= 1", name="ck_platform_telemetry_export_sequence"),
            sa.CheckConstraint("row_count >= 0", name="ck_platform_telemetry_export_rows"),
            sa.CheckConstraint(
                "schema_version = 'aggregate_only_v1'",
                name="ck_platform_telemetry_export_schema",
            ),
            sa.CheckConstraint(
                "status IN ('built','imported','superseded')",
                name="ck_platform_telemetry_export_status",
            ),
        ),
        "platform_telemetry_imports": sa.Table(
            "platform_telemetry_imports",
            metadata,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("export_id", sa.String(64), nullable=False),
            sa.Column("installation_id", sa.String(64), nullable=False),
            sa.Column("sequence", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("schema_version", sa.String(32), nullable=False),
            sa.Column("payload_digest", sa.String(128), nullable=True),
            sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("export_id", name="uq_platform_telemetry_import_id"),
            sa.CheckConstraint("row_count >= 0", name="ck_platform_telemetry_import_rows"),
        ),
    }


_INDEXES: tuple[tuple[str, str, list[str]], ...] = (
    ("ix_inference_metric_definitions_metric_key", "inference_metric_definitions", ["metric_key"]),
    ("ix_inference_metric_samples_metric_key", "inference_metric_samples", ["metric_key"]),
    ("ix_inference_metric_samples_subject_ref", "inference_metric_samples", ["subject_ref"]),
    ("ix_inference_metric_samples_tenant_id", "inference_metric_samples", ["tenant_id"]),
    ("ix_inference_metric_samples_pool_id", "inference_metric_samples", ["pool_id"]),
    ("ix_inference_metric_samples_deployment_id", "inference_metric_samples", ["deployment_id"]),
    ("ix_inference_metric_samples_attempt_id", "inference_metric_samples", ["attempt_id"]),
    ("ix_inference_metric_samples_window_start", "inference_metric_samples", ["window_start"]),
    ("ix_platform_telemetry_exports_installation_id", "platform_telemetry_exports", ["installation_id"]),
    ("ix_platform_telemetry_imports_installation_id", "platform_telemetry_imports", ["installation_id"]),
)


def upgrade() -> None:
    bind = op.get_bind()
    for name, table in _tables().items():
        create_table_if_missing(bind, table)
    for index_name, table_name, columns in _INDEXES:
        if has_table(bind, table_name):
            op.create_index(index_name, table_name, columns, if_not_exists=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table_name in reversed(list(_tables())):
        if has_table(bind, table_name):
            op.drop_table(table_name)
