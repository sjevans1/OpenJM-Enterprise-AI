"""Built-in governed tools.

Every tool here is deterministic, tenant-scoped and permission-gated. Read
tools only ever read. Write tools change a governed setting and report the
previous value so the change can be reconciled or rolled back.

There is deliberately no tool that grants shell, network, filesystem or
arbitrary SQL access: the runtime cannot execute what the registry does not
declare, and nothing here declares such a capability.
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import current_principal
from app.core.identity import Permission
from app.core.permissions import permission_strings_for_role
from app.models import AuditRecord, DataSource, Document, SavedReport
from app.services.actions.registry import (
    OperationClass,
    ParameterSpec,
    RiskLevel,
    ToolRegistry,
    ToolSpec,
)
from app.services.document_lifecycle import retrievable_filter

registry = ToolRegistry()


# ---------------------------------------------------------------------------
# Read tools
# ---------------------------------------------------------------------------


async def _knowledge_search(db: AsyncSession, arguments: dict) -> dict:
    principal = current_principal()
    query = arguments["query"].lower()
    rows = (
        (
            await db.execute(
                select(Document)
                .where(
                    Document.tenant_id == principal.tenant_id,
                    Document.user_id == principal.user_id,
                    *retrievable_filter(),
                )
                .order_by(Document.created_at.desc())
                .limit(200)
            )
        )
        .scalars()
        .all()
    )
    matches = [d for d in rows if query in (d.original_name or "").lower()]
    return {
        "query": arguments["query"],
        "match_count": len(matches),
        "documents": [
            {"document_id": d.id, "name": d.original_name} for d in matches[:20]
        ],
        "scanned": len(rows),
    }


async def _data_source_list(db: AsyncSession, arguments: dict) -> dict:
    principal = current_principal()
    rows = (
        (
            await db.execute(
                select(DataSource).where(
                    DataSource.tenant_id == principal.tenant_id,
                    DataSource.user_id == principal.user_id,
                )
            )
        )
        .scalars()
        .all()
    )
    return {
        "count": len(rows),
        "sources": [
            {
                "source_id": s.id,
                "name": s.name,
                "engine": s.engine,
                "enabled": bool(s.enabled),
                "status": s.status,
                "currency": s.revenue_currency,
            }
            for s in rows
        ],
    }


async def _report_list(db: AsyncSession, arguments: dict) -> dict:
    principal = current_principal()
    rows = (
        (
            await db.execute(
                select(SavedReport)
                .where(
                    SavedReport.tenant_id == principal.tenant_id,
                    SavedReport.user_id == principal.user_id,
                )
                .order_by(SavedReport.created_at.desc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )
    return {
        "count": len(rows),
        "reports": [
            {"report_id": r.id, "title": r.title, "mode": r.requested_mode} for r in rows
        ],
    }


async def _audit_list(db: AsyncSession, arguments: dict) -> dict:
    principal = current_principal()
    limit = int(arguments.get("limit", 20))
    rows = (
        (
            await db.execute(
                select(AuditRecord)
                .where(AuditRecord.tenant_id == principal.tenant_id)
                .order_by(AuditRecord.created_at.desc())
                .limit(max(1, min(limit, 100)))
            )
        )
        .scalars()
        .all()
    )
    return {
        "count": len(rows),
        "records": [
            {
                "action": r.action,
                "decision": r.decision,
                "principal_id": r.principal_id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }


# ---------------------------------------------------------------------------
# Write tools (approval required)
# ---------------------------------------------------------------------------


async def _owned_source(db: AsyncSession, source_id: str) -> DataSource | None:
    principal = current_principal()
    return (
        await db.execute(
            select(DataSource).where(
                DataSource.id == source_id,
                DataSource.tenant_id == principal.tenant_id,
                DataSource.user_id == principal.user_id,
            )
        )
    ).scalars().first()


async def _set_source_currency(db: AsyncSession, arguments: dict) -> dict:
    source = await _owned_source(db, arguments["source_id"])
    if source is None:
        return {
            "ok": False,
            "failure_category": "not_found",
            "detail": "Data source not found in this tenant",
        }
    previous = source.revenue_currency
    source.revenue_currency = arguments["currency"]
    await db.commit()
    return {
        "ok": True,
        "source_id": source.id,
        "previous_currency": previous,
        "currency": source.revenue_currency,
        "reversible": True,
    }


async def _set_source_enabled(db: AsyncSession, arguments: dict) -> dict:
    source = await _owned_source(db, arguments["source_id"])
    if source is None:
        return {
            "ok": False,
            "failure_category": "not_found",
            "detail": "Data source not found in this tenant",
        }
    previous = bool(source.enabled)
    source.enabled = arguments["enabled"]
    await db.commit()
    return {
        "ok": True,
        "source_id": source.id,
        "previous_enabled": previous,
        "enabled": bool(source.enabled),
        "reversible": True,
    }


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def _register_all() -> None:
    registry.register(
        ToolSpec(
            name="knowledge.search",
            description="Search this tenant's indexed knowledge documents by name.",
            operation_class=OperationClass.READ,
            risk_level=RiskLevel.LOW,
            required_permissions=frozenset({Permission.KNOWLEDGE_READ.value}),
            parameters=(ParameterSpec("query", str, max_length=200),),
            timeout_seconds=10,
        ),
        _knowledge_search,
    )
    registry.register(
        ToolSpec(
            name="data_source.list",
            description="List this tenant's governed structured data sources.",
            operation_class=OperationClass.READ,
            risk_level=RiskLevel.LOW,
            required_permissions=frozenset({Permission.DATA_READ.value}),
            timeout_seconds=10,
        ),
        _data_source_list,
    )
    registry.register(
        ToolSpec(
            name="report.list",
            description="List this tenant's saved reports.",
            operation_class=OperationClass.READ,
            risk_level=RiskLevel.LOW,
            required_permissions=frozenset({Permission.REPORTS_READ.value}),
            timeout_seconds=10,
        ),
        _report_list,
    )
    registry.register(
        ToolSpec(
            name="audit.list",
            description="Read recent audit records for this tenant.",
            operation_class=OperationClass.READ,
            risk_level=RiskLevel.MEDIUM,
            required_permissions=frozenset({Permission.AUDIT_READ.value}),
            parameters=(ParameterSpec("limit", int, required=False),),
            timeout_seconds=10,
        ),
        _audit_list,
    )
    registry.register(
        ToolSpec(
            name="data_source.set_currency",
            description=(
                "Set the operator-declared transaction currency of a governed data "
                "source. External mutation: requires explicit human approval."
            ),
            operation_class=OperationClass.WRITE,
            risk_level=RiskLevel.MEDIUM,
            required_permissions=frozenset({Permission.DATA_WRITE.value}),
            parameters=(
                ParameterSpec("source_id", str, max_length=64),
                ParameterSpec("currency", str, max_length=3),
            ),
            timeout_seconds=15,
            reversible=True,
        ),
        _set_source_currency,
    )
    registry.register(
        ToolSpec(
            name="data_source.set_enabled",
            description=(
                "Enable or disable a governed data source. External mutation: "
                "requires explicit human approval."
            ),
            operation_class=OperationClass.WRITE,
            risk_level=RiskLevel.HIGH,
            required_permissions=frozenset({Permission.DATA_WRITE.value}),
            parameters=(
                ParameterSpec("source_id", str, max_length=64),
                ParameterSpec("enabled", bool),
            ),
            timeout_seconds=15,
            reversible=True,
        ),
        _set_source_enabled,
    )

    # VS7 connector tools. Registered here so a single import of this module
    # produces the complete governed tool surface, and so connector operations
    # cannot exist outside the VS6 runtime.
    from app.services.actions.connector_tools import register_connector_tools

    register_connector_tools(registry)

    # BV5-C chat artifact creation. Exposed through the same registry so it is
    # governed, permission-bound, tenant-scoped, idempotent and audited like
    # every other declared capability.
    from app.services.actions.artifact_tools import register_artifact_tools

    register_artifact_tools(registry)


_register_all()


def serialize_result(result: dict) -> str:
    return json.dumps(result, default=str, separators=(",", ":"))
