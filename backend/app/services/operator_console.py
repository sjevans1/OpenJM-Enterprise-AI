"""OPS1 operator-console projections (OpenJM platform operator plane).

This module builds the *safe* operator representations used by the OpenJM
Control Center. It contains no authorization of its own: every caller is already
guarded by an explicit ``platform:*`` capability, and everything here is written
so that a metadata operator can never observe customer content.

Three separate concerns are deliberately kept apart:

* **Audit records** (``audit_records``) are the persisted, authoritative
  governance/security trail. They are projected through a bounded allow-list.
* **Operational diagnostics** are runtime telemetry built from safe metadata:
  readiness components, execution traces and bounded failure categories. Traces
  are projected field-by-field; the plan/executed SQL, the input hash and the
  free-form trace metadata are never returned, because a SQL string can carry a
  customer value.
* **Operational logs** are the JSON structured stream on stdout. They are NOT
  copied into the metadata database; this module reports the integration status
  instead of inventing an unsafe raw-log table.

Nothing here reads a document, a message, a report body or a connector
credential.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditRecord, ExecutionTrace, ModelUsageEvent, SupportDelegation, Tenant
from app.services.access_governance import as_utc

# Server-side bounds. The audit trail is the largest table an operator can scan,
# so the page size is capped independently of what a caller requests.
MAX_AUDIT_PAGE = 100
DEFAULT_AUDIT_PAGE = 50
MAX_TRACE_PAGE = 100
DEFAULT_TRACE_PAGE = 50
MAX_REASON_CHARS = 200
MAX_METADATA_VALUE_CHARS = 120

# Bounded allow-list for ``AuditRecord.metadata_json``. The raw JSON is free-form
# and may hold a value we never want in an operator screen, so only these keys
# are projected, each as a bounded string. A key that is absent is simply absent.
SAFE_AUDIT_METADATA_KEYS: frozenset[str] = frozenset(
    {
        "actor",
        "attempts",
        "capability",
        "connector_type",
        "department_id",
        "dry_run",
        "expires_at",
        "limit",
        "member_principal_id",
        "method",
        "name",
        "plan_id",
        "principal_id",
        "purged",
        "quarantined",
        "revoked",
        "scope",
        "slug",
        "status",
        "step",
        "steps",
        "steward_principal_id",
        "tool",
        "version",
    }
)

# Event families an operator filters by. Derived from the recorded action
# namespace rather than a stored column, so a new action still files correctly.
AUDIT_FAMILIES: tuple[str, ...] = (
    "authorize",
    "auth",
    "platform",
    "governance",
    "connector",
    "knowledge",
    "notification",
    "inference",
    "report",
    "action",
)

# Operational-log integration status. Reported honestly: the canonical
# operational-log surface is the JSON stream on stdout, and OPS1 does not copy it
# into the metadata database.
OPERATIONAL_LOG_STATUS: dict[str, object] = {
    "persistence": "stream",
    "format": "json",
    "location": "process stdout (structured JSON, one object per line)",
    "aggregated_into_metadata_db": False,
    "external_aggregation": "optional (for example OpenTelemetry/Loki) - not required by OPS1",
    "guidance": (
        "Use the authoritative audit trail and persisted execution traces in this "
        "console. Raw process logs stay on the streaming surface; enable an "
        "external aggregator if long-term log search is required."
    ),
}


def _bounded(value: object, limit: int = MAX_METADATA_VALUE_CHARS) -> str:
    text = "" if value is None else str(value)
    return text[:limit]


def project_audit_metadata(raw_json: str | None) -> dict[str, str]:
    """Project the free-form audit metadata to a bounded allow-list.

    Returns only known-safe keys, each truncated. An unparseable or unexpected
    payload yields an empty projection rather than an error, so the trail stays
    readable without ever echoing an unknown value.
    """
    if not raw_json:
        return {}
    import json

    try:
        parsed = json.loads(raw_json)
    except (ValueError, TypeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {
        key: _bounded(value)
        for key, value in parsed.items()
        if key in SAFE_AUDIT_METADATA_KEYS
    }


def project_audit_record(record: AuditRecord) -> dict:
    """One bounded, content-free operator view of an audit record."""
    return {
        "id": record.id,
        "created_at": record.created_at,
        "tenant_id": record.tenant_id,
        "principal_id": record.principal_id,
        "role": record.role,
        "action": record.action,
        "family": audit_family(record.action),
        "decision": record.decision,
        "resource_type": record.resource_type,
        "resource_id": record.resource_id,
        "auth_method": record.auth_method,
        "reason": _bounded(record.reason, MAX_REASON_CHARS) or None,
        "metadata": project_audit_metadata(record.metadata_json),
    }


def audit_family(action: str | None) -> str:
    """Map an action name onto its bounded operator family."""
    if not action:
        return "other"
    for family in AUDIT_FAMILIES:
        if action.startswith(family):
            return family
    return "other"


def project_trace(trace: ExecutionTrace) -> dict:
    """One bounded, content-free operator view of an execution trace.

    ``planned_sql``, ``executed_sql``, ``input_hash`` and ``metadata_json`` are
    deliberately excluded: a SQL string can carry a customer value and the trace
    metadata is free-form.
    """
    return {
        "id": trace.id,
        "created_at": trace.created_at,
        "request_id": trace.request_id,
        "tenant_id": trace.tenant_id,
        "tool_name": trace.tool_name,
        "operation_class": trace.operation_class,
        "risk_level": trace.risk_level,
        "status": trace.status,
        "duration_ms": trace.duration_ms,
        "error_class": _bounded(trace.error_class, MAX_REASON_CHARS) or None,
        "model_name": trace.model_name,
        "policy_decision": trace.policy_decision,
        "validation_decision": trace.validation_decision,
        "route": trace.route,
        "requested_mode": trace.requested_mode,
    }


# ---------------------------------------------------------------------------
# Audit explorer
# ---------------------------------------------------------------------------


async def search_audit(
    db: AsyncSession,
    *,
    tenant_id: str | None = None,
    principal_id: str | None = None,
    action: str | None = None,
    family: str | None = None,
    decision: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    audit_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> dict:
    """Bounded, filtered, paginated view of the authoritative audit trail."""
    size = DEFAULT_AUDIT_PAGE if limit is None else int(limit)
    size = max(1, min(size, MAX_AUDIT_PAGE))
    offset = max(0, int(offset or 0))

    conditions = []
    if audit_id:
        conditions.append(AuditRecord.id == audit_id)
    if tenant_id:
        conditions.append(AuditRecord.tenant_id == tenant_id)
    if principal_id:
        conditions.append(AuditRecord.principal_id == principal_id)
    if action:
        conditions.append(AuditRecord.action == action)
    if family:
        conditions.append(AuditRecord.action.like(f"{family}%"))
    if decision:
        conditions.append(AuditRecord.decision == decision)
    if resource_type:
        conditions.append(AuditRecord.resource_type == resource_type)
    if resource_id:
        conditions.append(AuditRecord.resource_id == resource_id)
    if since is not None:
        conditions.append(AuditRecord.created_at >= since)
    if until is not None:
        conditions.append(AuditRecord.created_at <= until)

    total = (
        await db.execute(select(func.count()).select_from(AuditRecord).where(*conditions))
    ).scalar_one()

    rows = (
        await db.execute(
            select(AuditRecord)
            .where(*conditions)
            .order_by(AuditRecord.created_at.desc())
            .limit(size)
            .offset(offset)
        )
    ).scalars().all()

    return {
        "events": [project_audit_record(row) for row in rows],
        "total": int(total),
        "limit": size,
        "offset": offset,
        "has_more": offset + len(rows) < int(total),
    }


async def audit_summary(db: AsyncSession) -> dict:
    """Recent-activity summary across the audit event families."""
    total = (await db.execute(select(func.count()).select_from(AuditRecord))).scalar_one()

    denied = (
        await db.execute(
            select(func.count())
            .select_from(AuditRecord)
            .where(AuditRecord.decision == "deny")
        )
    ).scalar_one()

    recent = (
        await db.execute(
            select(AuditRecord)
            .order_by(AuditRecord.created_at.desc())
            .limit(10)
        )
    ).scalars().all()

    family_rows = (
        await db.execute(
            select(AuditRecord.action, func.count()).group_by(AuditRecord.action)
        )
    ).all()
    by_family: dict[str, int] = {}
    for action, count in family_rows:
        key = audit_family(action)
        by_family[key] = by_family.get(key, 0) + int(count)

    return {
        "total": int(total),
        "denied": int(denied),
        "by_family": dict(sorted(by_family.items(), key=lambda item: -item[1])),
        "recent": [project_audit_record(row) for row in recent],
    }


# ---------------------------------------------------------------------------
# Security operations summary
# ---------------------------------------------------------------------------


def delegation_is_effective(
    status: str | None, expires_at: datetime | None, *, moment: datetime
) -> bool:
    """Whether a delegation is effective at *moment*, timezone-safely.

    Stored expiry values arrive timezone-aware from PostgreSQL and naive from
    SQLite, so both sides are normalised to UTC before comparison. Comparing an
    aware expiry against a naive "now" raises ``TypeError`` on PostgreSQL, which
    would turn the security and overview routes into a 500 on production.
    """
    if status != "active":
        return False
    expires = as_utc(expires_at)
    reference = as_utc(moment) or moment
    return expires is None or expires > reference


async def security_summary(db: AsyncSession, *, now: datetime | None = None) -> dict:
    """Operators, support delegations and recent privileged activity.

    Built entirely from safe metadata. A platform operator without
    ``platform:content:support`` still cannot read customer content: this summary
    names capabilities, scopes and counts, never content.
    """
    from app.services import access_governance

    moment = as_utc(now) if now is not None else datetime.now(timezone.utc)
    operators = await access_governance.list_platform_operators(db)
    operator_rows = [
        {
            "principal_id": row.principal_id,
            "capability": row.capability,
            "status": row.status,
            "created_at": row.created_at,
            "expires_at": getattr(row, "expires_at", None),
        }
        for row in operators
    ]

    delegations = (
        await db.execute(
            select(SupportDelegation).order_by(SupportDelegation.created_at.desc())
        )
    ).scalars().all()
    delegation_rows = []
    active_content = 0
    expiring = 0
    for row in delegations:
        expires_at = getattr(row, "expires_at", None)
        is_active = delegation_is_effective(row.status, expires_at, moment=moment)
        if is_active and row.scope == "content":
            active_content += 1
        if is_active and expires_at is not None and (as_utc(expires_at) - moment).days <= 7:
            expiring += 1
        delegation_rows.append(
            {
                "tenant_id": row.tenant_id,
                "principal_id": row.principal_id,
                "scope": row.scope,
                "status": row.status,
                "created_at": row.created_at,
                "expires_at": expires_at,
                "effective": bool(is_active),
            }
        )

    privileged = (
        await db.execute(
            select(AuditRecord)
            .where(
                (AuditRecord.action.like("platform%"))
                | (AuditRecord.action.like("governance%"))
                | (AuditRecord.action.like("authorize:platform%"))
            )
            .order_by(AuditRecord.created_at.desc())
            .limit(20)
        )
    ).scalars().all()

    denied_rows = (
        await db.execute(
            select(AuditRecord.action, func.count())
            .where(AuditRecord.decision == "deny")
            .group_by(AuditRecord.action)
            .limit(20)
        )
    ).all()

    suspended = (
        await db.execute(
            select(func.count()).select_from(Tenant).where(Tenant.status == "suspended")
        )
    ).scalar_one()

    return {
        "operators": operator_rows,
        "operator_count": len(operator_rows),
        "support_delegations": delegation_rows,
        "active_content_delegations": active_content,
        "expiring_delegations": expiring,
        "recent_privileged_actions": [project_audit_record(row) for row in privileged],
        "denied_categories": [
            {"action": action, "count": int(count)} for action, count in denied_rows
        ],
        "suspended_tenants": int(suspended),
    }


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


async def diagnostics(
    db: AsyncSession,
    *,
    correlation_id: str | None = None,
    status: str | None = None,
    tool_name: str | None = None,
    tenant_id: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> dict:
    """Bounded operational telemetry: readiness, traces and failure categories."""
    from app.services import readiness as readiness_service

    size = DEFAULT_TRACE_PAGE if limit is None else int(limit)
    size = max(1, min(size, MAX_TRACE_PAGE))
    offset = max(0, int(offset or 0))

    conditions = []
    if correlation_id:
        conditions.append(ExecutionTrace.request_id == correlation_id)
    if status:
        conditions.append(ExecutionTrace.status == status)
    if tool_name:
        conditions.append(ExecutionTrace.tool_name == tool_name)
    if tenant_id:
        conditions.append(ExecutionTrace.tenant_id == tenant_id)

    total = (
        await db.execute(select(func.count()).select_from(ExecutionTrace).where(*conditions))
    ).scalar_one()

    rows = (
        await db.execute(
            select(ExecutionTrace)
            .where(*conditions)
            .order_by(ExecutionTrace.created_at.desc())
            .limit(size)
            .offset(offset)
        )
    ).scalars().all()

    failure_rows = (
        await db.execute(
            select(ExecutionTrace.tool_name, ExecutionTrace.error_class, func.count())
            .where(ExecutionTrace.error_class.is_not(None))
            .group_by(ExecutionTrace.tool_name, ExecutionTrace.error_class)
            .limit(25)
        )
    ).all()

    components = await readiness_service.readiness_components(db)

    return {
        "components": components,
        "ready": readiness_service.overall_ready(components),
        "traces": [project_trace(row) for row in rows],
        "traces_total": int(total),
        "limit": size,
        "offset": offset,
        "failure_categories": [
            {
                "tool_name": tool,
                "error_class": _bounded(error, MAX_REASON_CHARS),
                "count": int(count),
            }
            for tool, error, count in failure_rows
        ],
        "operational_logs": OPERATIONAL_LOG_STATUS,
    }


# ---------------------------------------------------------------------------
# Fleet (tenants + aggregate usage)
# ---------------------------------------------------------------------------


async def _usage_totals(db: AsyncSession) -> dict[str, dict]:
    """Per-tenant aggregate usage, aggregate-only and content-free."""
    rows = (
        await db.execute(
            select(
                ModelUsageEvent.tenant_id,
                func.count(),
                func.coalesce(func.sum(ModelUsageEvent.input_tokens), 0),
                func.coalesce(func.sum(ModelUsageEvent.output_tokens), 0),
                func.coalesce(func.sum(ModelUsageEvent.total_tokens), 0),
            ).group_by(ModelUsageEvent.tenant_id)
        )
    ).all()
    return {
        tenant_id: {
            "requests": int(requests),
            "input_tokens": int(input_tokens),
            "output_tokens": int(output_tokens),
            "total_tokens": int(total_tokens),
        }
        for tenant_id, requests, input_tokens, output_tokens, total_tokens in rows
    }


async def tenant_fleet(db: AsyncSession, *, limit: int | None = None, offset: int = 0) -> dict:
    """Platform fleet view: tenant metadata joined to aggregate usage.

    Metadata-oriented by construction; there is no customer-content field here.
    """
    from app.services import platform_admin

    size = 50 if limit is None else max(1, min(int(limit), 200))
    offset = max(0, int(offset or 0))

    total = (await db.execute(select(func.count()).select_from(Tenant))).scalar_one()
    tenants = (
        await db.execute(
            select(Tenant).order_by(Tenant.created_at).limit(size).offset(offset)
        )
    ).scalars().all()
    usage = await _usage_totals(db)

    fleet = []
    for tenant in tenants:
        totals = usage.get(tenant.id, {})
        fleet.append(
            {
                "id": tenant.id,
                "slug": tenant.slug,
                "name": tenant.name,
                "status": tenant.status,
                "created_at": tenant.created_at,
                "usage": totals,
            }
        )
    return {
        "tenants": fleet,
        "total": int(total),
        "limit": size,
        "offset": offset,
        "has_more": offset + len(tenants) < int(total),
    }


# ---------------------------------------------------------------------------
# Alerts (derived findings, no autonomous remediation)
# ---------------------------------------------------------------------------


def derive_alerts(components: dict[str, dict], *, suspended_tenants: int = 0) -> list[dict]:
    """Bounded operator findings calculated from current health and metadata.

    A finding is advisory: it names a condition and a severity. Nothing here
    performs, or schedules, a remediation.
    """
    findings: list[dict] = []

    def add(severity: str, code: str, component: str, detail: str) -> None:
        findings.append(
            {"severity": severity, "code": code, "component": component, "detail": detail}
        )

    for name, component in components.items():
        status = component.get("status")
        if name == "model_provider" and status in {"degraded", "error"}:
            add("warning", "model_provider_degraded", name, "model provider not healthy")
        elif name == "knowledge" and status == "degraded":
            add("warning", "knowledge_degraded", name, "vector/knowledge store not ready")
        elif name == "connectors" and status == "degraded":
            add("warning", "connector_unhealthy", name, "one or more connectors unhealthy")
        elif name == "scheduler" and status not in {"ok", "disabled", None}:
            add("warning", "scheduler_unhealthy", name, "scheduler not healthy")
        elif name == "database" and status != "ok":
            add("error", "database_unavailable", name, "metadata database not reachable")
        elif name == "migrations" and status != "ok":
            add("error", "migration_drift", name, "schema revision is not at head")
        elif name == "storage" and status == "warning":
            add("warning", "low_storage", name, "low free space on the storage volume")
        elif name == "storage" and status == "error":
            add("error", "storage_unwritable", name, "storage volume not writable")
        elif name == "notifications" and component.get("pending", 0) > 0:
            add(
                "info",
                "notifications_pending",
                name,
                f"{component.get('pending')} notification(s) pending",
            )

    if suspended_tenants:
        add("info", "tenants_suspended", "tenants", f"{suspended_tenants} tenant(s) suspended")

    return findings
