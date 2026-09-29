from typing import Any

import httpx

from app.core.config import get_settings


class ModelGatewayError(RuntimeError):
    pass


class OpenAICompatibleModelGateway:
    """One model contract for Hermes, Ollama, vLLM and similar endpoints."""

    def __init__(self) -> None:
        self.settings = get_settings()

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> str:
        base_url = self.settings.model_base_url.rstrip("/")
        url = f"{base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.settings.model_api_key:
            headers["Authorization"] = f"Bearer {self.settings.model_api_key}"

        payload: dict[str, Any] = {
            "model": self.settings.model_name,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        try:
            async with httpx.AsyncClient(
                timeout=self.settings.model_timeout_seconds
            ) as client:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ModelGatewayError(f"Model request failed: {exc}") from exc

        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelGatewayError(
                "Model endpoint returned an unexpected response shape"
            ) from exc

        if not isinstance(content, str) or not content.strip():
            raise ModelGatewayError("Model returned an empty answer")
        return content.strip()
