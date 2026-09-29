import json
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DataSource
from app.services.data_sources import decode_schema
from app.services.model_gateway import (
    ModelGatewayError,
    OpenAICompatibleModelGateway,
)


class StructuredPlannerError(RuntimeError):
    pass


@dataclass(frozen=True)
class StructuredPlan:
    source_id: str
    sql: str
    rationale: str


@dataclass(frozen=True)
class StructuredPlanningResult:
    candidate: bool
    plan: StructuredPlan | None = None
    rationale: str = ""


STRUCTURED_CUES = (
    "revenue",
    "sales",
    "orders",
    "order ",
    "customers",
    "customer ",
    "products",
    "product ",
    "inventory",
    "stock",
    "invoice",
    "invoices",
    "quantity",
    "unit price",
    "total ",
    "sum ",
    "average",
    "avg ",
    "highest",
    "lowest",
    "top ",
    "bottom ",
    "how many",
    "count ",
    "by region",
    "by customer",
    "by product",
    "last month",
    "this month",
    "last quarter",
    "this quarter",
    "last year",
    "this year",
    "database",
    "data source",
)

GENERAL_DEFINITION_PATTERNS = (
    "what is ",
    "define ",
    "explain ",
)


class StructuredPlanner:
    """Creates one bounded read-only SQL proposal from authorized schema context."""

    def __init__(self) -> None:
        self.model_gateway = OpenAICompatibleModelGateway()

    async def _sources(
        self,
        db: AsyncSession,
        user_id: str,
    ) -> list[DataSource]:
        result = await db.execute(
            select(DataSource)
            .where(
                DataSource.user_id == user_id,
                DataSource.enabled.is_(True),
                DataSource.status == "connected",
                DataSource.schema_json.is_not(None),
            )
            .order_by(DataSource.created_at.asc())
        )
        return list(result.scalars().all())

    @staticmethod
    def _schema_terms(sources: list[DataSource]) -> set[str]:
        terms: set[str] = set()
        for source in sources:
            terms.update(
                token
                for token in re.findall(r"[a-z0-9]+", source.name.lower())
                if len(token) > 2
            )
            for table in decode_schema(source.schema_json):
                terms.update(
                    token
                    for token in re.findall(
                        r"[a-z0-9]+",
                        f"{table.name} {table.qualified_name}".lower(),
                    )
                    if len(token) > 2
                )
                for column in table.columns:
                    terms.update(
                        token
                        for token in re.findall(r"[a-z0-9]+", column.name.lower())
                        if len(token) > 2
                    )
        return terms

    def is_candidate(self, message: str, sources: list[DataSource]) -> bool:
        if not sources:
            return False

        lowered = " ".join(message.lower().split())
        if not lowered:
            return False

        definition_like = any(
            lowered.startswith(pattern) for pattern in GENERAL_DEFINITION_PATTERNS
        )
        cue_hits = sum(1 for cue in STRUCTURED_CUES if cue in lowered)
        if definition_like and cue_hits < 2:
            return False

        message_terms = set(re.findall(r"[a-z0-9]+", lowered))
        schema_overlap = bool(message_terms & self._schema_terms(sources))

        return cue_hits > 0 or schema_overlap

    @staticmethod
    def _schema_context(sources: list[DataSource]) -> str:
        blocks: list[str] = []
        for source in sources[:8]:
            lines = [
                f"SOURCE_ID: {source.id}",
                f"SOURCE_NAME: {source.name}",
                f"ENGINE: {source.engine}",
                "TABLES:",
            ]
            for table in decode_schema(source.schema_json)[:60]:
                column_text = ", ".join(
                    f"{column.name} {column.type}" for column in table.columns[:40]
                )
                lines.append(f"- {table.qualified_name}({column_text})")
                for fk in table.foreign_keys:
                    lines.append(
                        "  FK "
                        + ",".join(fk.constrained_columns)
                        + " -> "
                        + (
                            f"{fk.referred_schema}."
                            if fk.referred_schema
                            else ""
                        )
                        + fk.referred_table
                        + "("
                        + ",".join(fk.referred_columns)
                        + ")"
                    )
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)

    @staticmethod
    def _extract_json(raw: str) -> dict:
        text = raw.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
            text = re.sub(r"\s*```$", "", text)

        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise StructuredPlannerError("Structured planner did not return JSON")

        try:
            payload = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise StructuredPlannerError(
                "Structured planner returned invalid JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise StructuredPlannerError("Structured planner JSON must be an object")
        return payload

    async def plan(
        self,
        message: str,
        db: AsyncSession,
        user_id: str,
    ) -> StructuredPlanningResult:
        sources = await self._sources(db, user_id)
        if not self.is_candidate(message, sources):
            return StructuredPlanningResult(candidate=False)

        allowed_ids = {source.id for source in sources}
        prompt = (
            "You are OpenJM's bounded Structured Data planner. "
            "Decide whether the USER QUESTION can be answered from exactly one of the "
            "authorized relational schemas below using one read-only SELECT/CTE query. "
            "Do not answer the user's question. Do not invent tables, columns, source IDs "
            "or values. Never produce INSERT, UPDATE, DELETE, DDL, PRAGMA, ATTACH, COPY, "
            "CALL, shell commands, or multiple SQL statements. "
            "If the question cannot be answered from the listed schema, return use_structured=false. "
            "Return ONLY JSON with exactly these keys: "
            '{"use_structured":true|false,"source_id":"...","sql":"...","rationale":"..."}. '
            "When use_structured=false, source_id and sql must be empty strings.\n\n"
            f"AUTHORIZED SCHEMAS\n{self._schema_context(sources)}\n\n"
            f"USER QUESTION\n{message}"
        )

        try:
            raw = await self.model_gateway.chat(
                [{"role": "system", "content": prompt}],
                temperature=0.0,
                max_tokens=700,
            )
        except ModelGatewayError as exc:
            raise StructuredPlannerError(str(exc)) from exc

        payload = self._extract_json(raw)
        use_structured = payload.get("use_structured")
        if use_structured is not True:
            rationale = payload.get("rationale", "")
            return StructuredPlanningResult(
                candidate=True,
                rationale=rationale if isinstance(rationale, str) else "",
            )

        source_id = payload.get("source_id")
        sql = payload.get("sql")
        rationale = payload.get("rationale", "")

        if not isinstance(source_id, str) or source_id not in allowed_ids:
            raise StructuredPlannerError(
                "Structured planner selected an unauthorized or unknown source"
            )
        if not isinstance(sql, str) or not sql.strip():
            raise StructuredPlannerError("Structured planner returned empty SQL")
        if not isinstance(rationale, str):
            rationale = ""

        return StructuredPlanningResult(
            candidate=True,
            plan=StructuredPlan(
                source_id=source_id,
                sql=sql.strip(),
                rationale=rationale.strip(),
            ),
            rationale=rationale.strip(),
        )


structured_planner = StructuredPlanner()
