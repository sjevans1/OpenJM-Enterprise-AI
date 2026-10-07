"""Issue #38 — provider URL/credential isolation cannot regress.

The private model endpoint, host, path and bearer token must never reach a
client response, a public probe, the metrics surface or the operator log. These
negative tests inject a private sentinel host/path/token into transport failures
and assert none of them leak, while the failure still fails closed with a stable
category.
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest

from app.core.observability import METRICS
from app.services import model_gateway as mg

SENTINEL_HOST = "sentinel-private-provider.internal"
SENTINEL_PATH = "/v1/private/openjm"
SENTINEL_URL = f"https://{SENTINEL_HOST}{SENTINEL_PATH}"
SENTINEL_TOKEN = "sk-SENTINEL-PROVIDER-CREDENTIAL-9f3a"

SENTINELS = (SENTINEL_HOST, SENTINEL_PATH, SENTINEL_URL, SENTINEL_TOKEN)


def _assert_no_sentinel(text: str) -> None:
    for marker in SENTINELS:
        assert marker not in text, f"sentinel leaked: {marker!r}"


def _sentinel_gateway() -> mg.OpenAICompatibleModelGateway:
    gateway = mg.OpenAICompatibleModelGateway()
    gateway.settings = gateway.settings.model_copy(
        update={"model_base_url": SENTINEL_URL, "model_api_key": SENTINEL_TOKEN}
    )
    return gateway


def _raising_post(exc: BaseException):
    async def _post_json(url: str, headers: dict, payload: dict):
        raise exc

    return _post_json


def _status_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", SENTINEL_URL)
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError("provider error", request=request, response=response)


def test_classify_provider_error_categories() -> None:
    assert mg.classify_provider_error(httpx.ConnectTimeout("t")) == "timeout"
    assert mg.classify_provider_error(httpx.ReadTimeout("t")) == "timeout"
    assert mg.classify_provider_error(httpx.ConnectError("c")) == "unreachable"
    assert mg.classify_provider_error(_status_error(401)) == "auth_rejected"
    assert mg.classify_provider_error(_status_error(403)) == "auth_rejected"
    assert mg.classify_provider_error(_status_error(500)) == "bad_response"
    assert mg.classify_provider_error(ValueError("not json")) == "bad_response"


def test_sanitise_provider_text_redacts_url_and_credential() -> None:
    raw = (
        f"Server error '500' for url '{SENTINEL_URL}' "
        f"with Authorization: Bearer {SENTINEL_TOKEN}"
    )
    clean = mg.sanitise_provider_text(raw, base_url=SENTINEL_URL, api_key=SENTINEL_TOKEN)
    _assert_no_sentinel(clean)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (httpx.ReadTimeout("slow"), "timeout"),
        (httpx.ConnectError("refused"), "unreachable"),
        (_status_error(401), "auth_rejected"),
        (_status_error(503), "bad_response"),
    ],
)
async def test_gateway_maps_transport_failure_to_safe_category(
    monkeypatch, exc, expected
) -> None:
    gateway = _sentinel_gateway()
    monkeypatch.setattr(gateway, "_post_json", _raising_post(exc))
    with pytest.raises(mg.ModelGatewayError) as excinfo:
        await gateway.chat([{"role": "user", "content": "q"}])
    assert excinfo.value.category == expected
    _assert_no_sentinel(str(excinfo.value))


@pytest.mark.asyncio
async def test_gateway_error_and_log_never_leak_provider(monkeypatch, caplog) -> None:
    gateway = _sentinel_gateway()
    monkeypatch.setattr(
        gateway,
        "_post_json",
        _raising_post(
            httpx.ConnectError(
                f"failed to connect to {SENTINEL_URL} (Bearer {SENTINEL_TOKEN})"
            )
        ),
    )
    with caplog.at_level(logging.WARNING, logger="openjm.model"):
        with pytest.raises(mg.ModelGatewayError) as excinfo:
            await gateway.chat([{"role": "user", "content": "q"}])
    assert excinfo.value.category == "unreachable"
    _assert_no_sentinel(str(excinfo.value))

    # The operator log carries the category, never the provider address/secret.
    rendered = "\n".join(record.getMessage() for record in caplog.records)
    assert "unreachable" in rendered
    _assert_no_sentinel(rendered)

    # The metrics surface is a closed vocabulary: no provider detail.
    _assert_no_sentinel(METRICS.render())


@pytest.mark.asyncio
async def test_chat_api_502_never_leaks_provider(client, monkeypatch, caplog) -> None:
    from app.api import chat as chat_api

    gateway = chat_api.model_gateway
    monkeypatch.setattr(
        gateway,
        "settings",
        gateway.settings.model_copy(
            update={"model_base_url": SENTINEL_URL, "model_api_key": SENTINEL_TOKEN}
        ),
    )
    monkeypatch.setattr(
        gateway,
        "_post_json",
        _raising_post(
            httpx.ConnectError(
                f"cannot reach {SENTINEL_URL} with token {SENTINEL_TOKEN}"
            )
        ),
    )
    with caplog.at_level(logging.WARNING, logger="openjm.model"):
        response = await client.post(
            "/api/chat", json={"message": "hello", "mode": "chat"}
        )
    assert response.status_code == 502
    _assert_no_sentinel(response.text)
    assert "unreachable" in response.text
    _assert_no_sentinel("\n".join(record.getMessage() for record in caplog.records))


@pytest.mark.asyncio
async def test_ready_probe_never_leaks_provider(monkeypatch) -> None:
    gateway = _sentinel_gateway()

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, headers=None):
            raise httpx.ConnectError(f"{SENTINEL_URL} {SENTINEL_TOKEN}")

    monkeypatch.setattr(mg.httpx, "AsyncClient", lambda *a, **k: _FakeClient())
    component = await gateway.probe()
    assert component["status"] == "error"
    _assert_no_sentinel(json.dumps(component))
