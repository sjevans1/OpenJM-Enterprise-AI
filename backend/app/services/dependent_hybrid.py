"""Deterministic policy-threshold extraction for dependent Hybrid requests.

Retrieved text is inert evidence. This module recognizes a narrow revenue-policy
grammar, evaluates every applicable candidate, and returns one typed value with
its source provenance. It never emits SQL or treats document text as commands.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re

from app.schemas import Evidence

_MAX_POLICY_AMOUNT = Decimal("1000000000000000")


class PolicyThresholdError(ValueError):
    """Policy evidence cannot supply one safe, unambiguous threshold."""


@dataclass(frozen=True)
class PolicyThreshold:
    amount: Decimal
    period: str
    fiscal_year: int | None
    operator: str
    source_id: str
    evidence_id: str | None
    citation: str
    matching_text: str
    field: str = "revenue"
    currency: str | None = None


def is_dependent_revenue_request(message: str) -> bool:
    """Recognize explicit policy-to-revenue dependencies.

    Security boundary: any Hybrid request that combines revenue with a policy /
    rule / document authority term is conservatively routed into the fail-closed
    dependent gate. We do NOT try to prove independence; unknown combinations
    fail closed into the dependent path (and fail closed there too when no
    verifiable policy threshold exists). This closes the keyword-allowlist
    bypass where paraphrases like "qualify based on FY2025 annual revenue"
    would otherwise enter independent Hybrid without a verified threshold.
    """
    lowered = " ".join(message.casefold().split())
    authority_pattern = re.compile(
        r"\b(?:polic(?:y|ies)|documents?|handbooks?|regulations?|procedures?|"
        r"guidelines?|rules?|requirements?|standards?|criteri(?:on|a)|manuals?)\b"
    )
    revenue_pattern = re.compile(
        r"\b(?:revenue|sales|income|earnings|turnover)\b"
    )
    if revenue_pattern.search(lowered) is None:
        return False
    if authority_pattern.search(lowered) is None:
        return False

    # Preserve an explicit independent conjunction: a request to summarize an
    # authority source plus a separate request for an ordinary revenue metric.
    parts = re.split(r"\s+(?:and|plus)\s+", lowered)
    if len(parts) > 1:
        knowledge_directive = re.compile(
            r"\b(?:summari[sz]e|explain|describe|review|what does|tell me about)\b"
        )
        metric_directive = re.compile(
            r"\b(?:show|calculate|compute|give|what is|total|sum|average)\b"
        )
        has_policy_summary = any(
            authority_pattern.search(part) and knowledge_directive.search(part)
            for part in parts
        )
        has_independent_metric = any(
            revenue_pattern.search(part)
            and metric_directive.search(part)
            and authority_pattern.search(part) is None
            for part in parts
        )
        if has_policy_summary and has_independent_metric:
            return False
    return True


_AMOUNT = (
    r"(?P<currency>USD\s*\$?|US\$\s*|JMD\s*\$?|JM\$\s*|\$\s*)"
    r"(?P<amount>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\b"
)
_SCOPE = (
    r"(?:(?:for\s+)?(?P<fiscal_year_pre>FY\s*(?:19|20)\d{2})[,:]?\s*)?"
    r"(?:(?P<period>annual|monthly|quarterly)\s+)?revenue"
)
_YEAR_SUFFIX = (
    r"(?:\s+(?:for|in)\s+(?P<fiscal_year_post>FY\s*(?:19|20)\d{2}))?"
)
_AMOUNT_SUFFIX = r"(?:\s+(?P<operator_suffix>or\s+more|and\s+above))?"
_THRESHOLD = re.compile(
    r"\b"
    + _SCOPE
    + r"\s+(?:threshold|cutoff|minimum|limit)"
    + _YEAR_SUFFIX
    + r"\s*(?::|=|is\s+set\s+at|is|of|at|set\s+at)?\s*"
    + _AMOUNT
    + _AMOUNT_SUFFIX,
    re.IGNORECASE,
)
_COMPARISON = re.compile(
    r"\b"
    + _SCOPE
    + _YEAR_SUFFIX
    + r"\s+(?P<operator_phrase>meets?\s+or\s+exceeds?|exceed(?:s|ing)?|above|over|"
    + r"greater\s+than|more\s+than|is\s+over|is\s+at\s+least|at\s+least|meet(?:s|ing)?|"
    + r">=|>)\s+"
    + _AMOUNT
    + _AMOUNT_SUFFIX,
    re.IGNORECASE,
)
_INCLUSIVE_IS = re.compile(
    r"\b"
    + _SCOPE
    + _YEAR_SUFFIX
    + r"\s+is\s+"
    + _AMOUNT
    + r"\s+(?P<operator_suffix>or\s+more|and\s+above)",
    re.IGNORECASE,
)
_REVERSE = re.compile(
    r"\b(?:minimum|required)\s+"
    + _SCOPE
    + _YEAR_SUFFIX
    + r"\s*(?::|=|is|of|at)?\s*"
    + _AMOUNT
    + _AMOUNT_SUFFIX,
    re.IGNORECASE,
)
_PATTERNS = (_THRESHOLD, _COMPARISON, _INCLUSIVE_IS, _REVERSE)
_AUTHORITY_TERMS = (
    "policy",
    "rule",
    "guideline",
    "standard",
    "requirement",
    "criterion",
    "criteria",
)


def _currency_of(token: str | None) -> str | None:
    if not token:
        return None
    normalized = re.sub(r"\s+", "", token.upper())
    if normalized.startswith(("USD", "US$")):
        return "USD"
    if normalized.startswith(("JMD", "JM$")):
        return "JMD"
    return None


def _to_decimal(raw: str) -> Decimal:
    try:
        amount = Decimal(raw.replace(",", ""))
    except InvalidOperation as exc:
        raise PolicyThresholdError("Invalid policy threshold") from exc
    if not amount.is_finite() or amount <= 0 or amount > _MAX_POLICY_AMOUNT:
        raise PolicyThresholdError("Policy threshold must be finite and bounded")
    if amount.quantize(Decimal("0.01")) != amount:
        raise PolicyThresholdError("Policy threshold has unsupported precision")
    return amount


def _operator_of(phrase: str | None, suffix: str | None) -> str | None:
    normalized_phrase = " ".join((phrase or "").casefold().split())
    normalized_suffix = " ".join((suffix or "").casefold().split())
    if normalized_suffix in {"or more", "and above"}:
        return ">="
    if normalized_phrase in {
        "at least",
        "is at least",
        "meets",
        "meeting",
        "meet or exceed",
        "meets or exceeds",
        ">=",
    }:
        return ">="
    if normalized_phrase in {
        "exceed",
        "exceeds",
        "exceeding",
        "above",
        "over",
        "is over",
        "greater than",
        "more than",
        ">",
    }:
        return ">"
    return None


def _year_of(*raw_values: str | None) -> int | None:
    years = {
        int(re.sub(r"\D", "", value))
        for value in raw_values
        if value is not None
    }
    if len(years) > 1:
        raise PolicyThresholdError("Conflicting fiscal years in policy statement")
    return next(iter(years)) if years else None


def _match_to_candidate(match: re.Match) -> dict:
    groups = match.groupdict()
    return {
        "amount": _to_decimal(match.group("amount")),
        "period": (match.group("period") or "unspecified").lower(),
        "fiscal_year": _year_of(
            groups.get("fiscal_year_pre"), groups.get("fiscal_year_post")
        ),
        "operator": _operator_of(
            groups.get("operator_phrase"), groups.get("operator_suffix")
        ),
        "currency": _currency_of(match.group("currency")),
        "matching_text": match.group(0),
    }


def _all_threshold_matches(text: str) -> list[dict]:
    matches: list[dict] = []
    for pattern in _PATTERNS:
        matches.extend(_match_to_candidate(match) for match in pattern.finditer(text))
    return matches


def _has_policy_authority(item: Evidence) -> bool:
    context = f"{item.title}\n{item.passage}"
    return any(
        re.search(r"\b" + re.escape(term) + r"\b", context, re.IGNORECASE)
        for term in _AUTHORITY_TERMS
    )


def _without_generated_heading_prefix(item: Evidence) -> str:
    """Undo the trusted markdown-ingestion heading rendering, if present."""
    text = item.passage
    headings = {
        value.strip()
        for key in ("heading_path", "Header1")
        if isinstance((value := item.metadata.get(key)), str) and value.strip()
    }
    for heading in headings:
        prefix = f'"{heading}": '
        if text.startswith(prefix):
            return text[len(prefix) :]
    return text


def _is_structurally_safe_policy_passage(item: Evidence) -> bool:
    """Allow headings plus only bounded declarative policy assertions."""
    text = _without_generated_heading_prefix(item)
    statements: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        statements.extend(
            part.strip()
            for part in re.split(r"(?<=[.!?])\s+", stripped)
            if part.strip()
        )
    if not statements:
        return False
    for statement in statements:
        matches = [match for pattern in _PATTERNS for match in pattern.finditer(statement)]
        if not matches:
            return False
        match = matches[0]
        remainder = (statement[: match.start()] + " " + statement[match.end() :])
        remainder = remainder.strip(" \t\r\n.,:;-").casefold()
        if remainder not in {"", "the"}:
            return False
    return True


def fiscal_year_in_request(message: str) -> int | None:
    """Return one requested fiscal year and reject mixed fiscal years."""
    years = {
        int(value)
        for value in re.findall(r"\bFY\s*((?:19|20)\d{2})\b", message, re.IGNORECASE)
    }
    if len(years) > 1:
        raise PolicyThresholdError("Conflicting fiscal years in request")
    return next(iter(years)) if years else None


def period_in_request(message: str) -> str | None:
    """Return one requested reporting period and reject mixed periods."""
    periods = {
        period
        for period in ("annual", "monthly", "quarterly")
        if re.search(r"\b" + period + r"\b", message, re.IGNORECASE)
    }
    if len(periods) > 1:
        raise PolicyThresholdError("Conflicting revenue periods in request")
    return next(iter(periods)) if periods else None


def requested_threshold_operator(message: str) -> str:
    """Resolve conservative comparison semantics without changing > to >=."""
    lowered = " ".join(message.casefold().split())
    if re.search(r"\bmeet(?:s|ing)?\s+or\s+exceed(?:s|ing)?\b", lowered):
        return ">="
    strict = bool(
        re.search(
            r"(?:\b(?:exceed|exceeds|exceeding|above|over)\b|"
            r"\bgreater than\b|\bmore than\b|(?<![<>=])>(?![=]))",
            lowered,
        )
    )
    inclusive = bool(
        re.search(
            r"(?:\bat least\b|\bmeet(?:s|ing)?\b|\bor above\b|"
            r"\bor more\b|\band above\b|>=)",
            lowered,
        )
    )
    if strict and inclusive:
        raise PolicyThresholdError("Conflicting threshold comparison semantics")
    if strict:
        return ">"
    if inclusive:
        return ">="
    raise PolicyThresholdError(
        "Request must state whether to exceed or meet the threshold"
    )


def resolve_revenue_threshold(
    evidence: list[Evidence],
    *,
    requested_period: str | None = None,
    requested_fiscal_year: int | None = None,
    requested_operator: str | None = None,
) -> PolicyThreshold:
    """Resolve one order-independent applicable policy threshold."""
    if requested_period not in (None, "annual", "monthly", "quarterly"):
        raise PolicyThresholdError("Unsupported revenue period")
    if requested_operator not in (None, ">", ">="):
        raise PolicyThresholdError("Unsupported revenue comparison operator")

    candidates: list[PolicyThreshold] = []
    for index, item in enumerate(evidence, start=1):
        if item.source_type != "document":
            continue
        matches = _all_threshold_matches(item.passage)
        if not matches:
            continue
        if not _has_policy_authority(item):
            raise PolicyThresholdError("Revenue statement lacks policy authority")
        if not _is_structurally_safe_policy_passage(item):
            raise PolicyThresholdError("Policy passage contains unsupported content")
        for match in matches:
            if requested_period and match["period"] != requested_period:
                continue
            if (
                requested_fiscal_year is not None
                and match["fiscal_year"] != requested_fiscal_year
            ):
                continue
            effective_operator = match["operator"] or requested_operator
            if effective_operator is None:
                raise PolicyThresholdError(
                    "Policy and request do not establish comparison semantics"
                )
            candidates.append(
                PolicyThreshold(
                    amount=match["amount"],
                    period=match["period"],
                    fiscal_year=match["fiscal_year"],
                    operator=effective_operator,
                    source_id=item.source_id,
                    evidence_id=item.evidence_id,
                    citation=f"[DOC {index}]",
                    matching_text=match["matching_text"],
                    currency=match["currency"],
                )
            )

    if not candidates:
        raise PolicyThresholdError(
            "No applicable currency-denominated revenue threshold in authorized policy evidence"
        )

    signatures = {
        (
            candidate.amount,
            candidate.period,
            candidate.fiscal_year,
            candidate.field,
            candidate.operator,
            candidate.currency,
        )
        for candidate in candidates
    }
    if len(signatures) != 1:
        raise PolicyThresholdError("Conflicting revenue thresholds in policy evidence")

    selected = min(
        candidates,
        key=lambda candidate: (
            candidate.source_id,
            candidate.evidence_id or "",
            candidate.matching_text,
        ),
    )
    if selected.currency is None:
        raise PolicyThresholdError(
            "The policy currency is ambiguous; require explicit USD or JMD"
        )
    if requested_operator and selected.operator != requested_operator:
        raise PolicyThresholdError(
            "The policy comparison operator conflicts with the request"
        )
    return selected


def to_grounded_parameter(threshold: PolicyThreshold) -> dict:
    """Project the verified policy value onto one end-to-end typed contract."""
    if threshold.fiscal_year is not None:
        name = f"fy{threshold.fiscal_year}_{threshold.period}_revenue_threshold"
    else:
        name = f"{threshold.period}_revenue_threshold"
    return {
        "name": name,
        "value": format(threshold.amount, "f"),
        "type": "threshold",
        "field": threshold.field,
        "period": threshold.period,
        "fiscal_year": threshold.fiscal_year,
        "operator": threshold.operator,
        "unit": None,
        "currency": threshold.currency,
        "citation": threshold.citation,
        "matching_text": threshold.matching_text,
        "evidence_id": threshold.evidence_id or threshold.source_id,
        "source_id": threshold.source_id,
    }
