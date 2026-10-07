"""Immutable LLM usage-metering vocabulary (#46 M1).

Metering is deliberately separated from pricing: these values describe what was
consumed, never what it costs. A later increment converts usage into credits or
currency through versioned plans, without rewriting any historical usage row.
"""

from __future__ import annotations

from enum import Enum


class UsageSource(str, Enum):
    """Where the token counts came from."""

    # The provider returned authoritative usage for the invocation.
    PROVIDER_REPORTED = "provider_reported"
    # The provider returned none (e.g. a local model) and OpenJM estimated it.
    ESTIMATED = "estimated"


class UsageCallRole(str, Enum):
    """How this invocation relates to the others of the same request."""

    PRIMARY = "primary"
    RETRY = "retry"
    FALLBACK = "fallback"


USAGE_SOURCES: tuple[str, ...] = tuple(source.value for source in UsageSource)
USAGE_CALL_ROLES: tuple[str, ...] = tuple(role.value for role in UsageCallRole)

USAGE_STATUS_SUCCEEDED = "succeeded"
USAGE_STATUS_FAILED = "failed"
USAGE_STATUSES: tuple[str, ...] = (USAGE_STATUS_SUCCEEDED, USAGE_STATUS_FAILED)


def estimate_tokens(text: str) -> int:
    """A deterministic, conservative token estimate for provider-less models.

    Roughly four characters per token, rounded up, never negative. Clearly
    marked as an estimate at the row level; never presented as provider-grade.
    """
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)
