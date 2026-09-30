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
import re
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import get_settings


class ModelGatewayError(RuntimeError):
    pass


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
                "Model endpoint returned an unexpected response shape"
            ) from exc
        return content if isinstance(content, str) else ""

    async def _generate(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        max_tokens: int | None,
        extras: dict[str, Any] | None,
    ) -> tuple[str, OutputValidation]:
        """One generation attempt. Returns (raw content, validation)."""

        url = self._endpoint()
        headers = self._headers()
        payload = self._build_payload(messages, temperature, max_tokens, extras)
        try:
            _, body = await self._post_json(url, headers, payload)
        except (httpx.HTTPError, ValueError) as exc:
            raise ModelGatewayError(f"Model request failed: {exc}") from exc
        content = self._extract_content(body)
        return content, validate_model_output(content)

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> str:
        """Generate one validated answer.

        Flow: first attempt with the caller's settings → validate →
        return if valid. Otherwise ONE bounded retry with deterministic
        settings (temperature 0.0, bounded max_tokens) and the configured
        retry extras → validate → return if valid, else a controlled
        ModelGatewayError. Malformed content is never returned.
        """

        content, validation = await self._generate(
            messages, temperature=temperature, max_tokens=max_tokens, extras=None
        )
        if validation.ok:
            return content

        retry_max_tokens = max_tokens
        if retry_max_tokens is None:
            retry_max_tokens = self.settings.model_retry_max_tokens
        retry_content, retry_validation = await self._generate(
            messages,
            temperature=self.settings.model_retry_temperature,
            max_tokens=retry_max_tokens,
            extras=self._retry_extras,
        )
        if retry_validation.ok:
            return retry_content

        raise ModelGatewayError(
            "Model output failed validation after one bounded retry "
            f"(first: {validation.failure_reason}; "
            f"retry: {retry_validation.failure_reason})"
        )
