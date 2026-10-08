"""INF1 (Rahkia serving plane) typed contracts.

This is the bounded foundation agreed for INF1-A: the registry identities, the
internal adapter interface, the deterministic routing decision record, the token
and timing normalization rules and the pure aggregate-only export serializer.

Design rules carried from the accepted contract:

* Correlation identity is server generated. ``business_request_id`` covers one
  user turn or workflow operation, ``logical_call_id`` one purpose inside it and
  ``attempt_id`` one actual dispatch.
* Nothing here decides authority from a URL, a name or a client supplied field.
  Tenant identity is always the server resolved value.
* Every failure path denies. There is no branch that upgrades an unknown,
  missing or stale input into an allowance.

This module is deliberately dependency free (no FastAPI, no SQLAlchemy) so the
contract can be reasoned about and reused by services, tests and the API layer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Protocol, Sequence

CONTRACT_VERSION = "rahkia.contract.v1"

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class InferenceMode(str, Enum):
    """Where inference physically runs, relative to the customer."""

    CUSTOMER_LOCAL = "customer_local"
    OPENJM_LOCAL = "openjm_local"
    HOSTED_DEDICATED = "hosted_dedicated"
    HOSTED_SHARED = "hosted_shared"


INFERENCE_MODES: tuple[str, ...] = tuple(mode.value for mode in InferenceMode)

# Shared serving may be *registered* in A but never activated: production
# activation is INF1-B's isolation gate, and registration alone is not
# qualification.
UNQUALIFIED_MODES: frozenset[str] = frozenset({InferenceMode.HOSTED_SHARED.value})

# Modes that a local only (sovereign) binding is allowed to select.
LOCAL_ONLY_MODES: frozenset[str] = frozenset(
    {
        InferenceMode.CUSTOMER_LOCAL.value,
        InferenceMode.OPENJM_LOCAL.value,
    }
)

HOSTED_MODES: frozenset[str] = frozenset(
    {
        InferenceMode.HOSTED_DEDICATED.value,
        InferenceMode.HOSTED_SHARED.value,
    }
)


class DeploymentLifecycle(str, Enum):
    DISABLED = "disabled"
    READY = "ready"
    DRAINING = "draining"
    QUARANTINED = "quarantined"


DEPLOYMENT_LIFECYCLES: tuple[str, ...] = tuple(item.value for item in DeploymentLifecycle)

# Only ``ready`` may be dispatched to at all, and readiness still requires a
# non-stale health observation and a passing policy check.
ELIGIBLE_LIFECYCLES: frozenset[str] = frozenset({DeploymentLifecycle.READY.value})


class BindingStatus(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"


class ConnectivityMode(str, Enum):
    CONNECTED = "connected"
    AIR_GAPPED = "air_gapped"


class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"


class RouteReason(str, Enum):
    """Bounded, content free decision vocabulary (accepted contract)."""

    ALLOWED = "allowed"
    TENANT_INACTIVE = "tenant_inactive"
    BINDING_MISSING = "binding_missing"
    BINDING_REVOKED = "binding_revoked"
    MODEL_NOT_ALLOWED = "model_not_allowed"
    SITE_NOT_ALLOWED = "site_not_allowed"
    CAPABILITY_UNSUPPORTED = "capability_unsupported"
    ISOLATION_UNQUALIFIED = "isolation_unqualified"
    DEPLOYMENT_UNAVAILABLE = "deployment_unavailable"
    HEALTH_STALE = "health_stale"
    REQUEST_TOO_LARGE = "request_too_large"
    CONTEXT_LIMIT = "context_limit"
    POLICY_CHANGED = "policy_changed"


ROUTE_REASONS: tuple[str, ...] = tuple(reason.value for reason in RouteReason)


class CountMethod(str, Enum):
    RUNTIME_REPORTED = "runtime_reported"
    TOKENIZER_ESTIMATE = "tokenizer_estimate"
    CHARACTER_ESTIMATE = "character_estimate"
    UNKNOWN = "unknown"


class ExecutionCertainty(str, Enum):
    """What is known about whether the runtime actually consumed work."""

    NOT_DISPATCHED = "not_dispatched"
    COMPLETED = "completed"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class UsageCompleteness(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class MeasurementOrigin(str, Enum):
    GATEWAY = "gateway"
    RUNTIME = "runtime"
    COLLECTOR = "collector"


class MeasurementScope(str, Enum):
    ATTEMPT = "attempt"
    POOL_WINDOW = "pool_window"


class GpuMethod(str, Enum):
    MEASURED = "measured"
    ALLOCATED = "allocated"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"


class CancelOutcome(str, Enum):
    CONFIRMED_STOPPED = "confirmed_stopped"
    NOT_SUPPORTED = "not_supported"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Errors: every one of these denies
# ---------------------------------------------------------------------------


class InferenceError(RuntimeError):
    """Base class for a refusal to serve."""


class InferenceDenied(InferenceError):
    """A routing or authorization decision denied dispatch.

    ``reason`` is one of :class:`RouteReason`; ``safe_message`` is a bounded
    customer safe category with no inventory, endpoint or credential detail.
    """

    def __init__(self, reason: str, safe_message: str | None = None) -> None:
        self.reason = reason
        self.safe_message = safe_message or _SAFE_MESSAGES.get(
            reason, "The requested inference is not available."
        )
        super().__init__(f"{reason}: {self.safe_message}")


class AttributionError(InferenceError):
    """Usage attribution could not be recorded alongside the M1 event."""


class TelemetryError(InferenceError):
    """A usage or timing value was malformed and cannot be trusted."""


class ExportPolicyDisabled(InferenceError):
    """The aggregate export is switched off (the default)."""


_SAFE_MESSAGES: Mapping[str, str] = {
    RouteReason.TENANT_INACTIVE.value: "This workspace is not active.",
    RouteReason.BINDING_MISSING.value: "No inference deployment is configured for this workspace.",
    RouteReason.BINDING_REVOKED.value: "Inference access for this workspace was withdrawn.",
    RouteReason.MODEL_NOT_ALLOWED.value: "The requested model is not available for this workspace.",
    RouteReason.SITE_NOT_ALLOWED.value: "The requested model is not available at this site.",
    RouteReason.CAPABILITY_UNSUPPORTED.value: "The requested model cannot do this.",
    RouteReason.ISOLATION_UNQUALIFIED.value: (
        "Shared inference is not available until its isolation qualification is complete."
    ),
    RouteReason.DEPLOYMENT_UNAVAILABLE.value: "Inference is temporarily unavailable.",
    RouteReason.HEALTH_STALE.value: "Inference availability could not be confirmed.",
    RouteReason.REQUEST_TOO_LARGE.value: "The request is larger than this workspace allows.",
    RouteReason.CONTEXT_LIMIT.value: "The conversation is longer than the model allows.",
    RouteReason.POLICY_CHANGED.value: "Inference policy changed. Try again.",
    RouteReason.ALLOWED.value: "",
}


# ---------------------------------------------------------------------------
# Normalization: tokens and timing
# ---------------------------------------------------------------------------

_BOOL_AS_INT = "a boolean is not a token count"


def strict_token_count(value: Any, field_name: str) -> int:
    """Coerce one token counter, rejecting anything not a strict non-negative int.

    Booleans are rejected explicitly: ``True`` is an ``int`` in Python and must
    never be accepted as a count of one. Floats raise unless they are integral
    strings, and malformed values are refused rather than silently zeroed, so a
    bad provider payload cannot be recorded as confirmed consumption.
    """
    if isinstance(value, bool):
        raise TelemetryError(f"{field_name}: {_BOOL_AS_INT}")
    if isinstance(value, int):
        result = value
    elif isinstance(value, str) and value.strip().isdigit():
        result = int(value.strip())
    else:
        raise TelemetryError(f"{field_name}: expected a non-negative integer")
    if result < 0:
        raise TelemetryError(f"{field_name}: negative token count")
    return result


@dataclass(frozen=True)
class NormalizedUsage:
    """Token counts plus the provenance needed to interpret them."""

    input_tokens: int
    output_tokens: int
    total_tokens: int
    usage_source: str
    count_method: str
    completeness: str
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None
    tokenizer_revision: str | None = None

    def as_record(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "usage_source": self.usage_source,
            "count_method": self.count_method,
            "completeness": self.completeness,
            "tokenizer_revision": self.tokenizer_revision,
        }


def normalize_usage(
    *,
    input_tokens: Any,
    output_tokens: Any,
    total_tokens: Any = None,
    usage_source: str,
    count_method: str,
    completeness: str | None = None,
    cached_input_tokens: Any = None,
    reasoning_tokens: Any = None,
    tokenizer_revision: str | None = None,
) -> NormalizedUsage:
    """Normalize provider or runtime counters under the accepted rules.

    ``total`` equals input plus output under the adapter's qualified mapping: a
    provider supplied total is not trusted over the components, because cached
    and reasoning subsets must never be added a second time. Cached input is a
    subset of input and reasoning is a subset of output, so neither changes the
    total.
    """
    inputs = strict_token_count(input_tokens, "input_tokens")
    outputs = strict_token_count(output_tokens, "output_tokens")
    derived_total = inputs + outputs

    if total_tokens is not None:
        reported = strict_token_count(total_tokens, "total_tokens")
        if reported != derived_total:
            # A mismatch means the provider's semantics differ from ours; that
            # is an explicit mapping decision, never a silent reconciliation.
            raise TelemetryError(
                "total_tokens disagrees with input + output under the registered mapping"
            )

    cached = (
        None
        if cached_input_tokens is None
        else strict_token_count(cached_input_tokens, "cached_input_tokens")
    )
    reasoning = (
        None
        if reasoning_tokens is None
        else strict_token_count(reasoning_tokens, "reasoning_tokens")
    )
    if cached is not None and cached > inputs:
        raise TelemetryError("cached_input_tokens exceeds input_tokens")
    if reasoning is not None and reasoning > outputs:
        raise TelemetryError("reasoning_tokens exceeds output_tokens")

    if completeness is None:
        completeness = (
            UsageCompleteness.COMPLETE.value
            if usage_source == "provider_reported"
            else UsageCompleteness.UNKNOWN.value
        )
    if count_method == CountMethod.TOKENIZER_ESTIMATE.value and not tokenizer_revision:
        raise TelemetryError("a tokenizer estimate requires a tokenizer revision")

    return NormalizedUsage(
        input_tokens=inputs,
        output_tokens=outputs,
        total_tokens=derived_total,
        cached_input_tokens=cached,
        reasoning_tokens=reasoning,
        usage_source=usage_source,
        count_method=count_method,
        completeness=completeness,
        tokenizer_revision=tokenizer_revision,
    )


@dataclass(frozen=True)
class NormalizedTiming:
    """Timing and infrastructure measurements with explicit provenance.

    Every non token measurement names its origin, scope and quality, and an
    unobservable measurement stays null. Missing values are never rendered as a
    measured zero.
    """

    origin: str = MeasurementOrigin.GATEWAY.value
    scope: str = MeasurementScope.ATTEMPT.value
    quality: str = "unknown"
    gateway_queue_ms: int | None = None
    runtime_queue_ms: int | None = None
    service_latency_ms: int | None = None
    ttft_ms: int | None = None
    generation_ms: int | None = None
    output_tokens_per_second: float | None = None
    gpu_seconds: float | None = None
    gpu_method: str | None = None

    def as_record(self) -> dict[str, Any]:
        return {
            "origin": self.origin,
            "scope": self.scope,
            "quality": self.quality,
            "gateway_queue_ms": self.gateway_queue_ms,
            "runtime_queue_ms": self.runtime_queue_ms,
            "service_latency_ms": self.service_latency_ms,
            "ttft_ms": self.ttft_ms,
            "generation_ms": self.generation_ms,
            "output_tokens_per_second": self.output_tokens_per_second,
            "gpu_seconds": self.gpu_seconds,
            "gpu_method": self.gpu_method,
        }


def non_negative_duration(value: Any, field_name: str) -> int | None:
    """Validate a millisecond duration: finite, non-negative, or absent."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TelemetryError(f"{field_name}: expected a finite duration")
    if value != value or value in (float("inf"), float("-inf")):
        raise TelemetryError(f"{field_name}: duration is not finite")
    if value < 0:
        raise TelemetryError(f"{field_name}: negative duration")
    return int(value)


# ---------------------------------------------------------------------------
# Adapter interface
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InferenceRequest:
    """One bounded logical call. Messages are transient and never persisted."""

    tenant_id: str
    principal_id: str
    business_request_id: str
    logical_call_id: str
    attempt_id: str
    rahkia_alias: str
    max_output_tokens: int
    deadline_at: datetime
    messages: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    parent_attempt_id: str | None = None
    required_capability: str | None = None
    temperature: float = 0.0
    cache_policy_ref: str | None = None
    source_policy_epoch: str | None = None
    contract_version: str = CONTRACT_VERSION


@dataclass(frozen=True)
class ResolvedDeployment:
    """The authorized target. Never serialized to a customer or a log."""

    deployment_id: str
    deployment_revision: int
    model_release_id: str
    model_release_revision: int
    runtime_profile_id: str
    runtime_profile_revision: int
    inference_mode: str
    owner_tenant_id: str | None
    installation_id: str | None = None
    site_id: str | None = None
    endpoint_ref: str | None = None
    credential_ref: str | None = None
    # Operator data: the runtime's own model identifier. Used only inside the
    # runtime call path and never returned to a customer or written to a log.
    runtime_model_ref: str | None = None
    max_context_tokens: int | None = None
    max_output_tokens: int | None = None
    max_request_bytes: int | None = None
    health_observed_at: datetime | None = None


@dataclass(frozen=True)
class InferenceResult:
    attempt_id: str
    text: str
    usage: NormalizedUsage
    timing: NormalizedTiming
    certainty: str = ExecutionCertainty.COMPLETED.value
    model_identity_reported: str | None = None
    finish_reason: str | None = None
    provider_request_ref: str | None = None


@dataclass(frozen=True)
class RuntimeHealth:
    deployment_id: str
    deployment_revision: int
    status: str
    observed_at: datetime
    expires_at: datetime | None = None
    model_present: bool | None = None
    failure_code: str | None = None

    def is_fresh(self, now: datetime) -> bool:
        if self.expires_at is None:
            return False
        expires = self.expires_at if self.expires_at.tzinfo else self.expires_at.replace(
            tzinfo=timezone.utc
        )
        moment = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
        return expires > moment


@dataclass(frozen=True)
class CancelResult:
    outcome: str = CancelOutcome.NOT_SUPPORTED.value


class RuntimeAdapter(Protocol):
    """The internal adapter seam. Implementations never retry or re-route."""

    async def probe(self, target: ResolvedDeployment) -> RuntimeHealth: ...

    async def complete(
        self, request: InferenceRequest, target: ResolvedDeployment
    ) -> InferenceResult: ...

    async def cancel(self, attempt_id: str, target: ResolvedDeployment) -> CancelResult: ...


@dataclass(frozen=True)
class RoutingDecision:
    """An immutable, content free routing record.

    A decision is not an authorization token: it is re-evaluated immediately
    before every dispatch.
    """

    decision_id: str
    attempt_id: str
    tenant_id: str
    reason: str
    decided_at: datetime
    binding_id: str | None = None
    binding_revision: int | None = None
    deployment_id: str | None = None
    deployment_revision: int | None = None
    model_release_id: str | None = None
    model_release_revision: int | None = None
    runtime_profile_id: str | None = None
    runtime_profile_revision: int | None = None

    @property
    def allowed(self) -> bool:
        return self.reason == RouteReason.ALLOWED.value


# ---------------------------------------------------------------------------
# Identifier validation
# ---------------------------------------------------------------------------

_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def validate_opaque_id(value: Any, field_name: str) -> str:
    """Registry identities are opaque server issued values, never names or URLs."""
    if not isinstance(value, str) or not _OPAQUE_ID.match(value):
        raise ValueError(f"{field_name} must be an opaque identifier")
    return value


def validate_rahkia_alias(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("rahkia_alias is required")
    alias = value.strip()
    if "://" in alias or "/" in alias:
        raise ValueError("rahkia_alias must be a name, not a URL or path")
    return alias


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


__all__ = [
    "CONTRACT_VERSION",
    "AttributionError",
    "BindingStatus",
    "CancelOutcome",
    "CancelResult",
    "ConnectivityMode",
    "CountMethod",
    "DeploymentLifecycle",
    "ExecutionCertainty",
    "ExportPolicyDisabled",
    "GpuMethod",
    "HealthStatus",
    "HOSTED_MODES",
    "InferenceDenied",
    "InferenceError",
    "InferenceMode",
    "InferenceRequest",
    "InferenceResult",
    "INFERENCE_MODES",
    "LOCAL_ONLY_MODES",
    "MeasurementOrigin",
    "MeasurementScope",
    "NormalizedTiming",
    "NormalizedUsage",
    "ResolvedDeployment",
    "RouteReason",
    "ROUTE_REASONS",
    "RoutingDecision",
    "RuntimeAdapter",
    "RuntimeHealth",
    "TelemetryError",
    "UNQUALIFIED_MODES",
    "UsageCompleteness",
    "ensure_aware",
    "non_negative_duration",
    "normalize_usage",
    "strict_token_count",
    "utc_now",
    "validate_opaque_id",
    "validate_rahkia_alias",
]
