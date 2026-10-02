import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

import sqlglot
from sqlglot import exp
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
    "profit",
    "margin",
    "expense",
    "expenses",
    "cost",
    "costs",
    "budget",
    "balance",
    "receivable",
    "receivables",
    "payable",
    "payables",
    "payroll",
    "bonus",
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

    @staticmethod
    def looks_structured(message: str) -> bool:
        lowered = " ".join(message.lower().split())
        if not lowered:
            return False

        definition_like = any(
            lowered.startswith(pattern) for pattern in GENERAL_DEFINITION_PATTERNS
        )
        cue_hits = sum(1 for cue in STRUCTURED_CUES if cue in lowered)
        if definition_like and cue_hits < 2:
            return False
        return cue_hits > 0

    def is_candidate(self, message: str, sources: list[DataSource]) -> bool:
        if self.looks_structured(message):
            return True
        if not sources:
            return False

        lowered = " ".join(message.lower().split())
        message_terms = set(re.findall(r"[a-z0-9]+", lowered))

        # A single schema token can easily be an ordinary English word (for
        # example, a "name" column). Requiring at least two schema-term hits
        # prevents general conversation such as "Hello, my name is Sam" from
        # being misrouted to STRUCTURED while retaining explicit business-cue
        # routing through looks_structured().
        return len(message_terms & self._schema_terms(sources)) >= 2

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

    @staticmethod
    def validates_grounded_parameter(
        plan: StructuredPlan,
        parameter: dict,
    ) -> bool:
        """Require the complete SQL filter to equal the grounded predicate."""
        field = parameter.get("field")
        period = parameter.get("period")
        fiscal_year = parameter.get("fiscal_year")
        expected_operator = parameter.get("operator")
        expected_value = parameter.get("value")
        if (
            field != "revenue"
            or period not in {"annual", "monthly", "quarterly"}
            or expected_operator not in {">", ">="}
            or not isinstance(expected_value, str)
            or re.fullmatch(r"\d+(?:\.\d{1,2})?", expected_value) is None
            or parameter.get("unit") is not None
            or parameter.get("currency") not in {"USD", "JMD"}
        ):
            return False

        if fiscal_year is not None:
            if not isinstance(fiscal_year, int) or not 2000 <= fiscal_year < 2100:
                return False
            expected_column = f"fy{fiscal_year}_{period}_revenue"
            expected_name = f"fy{fiscal_year}_{period}_revenue_threshold"
        else:
            expected_column = f"{period}_revenue"
            expected_name = f"{period}_revenue_threshold"
        if parameter.get("name") != expected_name:
            return False

        try:
            query = sqlglot.parse_one(plan.sql)
        except Exception:
            return False
        if not isinstance(query, exp.Select):
            return False
        if any(
            isinstance(node, (exp.Or, exp.Not, exp.Between, exp.Subquery, exp.Union))
            for node in query.walk()
        ):
            return False
        if query.args.get("having") is not None:
            return False
        tables_in_query = list(query.find_all(exp.Table))
        if len(tables_in_query) != 1 or query.args.get("joins"):
            return False

        where = query.args.get("where")
        if not isinstance(where, exp.Where):
            return False
        comparison_types = {">": exp.GT, ">=": exp.GTE}
        comparison_type = comparison_types[expected_operator]
        comparison = where.this
        if not isinstance(comparison, comparison_type):
            return False

        all_comparisons = [
            node
            for node in query.walk()
            if isinstance(node, (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ, exp.NEQ))
        ]
        if all_comparisons != [comparison]:
            return False

        column = comparison.this
        literal = comparison.expression
        if (
            not isinstance(column, exp.Column)
            or column.name.casefold() != expected_column
            or not isinstance(literal, exp.Literal)
            or literal.is_string
        ):
            return False
        tables = StructuredPlanner._from_tables(query)
        if not tables:
            return False
        qualified = column.table
        if qualified and qualified not in tables:
            return False
        try:
            parsed_value = Decimal(str(literal.this))
        except (InvalidOperation, ValueError):
            return False
        if not parsed_value.is_finite():
            return False
        try:
            expected_decimal = Decimal(str(expected_value))
        except (InvalidOperation, ValueError):
            return False
        if (
            not expected_decimal.is_finite()
            or expected_decimal <= 0
            or expected_decimal > Decimal("1000000000000000")
        ):
            return False
        return parsed_value == expected_decimal

    @staticmethod
    def _from_tables(query: exp.Expression) -> set[str]:
        """Collect every table alias/name declared in the FROM/JOIN graph."""
        found = set()
        for table in query.find_all(exp.Table):
            found.add(table.name.casefold())
            alias = table.alias_or_name
            if alias != table.name:
                found.add(alias.casefold())
        for alias in query.find_all(exp.TableAlias):
            found.add(alias.alias_or_name.casefold())
        return found

    async def plan(
        self,
        message: str,
        db: AsyncSession,
        user_id: str,
    ) -> StructuredPlanningResult:
        sources = await self._sources(db, user_id)
        if not self.is_candidate(message, sources):
            return StructuredPlanningResult(candidate=False)
        if not sources:
            return StructuredPlanningResult(
                candidate=True,
                rationale=(
                    "No authorized enabled structured-data source is currently "
                    "available for this request."
                ),
            )

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
