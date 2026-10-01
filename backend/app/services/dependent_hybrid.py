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
    from sqlglot import exp, parse_one

    if proposal.count(POLICY_SQL_MARKER) != 1:
        raise PolicyThresholdError("Planner did not provide exactly one threshold marker")
    try:
        tree = parse_one(proposal)
    except Exception as exc:
        raise PolicyThresholdError("Proposed dependent SQL cannot be parsed") from exc
    if not isinstance(tree, exp.Query):
        raise PolicyThresholdError("Dependent SQL must be a read query")

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

    lhs = comparison.this
    if lhs is None:
        raise PolicyThresholdError("Missing revenue expression")
    revenue_columns = [c.name.casefold() for c in lhs.find_all(exp.Column)]
    if not revenue_columns or not any("revenue" in col for col in revenue_columns):
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
