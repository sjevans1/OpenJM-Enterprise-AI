"""INF1-A: Rahkia inference serving registry and usage attribution sidecar.

Revision ID: 0015_inf1_inference_registry
Revises: 0014_operations_admin_capability
Create Date: 2026-10-08

Additive only:

* ``inference_model_releases``, ``inference_runtime_profiles``,
  ``inference_deployments``, ``inference_tenant_bindings``,
  ``inference_health_observations``, ``inference_routing_decisions`` and
  ``inference_usage_attributions``;
* ``platform:inference:admin`` added to the platform-operator capability
  vocabulary.

No existing table is altered apart from the capability check constraint, no row
is rewritten and no data is moved. The attribution sidecar references
``model_usage_events`` one-to-one but never modifies it, so the accepted M1
ledger and its historical rows are untouched.

Rollback notes: the downgrade drops these registry tables. That is safe for a
deployment that has not yet registered inference; where registry revisions must
be preserved, revert executable code and leave the tables in place, as the
accepted plan requires. Rows holding ``platform:inference:admin`` are revoked
rather than deleted before the constraint narrows again.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.migrations_util import create_table_if_missing, has_table

revision = "0015_inf1_inference_registry"
down_revision = "0014_operations_admin_capability"
branch_labels = None
depends_on = None

_CONSTRAINT = "ck_platform_operator_capability"

_ACTIVE_BEFORE = (
    "'platform:metadata:read','platform:tenants:admin',"
    "'platform:operators:admin','platform:content:support',"
    "'platform:operations:admin'"
)

_ACTIVE_AFTER = (
    "'platform:metadata:read','platform:tenants:admin',"
    "'platform:operators:admin','platform:content:support',"
    "'platform:operations:admin','platform:inference:admin'"
)

ROUTE_REASONS = (
    "allowed",
    "tenant_inactive",
    "binding_missing",
    "binding_revoked",
    "model_not_allowed",
    "site_not_allowed",
    "capability_unsupported",
    "isolation_unqualified",
    "deployment_unavailable",
    "health_stale",
    "request_too_large",
    "context_limit",
    "policy_changed",
)

_ROUTE_REASON_SQL = "(" + ",".join(f"'{reason}'" for reason in ROUTE_REASONS) + ")"


def _check(active_vocabulary: str) -> str:
    return f"status = 'revoked' OR capability IN ({active_vocabulary})"


def _tables() -> dict[str, sa.Table]:
    """Table definitions, kept in one place so upgrade and downgrade agree.

    One shared MetaData instance so the foreign keys between these tables resolve
    against each other rather than only against the live database.
    """
    metadata = sa.MetaData()
    # Present so the sidecar's foreign key resolves; never created here, because
    # the M1 baseline revision already owns this table.
    sa.Table("model_usage_events", metadata, sa.Column("id", sa.String(36), primary_key=True))
    return {
        "inference_model_releases": sa.Table(
            "inference_model_releases",
            metadata,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("rahkia_alias", sa.String(120), nullable=False),
            sa.Column("release_version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("artifact_ref", sa.String(500), nullable=True),
            sa.Column("lineage_ref", sa.String(500), nullable=True),
            sa.Column("tokenizer_ref", sa.String(500), nullable=True),
            sa.Column("tokenizer_revision", sa.String(120), nullable=True),
            sa.Column("chat_template_revision", sa.String(120), nullable=True),
            sa.Column("capabilities_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("max_context_tokens", sa.Integer(), nullable=False),
            sa.Column("max_output_tokens", sa.Integer(), nullable=False),
            sa.Column("commercial_use_ref", sa.String(240), nullable=True),
            sa.Column("evaluation_ref", sa.String(240), nullable=True),
            sa.Column("status", sa.String(32), nullable=False, server_default="active"),
            sa.Column("created_by", sa.String(64), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("rahkia_alias", "release_version", name="uq_inference_release_alias_version"),
            sa.CheckConstraint("status IN ('active','retired')", name="ck_inference_release_status"),
            sa.CheckConstraint("max_context_tokens > 0", name="ck_inference_release_context"),
            sa.CheckConstraint("max_output_tokens > 0", name="ck_inference_release_output"),
        ),
        "inference_runtime_profiles": sa.Table(
            "inference_runtime_profiles",
            metadata,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("adapter_kind", sa.String(64), nullable=False),
            sa.Column("adapter_version", sa.String(64), nullable=False),
            sa.Column("engine_kind", sa.String(64), nullable=False),
            sa.Column("engine_version", sa.String(120), nullable=False),
            sa.Column("profile_version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("image_ref", sa.String(500), nullable=True),
            sa.Column("capabilities_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("usage_method", sa.String(32), nullable=False, server_default="unknown"),
            sa.Column("cancellation_supported", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("cache_policy", sa.String(32), nullable=False, server_default="none"),
            sa.Column("qualification_ref", sa.String(240), nullable=True),
            sa.Column("status", sa.String(32), nullable=False, server_default="active"),
            sa.Column("created_by", sa.String(64), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "adapter_kind", "engine_kind", "profile_version", name="uq_inference_runtime_profile"
            ),
            sa.CheckConstraint("status IN ('active','retired')", name="ck_inference_runtime_status"),
            sa.CheckConstraint(
                "usage_method IN ('runtime_reported','tokenizer_estimate','character_estimate','unknown')",
                name="ck_inference_runtime_usage_method",
            ),
        ),
        "inference_deployments": sa.Table(
            "inference_deployments",
            metadata,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("deployment_id", sa.String(36), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
            sa.Column(
                "model_release_id",
                sa.String(36),
                sa.ForeignKey("inference_model_releases.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column(
                "runtime_profile_id",
                sa.String(36),
                sa.ForeignKey("inference_runtime_profiles.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column("installation_id", sa.String(64), nullable=True),
            sa.Column("site_id", sa.String(64), nullable=True),
            sa.Column("residency_domain", sa.String(64), nullable=True),
            sa.Column("inference_mode", sa.String(32), nullable=False),
            sa.Column("owner_tenant_id", sa.String(36), nullable=True),
            sa.Column("endpoint_ref", sa.String(240), nullable=True),
            sa.Column("credential_ref", sa.String(240), nullable=True),
            sa.Column("lifecycle", sa.String(32), nullable=False, server_default="disabled"),
            sa.Column("connectivity_mode", sa.String(32), nullable=False, server_default="connected"),
            sa.Column("telemetry_policy_revision", sa.String(64), nullable=True),
            sa.Column("max_context_tokens", sa.Integer(), nullable=True),
            sa.Column("max_output_tokens", sa.Integer(), nullable=True),
            sa.Column("max_request_bytes", sa.Integer(), nullable=True),
            sa.Column("pool_id", sa.String(64), nullable=True),
            sa.Column("accelerator_type", sa.String(64), nullable=True),
            sa.Column("accelerator_count", sa.Integer(), nullable=True),
            sa.Column("tensor_parallel", sa.Integer(), nullable=True),
            sa.Column("pipeline_parallel", sa.Integer(), nullable=True),
            sa.Column("capacity_reservation_ref", sa.String(240), nullable=True),
            sa.Column("security_qualification_ref", sa.String(240), nullable=True),
            sa.Column("created_by", sa.String(64), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("deployment_id", "revision", name="uq_inference_deployment_revision"),
            sa.CheckConstraint(
                "inference_mode IN ('customer_local','openjm_local','hosted_dedicated','hosted_shared')",
                name="ck_inference_deployment_mode",
            ),
            sa.CheckConstraint(
                "lifecycle IN ('disabled','ready','draining','quarantined')",
                name="ck_inference_deployment_lifecycle",
            ),
            sa.CheckConstraint(
                "connectivity_mode IN ('connected','air_gapped')",
                name="ck_inference_deployment_connectivity",
            ),
            sa.CheckConstraint(
                "(inference_mode = 'hosted_shared' AND owner_tenant_id IS NULL) "
                "OR (inference_mode != 'hosted_shared' AND owner_tenant_id IS NOT NULL)",
                name="ck_inference_deployment_owner_split",
            ),
        ),
        "inference_tenant_bindings": sa.Table(
            "inference_tenant_bindings",
            metadata,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("binding_id", sa.String(36), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("tenant_id", sa.String(36), nullable=False),
            sa.Column("rahkia_alias", sa.String(120), nullable=False),
            sa.Column("allowed_model_release_ids_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("allowed_deployment_ids_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("allowed_sites_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("allowed_modes_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("allowed_data_classes_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("required_capabilities_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("priority_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("allow_hosted", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("fallback_policy", sa.String(32), nullable=False, server_default="none"),
            sa.Column("status", sa.String(32), nullable=False, server_default="active"),
            sa.Column("created_by", sa.String(64), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("tenant_id", "rahkia_alias", name="uq_inference_binding_tenant_alias"),
            sa.CheckConstraint("status IN ('active','revoked')", name="ck_inference_binding_status"),
        ),
        "inference_health_observations": sa.Table(
            "inference_health_observations",
            metadata,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("deployment_id", sa.String(36), nullable=False),
            sa.Column("deployment_revision", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(16), nullable=False),
            sa.Column("model_present", sa.Boolean(), nullable=True),
            sa.Column("failure_code", sa.String(64), nullable=True),
            sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint("status IN ('healthy','degraded','unknown')", name="ck_inference_health_status"),
        ),
        "inference_routing_decisions": sa.Table(
            "inference_routing_decisions",
            metadata,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("attempt_id", sa.String(64), nullable=False),
            sa.Column("tenant_id", sa.String(36), nullable=False),
            sa.Column("binding_id", sa.String(36), nullable=True),
            sa.Column("binding_revision", sa.Integer(), nullable=True),
            sa.Column("deployment_id", sa.String(36), nullable=True),
            sa.Column("deployment_revision", sa.Integer(), nullable=True),
            sa.Column("model_release_id", sa.String(36), nullable=True),
            sa.Column("model_release_revision", sa.Integer(), nullable=True),
            sa.Column("runtime_profile_id", sa.String(36), nullable=True),
            sa.Column("runtime_profile_revision", sa.Integer(), nullable=True),
            sa.Column("reason", sa.String(32), nullable=False),
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint(f"reason IN {_ROUTE_REASON_SQL}", name="ck_inference_route_reason"),
        ),
        "inference_usage_attributions": sa.Table(
            "inference_usage_attributions",
            metadata,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "usage_event_id",
                sa.String(36),
                sa.ForeignKey("model_usage_events.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("tenant_id", sa.String(36), nullable=False),
            sa.Column("business_request_id", sa.String(64), nullable=False),
            sa.Column("logical_call_id", sa.String(64), nullable=False),
            sa.Column("attempt_id", sa.String(64), nullable=False),
            sa.Column("parent_attempt_id", sa.String(64), nullable=True),
            sa.Column("routing_decision_id", sa.String(36), nullable=True),
            sa.Column("deployment_id", sa.String(36), nullable=True),
            sa.Column("deployment_revision", sa.Integer(), nullable=True),
            sa.Column("model_release_id", sa.String(36), nullable=True),
            sa.Column("runtime_profile_id", sa.String(36), nullable=True),
            sa.Column("installation_id", sa.String(64), nullable=True),
            sa.Column("site_id", sa.String(64), nullable=True),
            sa.Column("inference_mode", sa.String(32), nullable=True),
            sa.Column("execution_certainty", sa.String(16), nullable=False),
            sa.Column("completeness", sa.String(16), nullable=False),
            sa.Column("count_method", sa.String(32), nullable=False),
            sa.Column("tokenizer_revision", sa.String(120), nullable=True),
            sa.Column("gateway_queue_ms", sa.Integer(), nullable=True),
            sa.Column("runtime_queue_ms", sa.Integer(), nullable=True),
            sa.Column("service_latency_ms", sa.Integer(), nullable=True),
            sa.Column("ttft_ms", sa.Integer(), nullable=True),
            sa.Column("generation_ms", sa.Integer(), nullable=True),
            sa.Column("output_tokens_per_second", sa.Float(), nullable=True),
            sa.Column("gpu_seconds", sa.Float(), nullable=True),
            sa.Column("gpu_method", sa.String(16), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("usage_event_id", name="uq_inference_attribution_event"),
            sa.CheckConstraint(
                "execution_certainty IN ('not_dispatched','completed','partial','unknown')",
                name="ck_inference_attribution_certainty",
            ),
            sa.CheckConstraint(
                "completeness IN ('complete','partial','unknown')",
                name="ck_inference_attribution_completeness",
            ),
        ),
    }


_INDEXES: tuple[tuple[str, str, list[str]], ...] = (
    ("ix_inference_model_releases_rahkia_alias", "inference_model_releases", ["rahkia_alias"]),
    ("ix_inference_deployments_deployment_id", "inference_deployments", ["deployment_id"]),
    ("ix_inference_deployments_owner_tenant_id", "inference_deployments", ["owner_tenant_id"]),
    ("ix_inference_deployments_model_release_id", "inference_deployments", ["model_release_id"]),
    ("ix_inference_deployments_runtime_profile_id", "inference_deployments", ["runtime_profile_id"]),
    ("ix_inference_tenant_bindings_tenant_id", "inference_tenant_bindings", ["tenant_id"]),
    ("ix_inference_tenant_bindings_rahkia_alias", "inference_tenant_bindings", ["rahkia_alias"]),
    ("ix_inference_health_observations_deployment_id", "inference_health_observations", ["deployment_id"]),
    ("ix_inference_routing_decisions_attempt_id", "inference_routing_decisions", ["attempt_id"]),
    ("ix_inference_routing_decisions_tenant_id", "inference_routing_decisions", ["tenant_id"]),
    ("ix_inference_usage_attributions_tenant_id", "inference_usage_attributions", ["tenant_id"]),
    ("ix_inference_usage_attributions_attempt_id", "inference_usage_attributions", ["attempt_id"]),
)


def upgrade() -> None:
    bind = op.get_bind()
    for name, table in _tables().items():
        if not name.startswith("inference_"):
            continue
        create_table_if_missing(bind, table)
    for index_name, table_name, columns in _INDEXES:
        if has_table(bind, table_name):
            op.create_index(index_name, table_name, columns, if_not_exists=True)

    if has_table(bind, "platform_operators"):
        with op.batch_alter_table("platform_operators") as batch:
            batch.drop_constraint(_CONSTRAINT, type_="check")
            batch.create_check_constraint(_CONSTRAINT, _check(_ACTIVE_AFTER))


def downgrade() -> None:
    bind = op.get_bind()
    if has_table(bind, "platform_operators"):
        bind.exec_driver_sql(
            "UPDATE platform_operators SET status = 'revoked', revoked_at = CURRENT_TIMESTAMP "
            "WHERE capability = 'platform:inference:admin'"
        )
        with op.batch_alter_table("platform_operators") as batch:
            batch.drop_constraint(_CONSTRAINT, type_="check")
            batch.create_check_constraint(_CONSTRAINT, _check(_ACTIVE_BEFORE))

    for table_name in reversed(list(_tables())):
        if has_table(bind, table_name):
            op.drop_table(table_name)
