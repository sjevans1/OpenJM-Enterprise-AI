"""The typed runtime adapter seam and the OpenAI-compatible implementation.

The adapter is a transport boundary, not a policy boundary. It never retries,
never picks a different tenant, model or site, never infers authority from a URL
and never reinterprets a missing entitlement as approval. Routing already
decided what may run; the adapter only performs the call and reports what
actually happened.

Two profiles are registered explicitly (the current local endpoint and a
private-hosted Rahkia). Engine specific extensions belong inside a profile, and
passing these contract fixtures is not engine or hardware qualification.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol, Sequence

from app.core.inference import (
    CONTRACT_VERSION,
    CancelOutcome,
    CancelResult,
    CountMethod,
    ExecutionCertainty,
    InferenceError,
    InferenceRequest,
    InferenceResult,
    NormalizedTiming,
    ResolvedDeployment,
    RuntimeHealth,
    TelemetryError,
    normalize_usage,
    non_negative_duration,
)

LOCAL_ENDPOINT_PROFILE = "openai_compatible_local_v1"
PRIVATE_HOSTED_PROFILE = "openai_compatible_private_hosted_v1"

RUNTIME_PROFILES: tuple[str, ...] = (LOCAL_ENDPOINT_PROFILE, PRIVATE_HOSTED_PROFILE)


class TransportError(InferenceError):
    """The runtime could not be reached or did not answer usefully.

    ``certainty`` records what is known: a transport timeout is not proof that
    nothing was consumed, so it is reported as unknown rather than as zero usage.
    """

    def __init__(
        self,
        message: str,
        *,
        certainty: str = ExecutionCertainty.UNKNOWN.value,
        failure_code: str = "transport_error",
        usage: Any = None,
        timing: NormalizedTiming | None = None,
    ) -> None:
        super().__init__(message)
        self.certainty = certainty
        self.failure_code = failure_code
        self.usage = usage
        self.timing = timing


class ModelMismatchError(InferenceError):
    """The runtime answered with a model that is not the registered one.

    The observed usage is retained on the error so the ledger still records what
    was actually consumed, while the response itself is refused.
    """

    def __init__(self, message: str, *, usage: Any, timing: NormalizedTiming) -> None:
        super().__init__(message)
        self.usage = usage
        self.timing = timing
        self.certainty = ExecutionCertainty.PARTIAL.value


class ChatTransport(Protocol):
    """The only place a network call happens. Tests inject a fake."""

    async def chat_completion(
        self,
        *,
        endpoint_ref: str | None,
        credential_ref: str | None,
        payload: Mapping[str, Any],
        timeout_s: float,
    ) -> Mapping[str, Any]: ...

    async def health(self, *, endpoint_ref: str | None, credential_ref: str | None) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class AdapterProfile:
    """A pinned adapter/profile binding. No unpinned ``latest`` is accepted."""

    profile_ref: str
    adapter_kind: str
    adapter_version: str
    usage_method: str
    cancellation_supported: bool = False


_ADAPTER_KIND = "openai_compatible"
_ADAPTER_VERSION = "1.0.0"

PROFILE_REGISTRY: dict[str, AdapterProfile] = {
    LOCAL_ENDPOINT_PROFILE: AdapterProfile(
        profile_ref=LOCAL_ENDPOINT_PROFILE,
        adapter_kind=_ADAPTER_KIND,
        adapter_version=_ADAPTER_VERSION,
        usage_method=CountMethod.CHARACTER_ESTIMATE.value,
        cancellation_supported=False,
    ),
    PRIVATE_HOSTED_PROFILE: AdapterProfile(
        profile_ref=PRIVATE_HOSTED_PROFILE,
        adapter_kind=_ADAPTER_KIND,
        adapter_version=_ADAPTER_VERSION,
        usage_method=CountMethod.RUNTIME_REPORTED.value,
        cancellation_supported=False,
    ),
}


def resolve_profile(profile_ref: str) -> AdapterProfile:
    profile = PROFILE_REGISTRY.get(profile_ref)
    if profile is None:
        raise InferenceError(f"unknown adapter profile {profile_ref!r}")
    return profile


def build_chat_payload(request: InferenceRequest, target: ResolvedDeployment) -> dict[str, Any]:
    """Build the outbound payload from the authorized request and target only.

    A caller cannot supply the model, the destination or a security header: those
    come from the resolved registry target, not from the request.
    """
    if not target.runtime_model_ref:
        raise InferenceError("resolved deployment has no runtime model reference")
    return {
        "model": target.runtime_model_ref,
        "messages": [dict(message) for message in request.messages],
        "temperature": request.temperature,
        "max_tokens": request.max_output_tokens,
        "stream": False,
    }


def _normalize_from_response(
    response: Mapping[str, Any], *, usage_method: str
) -> tuple[Any, NormalizedTiming, str | None, str | None]:
    usage_block = response.get("usage")
    timing = NormalizedTiming(origin="gateway", quality="measured")
    if isinstance(usage_block, Mapping) and usage_block:
        try:
            usage = normalize_usage(
                input_tokens=usage_block.get("prompt_tokens"),
                output_tokens=usage_block.get("completion_tokens"),
                total_tokens=usage_block.get("total_tokens"),
                usage_source="provider_reported",
                count_method=CountMethod.RUNTIME_REPORTED.value,
            )
        except TelemetryError:
            # A malformed usage block is refused rather than recorded as zero.
            raise
    else:
        # No usage returned. This is an estimate and is labelled as one.
        text = response.get("choices")
        characters = 0
        if isinstance(text, Sequence) and text:
            first = text[0]
            if isinstance(first, Mapping):
                message = first.get("message")
                if isinstance(message, Mapping) and isinstance(message.get("content"), str):
                    characters = len(message["content"])
        usage = normalize_usage(
            input_tokens=0,
            output_tokens=characters // 4,
            usage_source="estimated",
            count_method=usage_method,
            completeness="unknown",
            tokenizer_revision=None,
        )
        timing = NormalizedTiming(origin="gateway", quality="estimated")

    finish_reason = None
    choices = response.get("choices")
    if isinstance(choices, Sequence) and choices:
        first = choices[0]
        if isinstance(first, Mapping):
            finish_reason = first.get("finish_reason")
    reported_model = response.get("model")
    return usage, timing, finish_reason, reported_model


class OpenAICompatibleAdapter:
    """Buffered completion and probing over one OpenAI compatible transport."""

    def __init__(self, transport: ChatTransport, *, profile_ref: str = PRIVATE_HOSTED_PROFILE) -> None:
        self._transport = transport
        self._profile = resolve_profile(profile_ref)

    @property
    def profile(self) -> AdapterProfile:
        return self._profile

    async def probe(self, target: ResolvedDeployment) -> RuntimeHealth:
        observed = datetime.now(timezone.utc)
        try:
            answer = await self._transport.health(
                endpoint_ref=target.endpoint_ref, credential_ref=target.credential_ref
            )
        except Exception:  # noqa: BLE001 - any failure is an unknown observation
            return RuntimeHealth(
                deployment_id=target.deployment_id,
                deployment_revision=target.deployment_revision,
                status="unknown",
                observed_at=observed,
                expires_at=None,
                model_present=None,
                failure_code="probe_failed",
            )
        models = answer.get("models") if isinstance(answer, Mapping) else None
        present = None
        if isinstance(models, Sequence):
            present = target.runtime_model_ref in [str(item) for item in models]
        return RuntimeHealth(
            deployment_id=target.deployment_id,
            deployment_revision=target.deployment_revision,
            status="healthy" if present is not False else "degraded",
            observed_at=observed,
            expires_at=None,
            model_present=present,
            failure_code=None,
        )

    async def complete(
        self, request: InferenceRequest, target: ResolvedDeployment
    ) -> InferenceResult:
        payload = build_chat_payload(request, target)
        remaining = (
            request.deadline_at.astimezone(timezone.utc) - datetime.now(timezone.utc)
        ).total_seconds()
        if remaining <= 0:
            raise TransportError(
                "deadline elapsed before dispatch",
                certainty=ExecutionCertainty.NOT_DISPATCHED.value,
                failure_code="deadline_elapsed",
            )

        started = time.monotonic()
        completion = await self._transport.chat_completion(
            endpoint_ref=target.endpoint_ref,
            credential_ref=target.credential_ref,
            payload=payload,
            timeout_s=min(remaining, 120.0),
        )
        elapsed_ms = int((time.monotonic() - started) * 1000)

        usage, timing, finish_reason, reported_model = _normalize_from_response(
            completion, usage_method=self._profile.usage_method
        )
        timing = NormalizedTiming(
            origin=timing.origin,
            quality=timing.quality,
            service_latency_ms=non_negative_duration(elapsed_ms, "service_latency_ms"),
        )

        if reported_model is not None and target.runtime_model_ref is not None:
            if str(reported_model) != str(target.runtime_model_ref):
                raise ModelMismatchError(
                    "runtime answered with an unexpected model", usage=usage, timing=timing
                )

        text = ""
        choices = completion.get("choices")
        if isinstance(choices, Sequence) and choices:
            first = choices[0]
            if isinstance(first, Mapping):
                message = first.get("message")
                if isinstance(message, Mapping) and isinstance(message.get("content"), str):
                    text = message["content"]

        return InferenceResult(
            attempt_id=request.attempt_id,
            text=text,
            usage=usage,
            timing=timing,
            certainty=ExecutionCertainty.COMPLETED.value,
            model_identity_reported=str(reported_model) if reported_model is not None else None,
            finish_reason=str(finish_reason) if finish_reason is not None else None,
        )

    async def cancel(self, attempt_id: str, target: ResolvedDeployment) -> CancelResult:
        # HTTP client disconnect is not proof that a worker stopped, so the
        # adapter reports honestly rather than claiming a termination it cannot
        # confirm. INF1-B owns the capacity and recovery policy that follows.
        if not self._profile.cancellation_supported:
            return CancelResult(outcome=CancelOutcome.NOT_SUPPORTED.value)
        return CancelResult(outcome=CancelOutcome.UNKNOWN.value)


__all__ = [
    "AdapterProfile",
    "ChatTransport",
    "LOCAL_ENDPOINT_PROFILE",
    "ModelMismatchError",
    "OpenAICompatibleAdapter",
    "PRIVATE_HOSTED_PROFILE",
    "PROFILE_REGISTRY",
    "RUNTIME_PROFILES",
    "TransportError",
    "build_chat_payload",
    "resolve_profile",
]
