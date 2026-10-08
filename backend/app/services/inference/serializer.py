"""The pure, aggregate-only telemetry export serializer (``aggregate_only_v1``).

This module serializes. It sends nothing, opens no socket and knows no endpoint.
Export policy defaults to off, and while it is off the serializer refuses to
produce a payload at all.

Unknown fields are rejected at every nesting level, so a caller cannot smuggle a
prompt, a response, a content hash, a principal identifier or a request
identifier into an outbound payload by adding a key the schema does not know.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from app.core.inference import ExportPolicyDisabled

SCHEMA_VERSION = "aggregate_only_v1"

# The complete field set, nesting included. Anything outside these sets is a
# hard error rather than silently dropped.
_ENVELOPE_FIELDS = frozenset(
    {
        "schema_version",
        "export_id",
        "installation_id",
        "sequence",
        "commercial_tenant_ref",
        "window_start",
        "window_end",
        "generated_at",
        "telemetry_policy_revision",
        "signing_key_id",
        "rows",
        "correction_of",
        "payload_digest",
        "signature",
    }
)

_ROW_FIELDS = frozenset(
    {
        "rahkia_alias",
        "inference_mode",
        "deployment_export_ref",
        "usage_quality",
        "attempts",
        "succeeded",
        "failed",
        "retries",
        "fallbacks",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cached_input_tokens",
        "reasoning_tokens",
        "unknown_usage_count",
        "latency_buckets",
        "gpu_seconds",
        "gpu_method",
        "gpu_coverage",
    }
)

# Field names that must never appear anywhere in a payload, even nested, because
# they are content derived or identify a person or a single call.
FORBIDDEN_FIELDS = frozenset(
    {
        "prompt",
        "prompts",
        "response",
        "responses",
        "content",
        "messages",
        "message_id",
        "conversation_id",
        "principal_id",
        "user_id",
        "request_id",
        "business_request_id",
        "logical_call_id",
        "attempt_id",
        "parent_attempt_id",
        "usage_event_id",
        "prompt_hash",
        "response_hash",
        "content_hash",
        "endpoint",
        "endpoint_ref",
        "credential",
        "credential_ref",
        "api_key",
        "token",
        "tenant_id",
    }
)


class ExportSchemaError(ValueError):
    """The payload does not match the aggregate-only schema."""


@dataclass(frozen=True)
class ExportPolicy:
    """Signed local configuration controlling what may leave the installation."""

    enabled: bool = False
    telemetry_policy_revision: str | None = None
    signing_key_id: str | None = None
    allowed_dimensions: frozenset[str] = field(
        default_factory=lambda: frozenset({"rahkia_alias", "inference_mode", "usage_quality"})
    )


@dataclass(frozen=True)
class ExportRow:
    """One bounded aggregate row. Counters only, never a per-call row."""

    rahkia_alias: str
    inference_mode: str | None = None
    deployment_export_ref: str | None = None
    usage_quality: str = "unknown"
    attempts: int = 0
    succeeded: int = 0
    failed: int = 0
    retries: int = 0
    fallbacks: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None
    unknown_usage_count: int = 0
    latency_buckets: Mapping[str, int] | None = None
    gpu_seconds: float | None = None
    gpu_method: str | None = None
    gpu_coverage: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "rahkia_alias": self.rahkia_alias,
            "inference_mode": self.inference_mode,
            "deployment_export_ref": self.deployment_export_ref,
            "usage_quality": self.usage_quality,
            "attempts": self.attempts,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "retries": self.retries,
            "fallbacks": self.fallbacks,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "unknown_usage_count": self.unknown_usage_count,
            "latency_buckets": dict(self.latency_buckets) if self.latency_buckets else None,
            "gpu_seconds": self.gpu_seconds,
            "gpu_method": self.gpu_method,
            "gpu_coverage": self.gpu_coverage,
        }


def _ensure_no_forbidden_keys(value: Any, *, path: str = "$") -> None:
    """Reject forbidden names at any depth, including inside mapping values."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ExportSchemaError(f"{path}: non string key {key!r}")
            lowered = key.lower()
            if lowered in FORBIDDEN_FIELDS:
                raise ExportSchemaError(f"{path}.{key}: field is not allowed in an export payload")
            _ensure_no_forbidden_keys(item, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _ensure_no_forbidden_keys(item, path=f"{path}[{index}]")


def _ensure_known_fields(value: Mapping[str, Any], allowed: frozenset[str], *, path: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ExportSchemaError(f"{path}: unknown fields {unknown}")


def build_aggregate_export(
    *,
    policy: ExportPolicy,
    export_id: str,
    installation_id: str,
    sequence: int,
    commercial_tenant_ref: str,
    window_start: datetime,
    window_end: datetime,
    rows: Sequence[ExportRow],
    correction_of: str | None = None,
    generated_at: datetime | None = None,
    payload_digest: str | None = None,
    signature: str | None = None,
) -> dict[str, Any]:
    """Build one bounded ``aggregate_only_v1`` payload, or refuse.

    Refuses when the policy is not enabled. The returned mapping is a fresh
    structure: no caller object is mutated and no field is added that the schema
    does not define.
    """
    if not policy.enabled:
        raise ExportPolicyDisabled("aggregate telemetry export is disabled")

    if window_end < window_start:
        raise ExportSchemaError("window_end precedes window_start")

    dimension_names = {
        "rahkia_alias": "rahkia_alias",
        "inference_mode": "inference_mode",
        "deployment_export_ref": "deployment_export_ref",
        "usage_quality": "usage_quality",
    }
    for name in policy.allowed_dimensions:
        if name not in dimension_names:
            raise ExportSchemaError(f"policy allows an unknown dimension {name!r}")

    row_dicts: list[dict[str, Any]] = []
    for row in rows:
        raw = row.as_dict()
        _ensure_known_fields(raw, _ROW_FIELDS, path="$.rows[]")
        if row.rahkia_alias not in (None, ""):
            if "rahkia_alias" not in policy.allowed_dimensions:
                raw["rahkia_alias"] = None
        if "inference_mode" not in policy.allowed_dimensions:
            raw["inference_mode"] = None
        if "deployment_export_ref" not in policy.allowed_dimensions:
            raw["deployment_export_ref"] = None
        if "usage_quality" not in policy.allowed_dimensions:
            raw["usage_quality"] = None
        row_dicts.append(raw)

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "export_id": export_id,
        "installation_id": installation_id,
        "sequence": sequence,
        "commercial_tenant_ref": commercial_tenant_ref,
        "window_start": window_start.astimezone(timezone.utc).isoformat(),
        "window_end": window_end.astimezone(timezone.utc).isoformat(),
        "generated_at": (generated_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(),
        "telemetry_policy_revision": policy.telemetry_policy_revision,
        "signing_key_id": policy.signing_key_id,
        "rows": row_dicts,
        "correction_of": correction_of,
        "payload_digest": payload_digest,
        "signature": signature,
    }

    _ensure_known_fields(payload, _ENVELOPE_FIELDS, path="$")
    _ensure_no_forbidden_keys(payload)
    return payload


def validate_export_payload(payload: Mapping[str, Any]) -> None:
    """Validate an already built payload against the aggregate-only schema.

    Raises when the envelope carries an unknown field, when any nesting level
    carries a forbidden name, or when a row is not a mapping. This is the check
    the exporter lane must run before signing or sending anything.
    """
    if not isinstance(payload, Mapping):
        raise ExportSchemaError("payload must be a mapping")
    _ensure_known_fields(payload, _ENVELOPE_FIELDS, path="$")
    rows = payload.get("rows")
    if rows is not None:
        if not isinstance(rows, (list, tuple)):
            raise ExportSchemaError("$.rows must be a list")
        for row in rows:
            if not isinstance(row, Mapping):
                raise ExportSchemaError("$.rows[] must be a mapping")
            _ensure_known_fields(row, _ROW_FIELDS, path="$.rows[]")
    _ensure_no_forbidden_keys(payload)


def canonical_payload_bytes(payload: Mapping[str, Any]) -> bytes:
    """A deterministic encoding for digesting, excluding the envelope signatures.

    The digest covers everything except ``payload_digest`` and ``signature`` so a
    signature cannot cover itself.
    """
    import json

    covered = {k: v for k, v in payload.items() if k not in {"payload_digest", "signature"}}
    return json.dumps(covered, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


__all__ = [
    "SCHEMA_VERSION",
    "ExportPolicy",
    "ExportRow",
    "ExportSchemaError",
    "build_aggregate_export",
    "canonical_payload_bytes",
    "validate_export_payload",
]
