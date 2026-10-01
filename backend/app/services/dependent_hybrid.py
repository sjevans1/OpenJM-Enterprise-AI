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
