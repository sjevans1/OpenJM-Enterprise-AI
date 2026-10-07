"""OpenJM model gateway: OpenAI-compatible chat with bounded output validation.

Phase B reliability design (docs/MODEL_GATEWAY_RELIABILITY.md):

1. Validation distinguishes a VALID answer from MALFORMED model output
   using proportion / repetition / structure rules over known
   tokenizer-control-token families. A legitimate answer that merely
   quotes a token-like string once is never rejected.

2. Bounded recovery: first generation with the caller's settings; if the
   output is malformed, ONE retry with deterministic settings
   (temperature 0.0, bounded max_tokens) plus deployment-configurable
   request extras (default enables the Gemma 4 template's thinking
   channel, the established root-cause fix for this llama-server build).
   If the retry is still malformed, a controlled ModelGatewayError is
   raised. No unlimited retries, no token stripping, no fabricated
   answers.

3. Malformed content is never returned and therefore never persisted as
   an assistant message by the API layer (chat.py raises 502 on
   ModelGatewayError before the assistant message is created). Raw model
   response bodies are never logged.
"""

import json
import logging
from datetime import datetime, timezone
import re
import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from app.core.config import get_settings
from app.services.report_runs import BudgetExceeded, ReportRunBudget

logger = logging.getLogger("openjm.model")

# Stable, client-safe provider failure categories (Issue #38 backend-only
# provider isolation). A provider failure is reported as one of these, never as
# raw exception text: an httpx error string can carry the private provider
# URL/host/path and, on some paths, credential material. The API response, the
# public probes and the metrics therefore carry a category at most; the
# operator log carries the category plus a sanitised, bounded detail.
FAILURE_CATEGORIES = ("timeout", "auth_rejected", "unreachable", "bad_response")

_SAFE_FAILURE_MESSAGES: dict[str, str] = {
    "timeout": "The model provider did not respond within the configured timeout.",
    "auth_rejected": "The model provider rejected the deployment credential.",
    "unreachable": "The model provider is unreachable.",
    "bad_response": "The model provider returned an unusable response.",
}

_URL_PATTERN = re.compile(r"https?://[^\s'\"<>()]+")


class ModelGatewayError(RuntimeError):
    """A controlled model-gateway failure that is safe to surface to a client.

    ``category`` is one of :data:`FAILURE_CATEGORIES`; ``str(error)`` is always a
    stable, non-sensitive message and never contains the provider URL/host/path
    or the credential.
    """

    def __init__(self, message: str, *, category: str = "bad_response") -> None:
        self.category = category if category in FAILURE_CATEGORIES else "bad_response"
        # Set by the gateway when a provider attempt consumed tokens but failed,
        # so the caller can persist the usage evidence after its own rollback.
        self.pending_usage: dict | None = None
        super().__init__(message)


def provider_failure(category: str) -> ModelGatewayError:
    """Build a controlled error carrying the safe message for *category*."""
    safe = category if category in FAILURE_CATEGORIES else "bad_response"
    return ModelGatewayError(_SAFE_FAILURE_MESSAGES[safe], category=safe)


def classify_provider_error(exc: BaseException) -> str:
    """Map a transport/HTTP failure to a stable, non-sensitive category."""
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code if exc.response is not None else None
        if status in (401, 403):
            return "auth_rejected"
        return "bad_response"
    if isinstance(exc, (httpx.TransportError, httpx.ConnectError, OSError)):
        return "unreachable"
    return "bad_response"


def sanitise_provider_text(text: str, *, base_url: str = "", api_key: str = "") -> str:
    """Redact provider addresses and credential material from operator text.

    Defence in depth: even the operator-only log must not echo the private
    provider address or the bearer credential. Known values are replaced first,
    then any remaining URL-like token is collapsed; the result is bounded.
    """
    cleaned = text
    for secret in (base_url, api_key):
        secret = (secret or "").strip()
        if secret:
            cleaned = cleaned.replace(secret, "[redacted]")
    cleaned = _URL_PATTERN.sub("[redacted-url]", cleaned)
    return cleaned[:300]


# ── tokenizer control-token families (generic across local models) ─────────
# These are tokenizer/control tokens, not ordinary angle-bracket text:
#   <unused42>            Gemma unused vocabulary entries
#   <|tool_call|> <|\"|>   full pipe-delimited control tokens
#   <|tool_call> <turn|>   half-pipe variants used by Gemma 4 turn markers
#   <bos> <eos> <think>    llama/Gemma special tokens
# Plain HTML like <div> or math like "x < 10" does NOT match any pattern.

_SPECIAL_TOKEN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"<unused\d+>"),
    re.compile(r"<\|[^<>|]*\|>"),
    re.compile(r"<\|[^<>|]*>"),
    re.compile(r"<[^<>|]*\|>"),
    re.compile(r"</?think>"),
    re.compile(r"<(?:bos|eos|start_of_turn|end_of_turn)>"),
)

# Validation thresholds. An answer is malformed when control tokens make up
# a substantial proportion of it, or when a single control token repeats
# pathologically, or when generation opens with a run of control tokens.
_MAX_DENSITY = 0.34
_MAX_SINGLE_REPEAT = 8
_MAX_LEADING_RUN = 3


@dataclass(frozen=True)
class OutputValidation:
    """Result of validating raw model output. Never mutates the content."""

    ok: bool
    reason: str | None
    density: float
    max_repeat: int

    @property
    def failure_reason(self) -> str:
        return self.reason or "unspecified validation failure"


def validate_model_output(content: str) -> OutputValidation:
    """Classify raw model output as a valid answer or malformed generation.

    Rules (proportion / repetition / structure — no naive substring test):

    - empty / whitespace-only content is invalid;
    - control-token density above ``_MAX_DENSITY`` is invalid;
    - any single control token repeated ``_MAX_SINGLE_REPEAT`` or more
      times is invalid;
    - a leading run of ``_MAX_LEADING_RUN``+ consecutive control tokens is
      invalid;
    - anything else — including prose that quotes a token-like string a
      few times — is valid.
    """

    if not isinstance(content, str) or not content.strip():
        return OutputValidation(False, "empty or whitespace-only content", 0.0, 0)

    total = len(content)
    covered = 0
    repeats: dict[str, int] = {}
    for pattern in _SPECIAL_TOKEN_PATTERNS:
        for match in pattern.finditer(content):
            covered += match.end() - match.start()
            literal = match.group(0)
            repeats[literal] = repeats.get(literal, 0) + 1

    density = covered / total if total else 0.0
    max_repeat = max(repeats.values(), default=0)

    if density > _MAX_DENSITY:
        return OutputValidation(
            False,
            f"control-token density {density:.2f} exceeds {_MAX_DENSITY}",
            round(density, 4),
            max_repeat,
        )
    if max_repeat >= _MAX_SINGLE_REPEAT:
        literal = max(repeats, key=repr if False else repeats.get)  # type: ignore[arg-type]
        return OutputValidation(
            False,
            f"control token {literal!r} repeated {max_repeat} times",
            round(density, 4),
            max_repeat,
        )
    if content.lstrip().startswith("<"):
        # structural rule: generation opening with a run of control tokens
        stripped = content.lstrip()
        run = 0
        position = 0
        while run < _MAX_LEADING_RUN:
            matched = False
            for pattern in _SPECIAL_TOKEN_PATTERNS:
                match = pattern.match(stripped, position)
                if match:
                    position = match.end()
                    run += 1
                    matched = True
                    break
            if not matched:
                break
        if run >= _MAX_LEADING_RUN:
            return OutputValidation(
                False,
                f"answer opens with a run of {run} control tokens",
                round(density, 4),
                max_repeat,
            )

    return OutputValidation(True, None, round(density, 4), max_repeat)


def _prompt_text(payload: dict) -> str:
    """Concatenated request message text, for a deterministic token estimate."""
    messages = payload.get("messages") if isinstance(payload, dict) else None
    if not isinstance(messages, list):
        return ""
    return "\n".join(
        item.get("content")
        for item in messages
        if isinstance(item, dict) and isinstance(item.get("content"), str)
    )


class OpenAICompatibleModelGateway:
    """One model contract for Hermes, Ollama, vLLM and similar endpoints."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self._retry_extras = self._parse_retry_extras(
            self.settings.model_retry_request_extras
        )

    @staticmethod
    def _parse_retry_extras(raw: str | None) -> dict[str, Any]:
        """Parse deployment-configurable retry request extras (JSON object).

        Default enables the Gemma 4 chat-template thinking channel, the
        established fix for malformed non-thinking generation on the
        current llama-server build. Set OPENJM_MODEL_RETRY_REQUEST_EXTRAS={}
        to disable, or to supply different per-request fields for another
        OpenAI-compatible runtime. Unknown fields are ignored by tolerant
        servers (llama.cpp, vLLM, Ollama); a strict server rejecting the
        retry fails closed into a controlled ModelGatewayError.
        """

        if raw is None or not raw.strip():
            return {}
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    async def _post_json(self, url: str, headers: dict, payload: dict) -> tuple[int, dict]:
        """POST the chat payload and return (status, parsed body).

        Isolated as a method so tests can substitute a fake transport;
        also the single place raw bodies exist (they are never logged).
        """

        async with httpx.AsyncClient(
            timeout=self.settings.model_timeout_seconds
        ) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            return response.status_code, response.json()

    def _endpoint(self) -> str:
        base_url = self.settings.model_base_url.rstrip("/")
        return f"{base_url}/chat/completions"

    def _models_endpoint(self) -> str:
        base_url = self.settings.model_base_url.rstrip("/")
        return f"{base_url}/models"

    async def probe(self) -> dict:
        """Report provider health WITHOUT leaking the credential.

        Returns provider mode, model identifier and a bounded status. A timeout
        or unavailability is a reported ``error`` component, never an exception:
        this is deliberately separate from core process liveness (VS8 D). The
        bearer credential is never included, and the endpoint host is reduced to
        a scheme classification so a private internal hostname is not disclosed.
        """
        import httpx

        from app.core.observability import METRICS

        mode = (self.settings.model_provider_mode or "local").strip()
        transport = "https" if self._models_endpoint().lower().startswith("https") else "http"
        component: dict[str, object] = {
            "mode": mode,
            "model": self.settings.model_name,
            "transport": transport,
            "configured": bool((self.settings.model_base_url or "").strip()),
            "fallback_policy": self.settings.model_provider_fallback,
        }
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                timeout=min(float(self.settings.model_timeout_seconds), 5.0)
            ) as client:
                response = await client.get(self._models_endpoint(), headers=self._headers())
                response.raise_for_status()
                body = response.json()
            ids = [
                item.get("id")
                for item in (body.get("data") or [])
                if isinstance(item, dict)
            ]
            component["status"] = "ok"
            component["model_present"] = self.settings.model_name in ids if ids else None
        except Exception as exc:  # noqa: BLE001 - reported, never raised
            component["status"] = "error"
            component["detail"] = type(exc).__name__
        finally:
            METRICS.histogram(
                "openjm_model_request_duration_seconds",
                time.perf_counter() - started,
                {"op": "probe"},
            )
        return component

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.settings.model_api_key:
            headers["Authorization"] = f"Bearer {self.settings.model_api_key}"
        return headers

    def _build_payload(
        self,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int | None,
        extras: dict[str, Any] | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.settings.model_name,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if extras:
            payload.update(extras)
        return payload

    @staticmethod
    def _extract_content(body: dict) -> str:
        """Extract choices[0].message.content from an OpenAI-shaped body.

        A present-but-empty (or non-string) content value is returned as ""
        so the validation layer can classify it and the bounded retry can
        run; a missing choices/message structure is a response-shape
        contract violation and raises immediately (no retry can fix the
        server's response shape).
        """

        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelGatewayError(
                "Model endpoint returned an unexpected response shape",
                category="bad_response",
            ) from exc
        return content if isinstance(content, str) else ""

    async def _generate(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        max_tokens: int | None,
        extras: dict[str, Any] | None,
        budget: Optional[ReportRunBudget] = None,
        usage_context: Any = None,
        db: Any = None,
        call_role: str = "primary",
        attempt: int = 0,
        parent_call_id: str | None = None,
    ) -> tuple[str, OutputValidation, str | None]:
        """One generation attempt. Returns (raw content, validation, usage id)."""
        if budget is not None:
            budget.count_model()
        url = self._endpoint()
        headers = self._headers()
        payload = self._build_payload(messages, temperature, max_tokens, extras)
        from app.core.observability import METRICS

        started = time.perf_counter()
        started_wall = datetime.now(timezone.utc)
        try:
            _, body = await self._post_json(url, headers, payload)
        except (httpx.HTTPError, ValueError) as exc:
            category = classify_provider_error(exc)
            METRICS.inc("openjm_model_requests_total", labels={"outcome": "error"})
            METRICS.histogram(
                "openjm_model_request_duration_seconds",
                time.perf_counter() - started,
                {"op": "chat"},
            )
            # Operator-only, strictly sanitised: category + type + redacted
            # detail. The raw httpx message is never logged.
            logger.warning(
                "model provider request failed (category=%s, error_type=%s): %s",
                category,
                type(exc).__name__,
                sanitise_provider_text(
                    str(exc),
                    base_url=self.settings.model_base_url,
                    api_key=self.settings.model_api_key,
                ),
                extra={"category": "model_provider", "failure_category": category},
            )
            error = provider_failure(category)
            # A consumed attempt must outlive the caller's own rollback: hand the
            # evidence to the caller, which persists it after discarding the
            # abandoned business turn (usage_metering.record_pending_failure).
            error.pending_usage = {
                "context": usage_context,
                "call_role": call_role,
                "attempt": attempt,
                "failure_category": category,
                "prompt_text": _prompt_text(payload),
                "started_at": started_wall,
                "parent_call_id": parent_call_id,
            }
            raise error from exc
        if budget is not None:
            budget.check_wall(datetime.now(timezone.utc))
        content = self._extract_content(body)
        validation = validate_model_output(content)
        METRICS.inc(
            "openjm_model_requests_total",
            labels={"outcome": "ok" if validation.ok else "malformed"},
        )
        METRICS.histogram(
            "openjm_model_request_duration_seconds",
            time.perf_counter() - started,
            {"op": "chat"},
        )
        provider_usage = None
        if isinstance(body, dict):
            from app.services.usage_metering import ModelCallUsage

            provider_usage = ModelCallUsage.from_provider_usage(body.get("usage"))
        usage_id = await self._meter(
            usage_context=usage_context,
            db=db,
            call_role=call_role,
            attempt=attempt,
            status="succeeded",
            failure_category=None,
            provider_usage=provider_usage,
            prompt_text=_prompt_text(payload),
            completion_text=content,
            started_at=started_wall,
            parent_call_id=parent_call_id,
        )
        return content, validation, usage_id

    async def _meter(
        self,
        *,
        usage_context: Any,
        db: Any,
        call_role: str,
        attempt: int,
        status: str,
        failure_category: str | None,
        provider_usage: Any,
        prompt_text: str,
        completion_text: str,
        started_at: datetime,
        parent_call_id: str | None,
    ) -> str | None:
        """Finalize one usage event for this attempt. Never breaks the call path.

        Metering flushes into the caller's unit of work; the request-level
        commit persists it. A metering failure must never fail the model call.
        """
        if usage_context is None or db is None:
            return None
        try:
            from app.services.usage_metering import ModelCallUsage, record_model_usage

            resolved = provider_usage
            if resolved is None:
                resolved = ModelCallUsage.estimate(
                    prompt_text, completion_text if status == "succeeded" else ""
                )
            event = await record_model_usage(
                db,
                context=usage_context,
                usage=resolved,
                call_role=call_role,
                attempt=attempt,
                status=status,
                failure_category=failure_category,
                parent_call_id=parent_call_id,
                started_at=started_at,
            )
            return event.id if event is not None else None
        except Exception as exc:  # noqa: BLE001 - metering must not break the model path
            logger.warning("model usage metering failed: %r", exc)
            return None

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        budget: Optional[ReportRunBudget] = None,
        usage_context: Any = None,
        db: Any = None,
    ) -> str:
        """Generate one validated answer.

        Flow: first attempt with the caller's settings → validate →
        return if valid. Otherwise ONE bounded retry with deterministic
        settings (temperature 0.0, bounded max_tokens) and the configured
        retry extras → validate → return if valid, else a controlled
        ModelGatewayError. Malformed content is never returned.

        When *budget* is supplied (report-execution path only) every HTTP
        attempt is counted and max_tokens is capped to
        ``budget.max_output_tokens`` (default 2048).  When *budget* is
        ``None`` (ordinary Chat) behaviour is unchanged.

        When *usage_context* and *db* are supplied, each HTTP attempt is
        metered as one append-only usage event: the retry is a separate row
        linked to the primary attempt.
        """
        if budget is not None:
            max_tokens = budget.enforce_max_tokens(max_tokens)
        content, validation, primary_usage_id = await self._generate(
            messages, temperature=temperature, max_tokens=max_tokens, extras=None,
            budget=budget,
            usage_context=usage_context, db=db, call_role="primary", attempt=0,
        )
        if validation.ok:
            return content

        retry_max_tokens = max_tokens
        if retry_max_tokens is None:
            retry_max_tokens = self.settings.model_retry_max_tokens
        retry_content, retry_validation, _retry_usage_id = await self._generate(
            messages,
            temperature=self.settings.model_retry_temperature,
            max_tokens=retry_max_tokens,
            extras=self._retry_extras,
            budget=budget,
            usage_context=usage_context,
            db=db,
            call_role="retry",
            attempt=1,
            parent_call_id=primary_usage_id,
        )
        if retry_validation.ok:
            return retry_content

        raise ModelGatewayError(
            "Model output failed validation after one bounded retry "
            f"(first: {validation.failure_reason}; "
            f"retry: {retry_validation.failure_reason})",
            category="bad_response",
        )
