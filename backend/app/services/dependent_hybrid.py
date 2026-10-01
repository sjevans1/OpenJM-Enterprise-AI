"""Deterministic policy-threshold extraction for the dependent Hybrid gate.

This module does not turn documents into instructions or SQL.  It recognizes a
small, explicit policy grammar, binds one unambiguous fact to one authorized
document citation, and fails closed for unknown scope or conflicting facts.
The structured execution boundary must independently validate and bind the
value before any dependent query runs.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re

from app.schemas import Evidence


class PolicyThresholdError(ValueError):
    """Policy evidence cannot supply one safe, unambiguous threshold."""


@dataclass(frozen=True)
class PolicyThreshold:
    amount: Decimal
    period: str
    source_id: str
    evidence_id: str | None
    citation: str
    matching_text: str


# Only request an evidence-dependent threshold when all three concepts are
# explicit: the business metric, a threshold, and document/policy authority.
# Ordinary independent Hybrid questions must retain their current behavior.
def is_dependent_revenue_request(message: str) -> bool:
    lowered = " ".join(message.casefold().split())
    return (
        "revenue" in lowered
        and any(term in lowered for term in ("threshold", "minimum", "cutoff"))
        and any(term in lowered for term in ("policy", "document", "guideline"))
    )


# Numeric thresholds must have explicit currency; otherwise a passage might
# confuse a percentage, an order count, a year, or a revenue amount.
_AMOUNT = r"(?P<currency>USD\s*\$?|US\$\s*|\$\s*)(?P<amount>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?)\b"
_FORWARD = re.compile(
    r"\b(?:(?P<period>annual|monthly|quarterly)\s+)?revenue\s+"
    r"(?:(?:eligibility|minimum|qualification)\s+)?"
    r"(?:threshold|cutoff|minimum)\s*(?::|=|is|of|at|set\s+at)?\s*"
    + _AMOUNT,
    re.IGNORECASE,
)
_REVERSE = re.compile(
    r"\b(?:minimum|required)\s+"
    r"(?:(?P<period>annual|monthly|quarterly)\s+)?"
    r"revenue\s*(?::|=|is|of|at)?\s*" + _AMOUNT,
    re.IGNORECASE,
)


def resolve_revenue_threshold(
    evidence: list[Evidence], *, requested_period: str | None = None
) -> PolicyThreshold:
    """Find exactly one policy revenue threshold with matching period.

    The citation index is derived from the same document-order list used by
    the Hybrid prompt. Repeated identical mentions are allowed. Contradictory
    values or periods, non-document evidence, missing currency, and unspecific
    periods for explicitly periodic requests fail closed.
    """
    if requested_period not in (None, "annual", "monthly", "quarterly"):
        raise PolicyThresholdError("Unsupported revenue period")

    candidates: list[PolicyThreshold] = []
    for index, item in enumerate(evidence, start=1):
        if item.source_type != "document":
            continue
        for line in item.passage.splitlines():
            for pattern in (_FORWARD, _REVERSE):
                for match in pattern.finditer(line):
                    try:
                        amount = Decimal(match.group("amount").replace(",", ""))
                    except InvalidOperation as exc:
                        raise PolicyThresholdError("Invalid policy threshold") from exc
                    if not amount.is_finite() or amount <= 0:
                        raise PolicyThresholdError("Policy threshold must be positive")
                    period = (match.group("period") or "unspecified").lower()
                    candidates.append(
                        PolicyThreshold(
                            amount=amount,
                            period=period,
                            source_id=item.source_id,
                            evidence_id=item.evidence_id,
                            citation=f"[DOC {index}]",
                            matching_text=match.group(0),
                        )
                    )

    if not candidates:
        raise PolicyThresholdError(
            "No explicit currency-denominated revenue threshold in authorized policy evidence"
        )

    signatures = {(c.amount, c.period) for c in candidates}
    if len(signatures) != 1:
        raise PolicyThresholdError("Conflicting revenue thresholds in policy evidence")

    selected = candidates[0]
    if requested_period and selected.period != requested_period:
        raise PolicyThresholdError(
            "The policy does not establish the requested revenue period"
        )
    return selected


def period_in_request(message: str) -> str | None:
    """Reject mixed periods instead of silently choosing one."""
    periods = {
        period
        for period in ("annual", "monthly", "quarterly")
        if re.search(r"\b" + period + r"\b", message, re.IGNORECASE)
    }
    if len(periods) > 1:
        raise PolicyThresholdError("Conflicting revenue periods in request")
    return next(iter(periods)) if periods else None


POLICY_SQL_MARKER = "__OPENJM_POLICY_THRESHOLD__"


def requested_threshold_operator(message: str) -> str:
    """Conservative supported comparison semantics (no silent > vs >= changes)."""
    lowered = message.casefold()
    strict = bool(re.search(r"\b(exceed|exceeds|exceeding|above|greater than|more than)\b", lowered))
    inclusive = bool(re.search(r"\b(at least|meet|meets|meeting|or above)\b", lowered))
    if strict and inclusive:
        raise PolicyThresholdError("Conflicting threshold comparison semantics")
    if strict:
        return ">"
    if inclusive:
        return ">="
    raise PolicyThresholdError("Request must state whether to exceed or meet the threshold")


def bind_document_threshold_sql(
    proposal: str,
    threshold: PolicyThreshold,
    *,
    requested_period: str | None,
    operator: str,
) -> str:
    """Bind a verified decimal into a narrowly validated, model-proposed SQL AST.

    Only a literal identifier marker on the right side of the requested revenue
    comparison can be replaced.  No policy passage is ever concatenated into SQL.
    The independent SQL-policy validator still checks the final SQL for DDL,
    DML, unknown objects and columns, row bounds and read-only execution.
    """
    from sqlglot import exp, parse

    if proposal.count(POLICY_SQL_MARKER) != 1:
        raise PolicyThresholdError("Planner did not provide exactly one threshold marker")
    try:
        statements = [node for node in parse(proposal) if node is not None]
    except Exception as exc:
        raise PolicyThresholdError("Proposed dependent SQL cannot be parsed") from exc
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise PolicyThresholdError("Dependent SQL must be exactly one SELECT")
    tree = statements[0]
    # The initial C3 gate supports one simple threshold condition. Complex
    # boolean branches or unions could broaden a query beyond the policy rule.
    if any(tree.find(node) for node in (exp.Or, exp.Union, exp.Except, exp.Intersect)):
        raise PolicyThresholdError("Ambiguous dependent query logic")
    predicates = [
        node for node in tree.find_all(exp.Predicate)
        if isinstance(node, (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ, exp.NEQ))
    ]
    if len(predicates) != 1:
        raise PolicyThresholdError("Dependent query requires one comparison only")

    columns = [
        node for node in tree.find_all(exp.Column)
        if node.name == POLICY_SQL_MARKER and not node.table
    ]
    if len(columns) != 1:
        raise PolicyThresholdError("Threshold marker is not an independent SQL value")
    marker = columns[0]
    comparison = marker.parent
    if not isinstance(comparison, (exp.GT, exp.GTE)) or comparison.expression is not marker:
        raise PolicyThresholdError("Threshold must be the right operand of a comparison")
    if (isinstance(comparison, exp.GT) and operator != ">") or (
        isinstance(comparison, exp.GTE) and operator != ">="
    ):
        raise PolicyThresholdError("Proposed SQL changed the requested threshold operator")

    conditions = [
        clause for clause in (tree.args.get("where"), tree.args.get("having"))
        if clause is not None
    ]
    if len(conditions) != 1 or conditions[0].this is not comparison:
        raise PolicyThresholdError(
            "Dependent SQL must use the verified threshold as its only filter"
        )
    if list(tree.find_all(exp.Join)):
        raise PolicyThresholdError("C3 does not infer threshold semantics across joins")

    lhs = comparison.this
    if not isinstance(lhs, exp.Column):
        raise PolicyThresholdError("Only direct revenue column comparison is supported")
    revenue_columns = [lhs.name.casefold()]
    if not any("revenue" in col for col in revenue_columns):
        raise PolicyThresholdError("Threshold is not compared against a revenue column")
    if requested_period and not any(
        requested_period in column for column in revenue_columns
    ):
        # Refuse to assume an annual/monthly/quarterly metric from an
        # unspecified total or to infer period filters from arbitrary SQL.
        raise PolicyThresholdError("SQL does not prove the requested revenue period")

    marker.replace(exp.Literal.number(str(threshold.amount)))
    rendered = tree.sql()
    if POLICY_SQL_MARKER in rendered:
        raise PolicyThresholdError("Unbound policy marker")
    return rendered


# C3 supports one deliberately narrow audited query shape. We never allow a
# document to introduce a table, field, operator, or SQL fragment.
@dataclass(frozen=True)
class DependentRevenueQuery:
    sql: str
    source_id: str
    year: int
    operator: str
    threshold: PolicyThreshold


def compile_customer_revenue_query(
    message: str, threshold: PolicyThreshold, sources: list
) -> DependentRevenueQuery:
    """Prepare a deterministic read-only completed-orders query.

    No model-generated SQL or source-document SQL is executed. A single year,
    qualifying operator, completed-order scope, matching period, and exactly
    one schema with the required relations are mandatory.
    """
    from app.services.data_sources import decode_schema

    if threshold.period != "annual":
        raise PolicyThresholdError(
            "Dependent revenue queries currently require an annual policy threshold"
        )
    lowered = message.lower()
    if not re.search(r"\\bcompleted(?:[- ]order[s]?)?\\b", lowered):
        raise PolicyThresholdError(
            "Specify completed orders; the request does not establish a revenue status"
        )
    year_matches = re.findall(r"(?<!\\d)(?:19|20)\\d{2}(?!\\d)", message)
    years = set(year_matches)
    if len(years) != 1:
        raise PolicyThresholdError("Specify exactly one calendar year")
    year = int(next(iter(years)))
    if year >= 2099 or year < 2000:
        raise PolicyThresholdError("Calendar year is outside supported bounds")
    requested_period = period_in_request(message)
    if requested_period and requested_period != threshold.period:
        raise PolicyThresholdError("The requested period differs from the policy")
    if re.search(r"\\b(?:exceed|exceeds|exceeding|above|over|greater than)\\b", lowered):
        operator = ">"
    elif re.search(r"\\b(?:at least|meet|meets|meeting|minimum of)\\b", lowered):
        operator = ">="
    else:
        raise PolicyThresholdError("Specify whether to exceed or meet the threshold")

    required = {
        "customers": {"id", "name"},
        "orders": {"id", "customer_id", "order_date", "status"},
        "order_items": {"order_id", "quantity", "unit_price"},
    }
    candidates = []
    for source in sources:
        tables = {
            t.name.lower(): {c.name.lower() for c in t.columns}
            for t in decode_schema(source.schema_json)
        }
        if all(required[name].issubset(tables.get(name, set())) for name in required):
            # The query runner still verifies source ownership/permissions and
            # every table and column against the configured authorized set.
            candidates.append(source)

    if len(candidates) != 1:
        raise PolicyThresholdError(
            "Requires exactly one authorized sales source with the known schema"
        )
    if not candidates[0].enabled or candidates[0].status != "connected":
        raise PolicyThresholdError("Sales source is unavailable")

    # Decimal is parsed solely from the currency-bound numeric grammar.
    # Rendering only Decimal digits and a decimal point cannot introduce SQL.
    number = format(threshold.amount, "f")
    if not re.fullmatch(r"\\d+(?:\\.\\d{1,2})?", number):
        raise PolicyThresholdError("Unsupported numeric threshold")
    sql = (
        "SELECT c.name AS customer_name, "
        "ROUND(SUM(oi.quantity * oi.unit_price), 2) AS completed_order_revenue "
        "FROM customers AS c "
        "JOIN orders AS o ON o.customer_id = c.id "
        "JOIN order_items AS oi ON oi.order_id = o.id "
        f"WHERE o.status = 'completed' AND o.order_date >= '{year:04d}-01-01' "
        f"AND o.order_date < '{year + 1:04d}-01-01' "
        "GROUP BY c.id, c.name "
        f"HAVING SUM(oi.quantity * oi.unit_price) {operator} {number} "
        "ORDER BY completed_order_revenue DESC LIMIT 100"
    )
    return DependentRevenueQuery(
        sql=sql,
        source_id=candidates[0].id,
        year=year,
        operator=operator,
        threshold=threshold,
    )
