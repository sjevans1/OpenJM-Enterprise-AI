import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from time import perf_counter
from typing import Any, Protocol
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DataSource, Document
from app.schemas import Evidence
from app.services.execution_trace import (
    complete_execution_trace,
    start_execution_trace,
)
from app.services.knowledge import knowledge_engine
from app.services.structured_executor import execute_structured_query
from app.services.report_scope import (
    ReportSourceScope,
    ReportScopeError,
    source_scope_still_authorized,
)
from app.services.structured_planner import StructuredPlan, StructuredPlanner


class ToolError(RuntimeError):
    pass


class ToolNotFoundError(ToolError):
    pass


class ToolPermissionError(ToolError):
    pass


class ToolApprovalRequiredError(ToolError):
    pass


class ToolInputError(ToolError):
    pass


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    operation_class: str
    risk_level: str
    requires_approval: bool
    required_permissions: frozenset[str] = frozenset()
    input_schema: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolContext:
    user_id: str
    permissions: frozenset[str] = frozenset()
    approval_granted: bool = False
    conversation_id: str | None = None
    request_id: str = field(default_factory=lambda: str(uuid4()))
    route: str | None = None
    requested_mode: str | None = None
    model_name: str | None = None
    db: AsyncSession | None = None
    report_scope: ReportSourceScope | None = None


@dataclass
class ToolResult:
    evidence: list[Evidence] = field(default_factory=list)
    output: dict[str, Any] = field(default_factory=dict)
    trace_metadata: dict[str, Any] = field(default_factory=dict)
    # IDs are populated only by the registry after a successful governed
    # invocation. Callers can therefore bind audit rows without timing scans.
    trace_ids: list[str] = field(default_factory=list)


class Tool(Protocol):
    spec: ToolSpec

    async def execute(
        self,
        context: ToolContext,
        payload: dict[str, Any],
    ) -> ToolResult: ...


def _with_evidence_id(evidence: Evidence) -> Evidence:
    if evidence.evidence_id:
        return evidence
    return evidence.model_copy(update={"evidence_id": str(uuid4())})


class ToolRegistry:
    """Deterministic registry and governance boundary for OpenJM capabilities."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        name = tool.spec.name.strip()
        if not name:
            raise ValueError("Tool name cannot be empty")
        if name in self._tools:
            raise ValueError(f"Tool already registered: {name}")
        self._tools[name] = tool

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolNotFoundError(f"Unknown or unregistered tool: {name}") from exc

    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(tool.spec for tool in self._tools.values())

    @staticmethod
    def _enforce(spec: ToolSpec, context: ToolContext) -> None:
        missing = spec.required_permissions - context.permissions
        if missing:
            raise ToolPermissionError(
                f"Tool {spec.name} requires permissions: {', '.join(sorted(missing))}"
            )
        if spec.requires_approval and not context.approval_granted:
            raise ToolApprovalRequiredError(
                f"Tool {spec.name} requires explicit approval"
            )

    async def execute(
        self,
        name: str,
        context: ToolContext,
        payload: dict[str, Any],
    ) -> ToolResult:
        tool = self.get(name)
        invocation_id = str(uuid4())
        trace = None
        started = perf_counter()
        planned_sql = (
            str(payload.get("sql")) if isinstance(payload.get("sql"), str) else None
        )

        if context.db is not None:
            trace = await start_execution_trace(
                context.db,
                request_id=context.request_id,
                tool_invocation_id=invocation_id,
                user_id=context.user_id,
                conversation_id=context.conversation_id,
                route=context.route,
                requested_mode=context.requested_mode,
                tool_name=tool.spec.name,
                operation_class=tool.spec.operation_class,
                risk_level=tool.spec.risk_level,
                requires_approval=tool.spec.requires_approval,
                model_name=context.model_name,
                payload=payload,
                planned_sql=planned_sql,
            )

        try:
            self._enforce(tool.spec, context)
            result = await tool.execute(context, payload)
            result.evidence = [_with_evidence_id(item) for item in result.evidence]
        except (ToolPermissionError, ToolApprovalRequiredError) as exc:
            if trace is not None and context.db is not None:
                await complete_execution_trace(
                    context.db,
                    trace,
                    status="denied",
                    validation_decision="denied",
                    duration_ms=int((perf_counter() - started) * 1000),
                    error_class=exc.__class__.__name__,
                )
            raise
        except Exception as exc:
            if trace is not None and context.db is not None:
                await complete_execution_trace(
                    context.db,
                    trace,
                    status="failed",
                    duration_ms=int((perf_counter() - started) * 1000),
                    error_class=exc.__class__.__name__,
                )
            raise

        if trace is not None and context.db is not None:
            metadata = result.trace_metadata
            await complete_execution_trace(
                context.db,
                trace,
                status="succeeded",
                source_id=metadata.get("source_id"),
                executed_sql=metadata.get("executed_sql"),
                validation_decision=metadata.get("validation_decision", "allowed"),
                policy_decision=metadata.get("policy_decision"),
                row_limit=metadata.get("row_limit"),
                duration_ms=int((perf_counter() - started) * 1000),
                evidence_ids=[
                    item.evidence_id
                    for item in result.evidence
                    if item.evidence_id is not None
                ],
                processing_location=metadata.get("processing_location"),
                metadata={
                    key: value
                    for key, value in metadata.items()
                    if key
                    not in {
                        "source_id",
                        "executed_sql",
                        "validation_decision",
                        "policy_decision",
                        "row_limit",
                        "processing_location",
                    }
                },
            )
            result.trace_ids.append(trace.id)

        return result


class KnowledgeSearchTool:
    spec = ToolSpec(
        name="knowledge.search",
        description="Retrieve authorized evidence from indexed OpenJM knowledge.",
        operation_class="READ",
        risk_level="LOW",
        requires_approval=False,
        required_permissions=frozenset({"knowledge.read"}),
        input_schema={"query": "string"},
    )

    async def execute(
        self,
        context: ToolContext,
        payload: dict[str, Any],
    ) -> ToolResult:
        query = payload.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ToolInputError("knowledge.search requires a non-empty query")
        if context.db is None:
            raise ToolInputError("knowledge.search requires a database session")

        statement = select(Document).where(
            Document.user_id == context.user_id,
            Document.status == "ready",
            Document.indexed.is_(True),
        )
        if context.report_scope is not None:
            try:
                document_ids = context.report_scope.require_documents()
            except ReportScopeError as exc:
                raise ToolPermissionError(
                    "Report does not permit Knowledge retrieval"
                ) from exc
            statement = statement.where(Document.id.in_(document_ids))
        documents = (
            (await context.db.execute(statement.order_by(Document.created_at.desc())))
            .scalars()
            .all()
        )
        if context.report_scope is not None and (
            {doc.id for doc in documents} != context.report_scope.document_ids
        ):
            raise ToolPermissionError(
                "Pinned Knowledge document is no longer authorized"
            )
        refs = [(item.id, item.original_name) for item in documents]
        authorized_source_ids = {document_id for document_id, _ in refs}
        evidence = await knowledge_engine.retrieve(
            query,
            refs,
            neighbor_primary_limit=(
                knowledge_engine.settings.rag_neighbor_primary_limit
            ),
            neighbor_max_chunks=knowledge_engine.settings.rag_neighbor_max_chunks,
        )
        normalized = [
            item.model_copy(
                update={
                    "evidence_id": item.evidence_id or str(uuid4()),
                    "provenance": {
                        **item.provenance,
                        "tool": self.spec.name,
                        "retrieval_mode": (
                            "exact_chunk_id"
                            if item.provenance.get("retrieval_role") == "neighbor"
                            else "semantic"
                        ),
                    },
                    "access_context": {
                        **item.access_context,
                        "user_id": context.user_id,
                    },
                    "processing_location": item.processing_location or "local",
                    "observed_at": item.observed_at or datetime.now(timezone.utc),
                }
            )
            for item in evidence
        ]
        for item in normalized:
            # Retrieved provenance is untrusted, even if the vector search was
            # restricted to known document collections. Reject malformed
            # equivalent-source records instead of silently skipping them.
            equivalents = item.provenance.get("equivalent_sources", [])
            if (
                item.source_type != "document"
                or not isinstance(equivalents, list)
                or len(equivalents) > 24
            ):
                raise ToolPermissionError("Knowledge evidence provenance is invalid")
            equivalent_source_ids: set[str] = set()
            for equivalent in equivalents:
                if (
                    not isinstance(equivalent, dict)
                    or not isinstance(equivalent.get("source_id"), str)
                    or not equivalent["source_id"]
                ):
                    raise ToolPermissionError(
                        "Knowledge evidence provenance is invalid"
                    )
                equivalent_source_ids.add(equivalent["source_id"])
            if (
                item.source_id not in authorized_source_ids
                or not equivalent_source_ids.issubset(authorized_source_ids)
            ):
                raise ToolPermissionError(
                    "Knowledge retrieval returned evidence outside the authorized set"
                )
        return ToolResult(
            evidence=normalized,
            output={
                "evidence_count": len(normalized),
                "primary_evidence_count": sum(
                    item.provenance.get("retrieval_role") == "primary"
                    for item in normalized
                ),
                "neighbor_evidence_count": sum(
                    item.provenance.get("retrieval_role") == "neighbor"
                    for item in normalized
                ),
                "deduplicated_count": sum(
                    max(int(item.provenance.get("duplicate_count", 1)) - 1, 0)
                    for item in normalized
                    if item.provenance.get("retrieval_role") == "primary"
                ),
                "evidence_limit": (
                    knowledge_engine.settings.rag_top_k
                    + knowledge_engine.settings.rag_neighbor_max_chunks
                ),
            },
            trace_metadata={
                "processing_location": "local",
                "primary_evidence_count": sum(
                    item.provenance.get("retrieval_role") == "primary"
                    for item in normalized
                ),
                "neighbor_evidence_count": sum(
                    item.provenance.get("retrieval_role") == "neighbor"
                    for item in normalized
                ),
            },
        )


class StructuredQueryTool:
    spec = ToolSpec(
        name="structured.query",
        description="Execute one governed read-only query against an authorized source.",
        operation_class="READ",
        risk_level="MODERATE",
        requires_approval=False,
        required_permissions=frozenset({"structured.read"}),
        input_schema={
            "source_id": "string",
            "sql": "string",
            "grounded_parameter": "object (optional)",
        },
    )

    @staticmethod
    def _grounded_parameter(payload: dict[str, Any]) -> dict[str, Any] | None:
        value = payload.get("grounded_parameter")
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ToolInputError("grounded_parameter must be an object")

        required_strings = (
            "name",
            "type",
            "field",
            "period",
            "currency",
            "citation",
            "matching_text",
            "evidence_id",
            "source_id",
        )
        if any(
            not isinstance(value.get(key), str) or not value[key]
            for key in required_strings
        ):
            raise ToolInputError("grounded_parameter identity is invalid")
        raw_value = value.get("value")
        if (
            not isinstance(raw_value, str)
            or re.fullmatch(r"\d+(?:\.\d{1,2})?", raw_value) is None
        ):
            raise ToolInputError(
                "grounded_parameter value must be a canonical decimal string"
            )
        candidate_value = Decimal(raw_value)
        if (
            not candidate_value.is_finite()
            or candidate_value <= 0
            or candidate_value > Decimal("1000000000000000")
        ):
            raise ToolInputError("grounded_parameter value must be finite and bounded")
        if value.get("operator") not in {">", ">="}:
            raise ToolInputError("grounded_parameter operator is invalid")
        if value.get("unit") is not None and not isinstance(value["unit"], str):
            raise ToolInputError("grounded_parameter unit is invalid")

        fiscal_year = value.get("fiscal_year")
        if fiscal_year is not None and (
            not isinstance(fiscal_year, int) or not 2000 <= fiscal_year < 2100
        ):
            raise ToolInputError("grounded_parameter fiscal_year is invalid")
        if value["field"] != "revenue":
            raise ToolInputError("grounded_parameter field is unsupported")
        if value["period"] not in {"annual", "monthly", "quarterly"}:
            raise ToolInputError("grounded_parameter period is invalid")
        if value["currency"] not in {"USD", "JMD"}:
            raise ToolInputError("grounded_parameter currency is invalid")

        return {
            "name": value["name"],
            "value": value["value"],
            "type": value["type"],
            "field": value["field"],
            "period": value["period"],
            "fiscal_year": fiscal_year,
            "operator": value["operator"],
            "unit": value.get("unit"),
            "currency": value["currency"],
            "citation": value["citation"],
            "matching_text": value["matching_text"],
            "evidence_id": value["evidence_id"],
            "source_id": value["source_id"],
        }

    async def execute(
        self,
        context: ToolContext,
        payload: dict[str, Any],
    ) -> ToolResult:
        if context.db is None:
            raise ToolInputError("structured.query requires a database session")

        source_id = payload.get("source_id")
        sql = payload.get("sql")
        grounded_parameter = self._grounded_parameter(payload)
        if not isinstance(source_id, str) or not source_id:
            raise ToolInputError("structured.query requires source_id")
        if not isinstance(sql, str) or not sql.strip():
            raise ToolInputError("structured.query requires SQL")
        scoped_tables = None
        if context.report_scope is not None:
            try:
                scoped_tables = context.report_scope.tables_for(source_id)
            except ReportScopeError as exc:
                raise ToolPermissionError(
                    "Report does not permit this data source"
                ) from exc
            if grounded_parameter is not None and (
                grounded_parameter["source_id"] not in context.report_scope.document_ids
            ):
                raise ToolPermissionError("Report does not permit this policy document")

        result = await context.db.execute(
            select(DataSource).where(
                DataSource.id == source_id,
                DataSource.user_id == context.user_id,
            )
        )
        source = result.scalars().first()
        if source is None:
            raise ToolPermissionError("Data source is unavailable or unauthorized")
        if scoped_tables is not None and not source_scope_still_authorized(
            source, scoped_tables
        ):
            raise ToolPermissionError("Pinned table grant or schema has changed")

        if grounded_parameter is not None:
            document_result = await context.db.execute(
                select(Document).where(
                    Document.id == grounded_parameter["source_id"],
                    Document.user_id == context.user_id,
                    Document.status == "ready",
                    Document.indexed.is_(True),
                )
            )
            if document_result.scalars().first() is None:
                raise ToolInputError(
                    "Grounded policy source is unavailable or unauthorized"
                )
            if (
                not source.enabled
                or source.status != "connected"
                or source.revenue_currency != grounded_parameter["currency"]
            ):
                raise ToolInputError(
                    "Structured source is unavailable or has incompatible currency"
                )
            grounded_plan = StructuredPlan(
                source_id=source.id,
                sql=sql,
                rationale="tool-boundary grounding validation",
            )
            if not StructuredPlanner.validates_grounded_parameter(
                grounded_plan, grounded_parameter
            ):
                raise ToolInputError(
                    "SQL does not preserve the verified grounded policy predicate"
                )

        query_result = (
            await execute_structured_query(source, sql)
            if scoped_tables is None
            else await execute_structured_query(
                source, sql, scoped_tables=scoped_tables
            )
        )
        preview_rows = [list(row) for row in query_result.rows[:20]]
        passage = json.dumps(
            {
                "columns": list(query_result.columns),
                "rows": preview_rows,
                "row_count": query_result.row_count,
                "truncated": query_result.truncated,
            },
            default=str,
        )

        evidence = Evidence(
            evidence_id=str(uuid4()),
            source_type="structured_query",
            source_id=source.id,
            title=source.name,
            passage=passage,
            provenance={
                "tool": self.spec.name,
                "engine": source.engine,
                "executed_sql": query_result.sql,
                **(
                    {"grounded_parameter": grounded_parameter}
                    if grounded_parameter
                    else {}
                ),
            },
            access_context={"user_id": context.user_id},
            processing_location="local",
            observed_at=datetime.now(timezone.utc),
            metadata={
                "sql": query_result.sql,
                "columns": list(query_result.columns),
                "row_count": query_result.row_count,
                "truncated": query_result.truncated,
                "row_limit": query_result.policy.row_limit,
                "tables": list(query_result.policy.tables),
            },
        )

        return ToolResult(
            evidence=[evidence],
            output={
                "columns": list(query_result.columns),
                "rows": [list(row) for row in query_result.rows],
                "row_count": query_result.row_count,
                "truncated": query_result.truncated,
            },
            trace_metadata={
                "source_id": source.id,
                "executed_sql": query_result.sql,
                "validation_decision": "allowed",
                "policy_decision": "read_only_allowed",
                "row_limit": query_result.policy.row_limit,
                "processing_location": "local",
                "tables": list(query_result.policy.tables),
                "truncated": query_result.truncated,
                **(
                    {"grounded_parameter": grounded_parameter}
                    if grounded_parameter
                    else {}
                ),
            },
        )


def build_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(KnowledgeSearchTool())
    registry.register(StructuredQueryTool())
    return registry


tool_registry = build_tool_registry()
