"""Deterministic unit tests for the bounded model-output validation gateway.

The OpenAI-compatible endpoint is mocked (no Gemma, no network). Covers:

- valid normal answer
- empty content
- repeated <unusedNN> garbage
- raw control/tool tokens
- mixed valid prose with harmless angle-bracket text (must NOT be rejected)
- malformed response shape
- retry succeeds (first malformed, retry valid)
- retry also malformed → controlled failure
- planner-style usage (temperature 0.0 + max_tokens) is not broken
- validation rules themselves (density / repetition / structure)
"""

import asyncio
import json
from typing import Any

import pytest

from app.services.model_gateway import (
    ModelGatewayError,
    OpenAICompatibleModelGateway,
    validate_model_output,
)


def _body(content: str, finish: str = "stop") -> dict:
    return {
        "choices": [
            {
                "index": 0,
                "finish_reason": finish,
                "message": {"role": "assistant", "content": content},
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def _make_gateway(monkeypatch, responses: list[dict | Exception]):
    """Gateway whose transport replays the given responses in order."""

    gateway = OpenAICompatibleModelGateway()
    calls: list[dict] = []
    queue = list(responses)

    async def fake_post_json(url: str, headers: dict, payload: dict):
        calls.append(
            {
                "url": url,
                "temperature": payload.get("temperature"),
                "max_tokens": payload.get("max_tokens"),
                "extras": {
                    k: v
                    for k, v in payload.items()
                    if k not in {"model", "messages", "temperature", "stream", "max_tokens"}
                },
                "messages": payload.get("messages"),
            }
        )
        if not queue:
            raise AssertionError("unexpected extra model call")
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return 200, item

    monkeypatch.setattr(gateway, "_post_json", fake_post_json)
    return gateway, calls


# ── pure validation rules ───────────────────────────────────────────────────


def test_valid_normal_answer_passes():
    result = validate_model_output(
        "The PRIMARY launch sequence code for Project Phoenix is 7-3-9-2-5 [1]."
    )
    assert result.ok


def test_empty_content_is_invalid():
    assert not validate_model_output("").ok
    assert not validate_model_output("   \n\t  ").ok


def test_repeated_unused_garbage_is_invalid():
    garbage = "<unused32>" * 40
    result = validate_model_output(garbage)
    assert not result.ok
    assert "density" in result.failure_reason


def test_mixed_control_token_garbage_is_invalid():
    garbage = (
        "<unused50><unused16><unused26><unused2><unused7><unused16>"
        "<unused38><unused20><unused50><unused39><|tool_call><unused9>"
        "<unused47><unused40><|\"|><unused2><unused23><|\"|>"
    )
    assert not validate_model_output(garbage).ok


def test_raw_tool_control_tokens_invalid():
    assert not validate_model_output("<|tool_call|>call:search{query:<|\"|>x<|\"|>}<tool_call|>").ok
    assert not validate_model_output("<turn|><|turn>model<channel|>").ok


def test_single_quoted_token_in_prose_is_valid():
    """A legitimate answer that mentions a token-like string once must pass."""
    text = (
        "The tokenizer reserves entries such as <unused3> for future use; "
        "they never appear in normal answers."
    )
    assert validate_model_output(text).ok


def test_html_angle_brackets_are_valid():
    assert validate_model_output("Wrap the output in <div class='x'> tags.").ok
    assert validate_model_output("For values x < 10 the loop terminates.").ok


def test_structure_rule_leading_control_run():
    content = "<unused5><unused6><unused7>" + " Then a normal answer follows here."
    assert not validate_model_output(content).ok


def test_moderate_token_presence_below_threshold_is_valid():
    # a few token mentions inside a long legitimate answer stay valid
    text = ("Analysis of the report shows revenue growth. " * 20) + " One note: <unused9> appeared once."
    assert validate_model_output(text).ok


# ── gateway behavior (mocked endpoint) ─────────────────────────────────────


@pytest.mark.asyncio
async def test_gateway_returns_valid_answer_single_call(monkeypatch):
    gateway, calls = _make_gateway(monkeypatch, [_body("The answer is 7-3-9-2-5 [1].")])
    result = await gateway.chat([{"role": "user", "content": "q"}])
    assert result == "The answer is 7-3-9-2-5 [1]."
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_gateway_empty_content_fails(monkeypatch):
    gateway, _calls = _make_gateway(monkeypatch, [_body(""), _body("valid retry answer")])
    # empty first attempt -> retry with deterministic settings
    result = await gateway.chat([{"role": "user", "content": "q"}])
    assert result == "valid retry answer"


@pytest.mark.asyncio
async def test_gateway_repeated_garbage_then_valid_retry(monkeypatch):
    garbage = "<unused32>" * 40
    gateway, calls = _make_gateway(monkeypatch, [_body(garbage), _body("A valid answer after retry.")])
    result = await gateway.chat([{"role": "user", "content": "q"}])
    assert result == "A valid answer after retry."
    assert len(calls) == 2
    # retry used deterministic settings and the configured extras
    assert calls[1]["temperature"] == 0.0
    assert calls[1]["max_tokens"] == 512
    assert calls[1]["extras"].get("chat_template_kwargs") == {"enable_thinking": True}


@pytest.mark.asyncio
async def test_gateway_retry_also_malformed_is_controlled_failure(monkeypatch):
    gateway, calls = _make_gateway(
        monkeypatch,
        [_body("<unused1>" * 40), _body("<|tool_call|>" * 30)],
    )
    with pytest.raises(ModelGatewayError) as excinfo:
        await gateway.chat([{"role": "user", "content": "q"}])
    # bounded: exactly two attempts, no unlimited loop
    assert len(calls) == 2
    # the malformed content itself is not embedded in the error
    assert "<unused1>" not in str(excinfo.value)
    assert "validation" in str(excinfo.value)


@pytest.mark.asyncio
async def test_gateway_structurally_invalid_body(monkeypatch):
    gateway, _calls = _make_gateway(monkeypatch, [{"unexpected": "shape"}, {"unexpected": "shape"}])
    with pytest.raises(ModelGatewayError):
        await gateway.chat([{"role": "user", "content": "q"}])


@pytest.mark.asyncio
async def test_gateway_no_choices(monkeypatch):
    gateway, _calls = _make_gateway(monkeypatch, [{"choices": []}, {"choices": []}])
    with pytest.raises(ModelGatewayError):
        await gateway.chat([{"role": "user", "content": "q"}])


@pytest.mark.asyncio
async def test_gateway_planner_style_usage_not_broken(monkeypatch):
    """Planner calls (temperature 0.0, max_tokens 700, JSON expected) must
    pass through unchanged and not be retried when valid."""

    planner_json = json.dumps(
        {
            "use_structured": True,
            "source_id": "src-1",
            "sql": "SELECT 1",
            "rationale": "ok",
        }
    )
    gateway, calls = _make_gateway(monkeypatch, [_body(planner_json)])
    result = await gateway.chat(
        [{"role": "system", "content": "planner prompt"}],
        temperature=0.0,
        max_tokens=700,
    )
    assert json.loads(result)["sql"] == "SELECT 1"
    assert len(calls) == 1
    assert calls[0]["temperature"] == 0.0
    assert calls[0]["max_tokens"] == 700


@pytest.mark.asyncio
async def test_gateway_planner_garbage_then_retry(monkeypatch):
    """A malformed planner response gets the same bounded retry; the retry
    keeps the caller's max_tokens bound."""

    planner_json = '{"use_structured": true, "source_id": "s", "sql": "SELECT 1", "rationale": "r"}'
    gateway, calls = _make_gateway(
        monkeypatch,
        [_body("<unused20><unused28>" * 12), _body(planner_json)],
    )
    result = await gateway.chat(
        [{"role": "system", "content": "planner prompt"}],
        temperature=0.0,
        max_tokens=700,
    )
    assert json.loads(result)["sql"] == "SELECT 1"
    assert len(calls) == 2
    assert calls[1]["max_tokens"] == 700  # caller bound preserved on retry


@pytest.mark.asyncio
async def test_gateway_transport_error_is_controlled(monkeypatch):
    import httpx

    gateway, _calls = _make_gateway(
        monkeypatch, [httpx.ConnectError("connection refused"), httpx.ConnectError("again")]
    )
    with pytest.raises(ModelGatewayError):
        await gateway.chat([{"role": "user", "content": "q"}])


@pytest.mark.asyncio
async def test_gateway_http_error_is_controlled(monkeypatch):
    import httpx

    response = httpx.Response(500, request=httpx.Request("POST", "http://x/v1/chat/completions"))
    gateway, _calls = _make_gateway(
        monkeypatch, [httpx.HTTPStatusError("server error", request=response.request, response=response)]
    )
    with pytest.raises(ModelGatewayError):
        await gateway.chat([{"role": "user", "content": "q"}])


def test_retry_extras_parsing(monkeypatch):
    gateway = OpenAICompatibleModelGateway()
    # default deployment extras enable the Gemma thinking channel
    assert gateway._retry_extras == {"chat_template_kwargs": {"enable_thinking": True}}

    class FakeSettings:
        model_retry_request_extras = '{"custom_field": 1}'

    gateway2 = OpenAICompatibleModelGateway.__new__(OpenAICompatibleModelGateway)
    gateway2.settings = FakeSettings()
    gateway2._retry_extras = gateway2._parse_retry_extras('{"custom_field": 1}')
    assert gateway2._retry_extras == {"custom_field": 1}

    gateway3 = OpenAICompatibleModelGateway.__new__(OpenAICompatibleModelGateway)
    gateway3._retry_extras = gateway3._parse_retry_extras("not json")
    assert gateway3._retry_extras == {}
