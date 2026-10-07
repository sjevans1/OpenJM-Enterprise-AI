"""M1 correction acceptance: per-turn identity, transaction safety, failure
evidence, and planner invocation coverage.

Addresses the PR #51 review: a conversation id is not a request id; metering
must never roll back the caller's unit of work; a failed model attempt must keep
its usage evidence without persisting orphan Chat state; and the known
planner/report model call sites must be metered.
"""

import json

import httpx
import pytest
from sqlalchemy import select

from app.models import Conversation, DataSource, Message, ModelUsageEvent
from app.services.usage_metering import UsageContext, record_model_usage


def _context(request_id: str = "req-1", **kwargs) -> UsageContext:
    return UsageContext(
        tenant_id="tnt-usage-a",
        request_id=request_id,
        provider_route="local",
        model_name="test-model",
        principal_id="p1",
        **kwargs,
    )


def _completion(content: str, usage: dict | None = None) -> dict:
    body = {"choices": [{"message": {"content": content}}]}
    if usage is not None:
        body["usage"] = usage
    return body


# ---------------------------------------------------------------------------
# 1. Unique per-turn request identity
# ---------------------------------------------------------------------------


async def test_two_turns_in_one_conversation_record_two_primary_events(
    client, file_db, monkeypatch
):
    from app.api import chat as chat_api

    async def fake_post(url, headers, payload):
        return 200, _completion("A short grounded answer.", {"prompt_tokens": 7, "completion_tokens": 5})

    monkeypatch.setattr(chat_api.model_gateway, "_post_json", fake_post)

    first = await client.post("/api/chat", json={"message": "First question", "mode": "chat"})
    assert first.status_code == 200, first.text
    conversation_id = first.json()["conversation_id"]

    second = await client.post(
        "/api/chat",
        json={"message": "Second question", "mode": "chat", "conversation_id": conversation_id},
    )
    assert second.status_code == 200, second.text

    async with file_db() as db:
        events = (await db.execute(select(ModelUsageEvent))).scalars().all()
    assert len(events) == 2, "two turns must produce two billable usage events"
    request_ids = {event.request_id for event in events}
    assert len(request_ids) == 2, "each turn needs its own request id"
    assert conversation_id not in request_ids, "the conversation id must not be the request id"
    assert {event.conversation_id for event in events} == {conversation_id}
    assert all(event.call_role == "primary" for event in events)


async def test_retry_within_one_turn_is_linked_to_the_same_request(file_db):
    async with file_db() as db:
        primary = await record_model_usage(
            db, context=_context("turn-1"), call_role="primary", attempt=0,
            prompt_text="q", completion_text="a",
        )
        retry = await record_model_usage(
            db, context=_context("turn-1"), call_role="retry", attempt=1,
            parent_call_id=primary.id if primary else None,
            prompt_text="q", completion_text="b",
        )
        await db.commit()
    assert primary is not None and retry is not None
    assert primary.request_id == retry.request_id == "turn-1"
    assert retry.parent_call_id == primary.id
    async with file_db() as db:
        assert len((await db.execute(select(ModelUsageEvent))).scalars().all()) == 2


# ---------------------------------------------------------------------------
# 2. Transaction-safe exactly-once (no caller rollback)
# ---------------------------------------------------------------------------


async def test_duplicate_finalization_does_not_rollback_unrelated_caller_state(
    file_db, monkeypatch
):
    import app.services.usage_metering as um

    context = _context("dup-req")
    async with file_db() as other:
        await um.record_model_usage(other, context=context, prompt_text="aaaa", completion_text="bbbb")
        await other.commit()

    # Simulate the race: the pre-check misses the row that another request just
    # committed, so the insert hits the unique constraint.
    async def always_miss(db, key):
        return None

    monkeypatch.setattr(um, "_find_existing", always_miss)

    async with file_db() as db:
        db.add(Conversation(user_id="u", title="pending-business-row"))
        await db.flush()
        result = await um.record_model_usage(
            db, context=context, prompt_text="aaaa", completion_text="bbbb"
        )
        # The caller's transaction is still usable and commits normally.
        await db.commit()
    assert result is None, "the losing side of the race must not fabricate a row"

    async with file_db() as db:
        survivors = (
            await db.execute(select(Conversation).where(Conversation.title == "pending-business-row"))
        ).scalars().all()
        events = (await db.execute(select(ModelUsageEvent))).scalars().all()
    assert len(survivors) == 1, "a metering conflict must not roll back unrelated caller state"
    assert len(events) == 1, "only the winner's usage row exists"


# ---------------------------------------------------------------------------
# 3. Failed attempts keep usage evidence without orphan Chat state
# ---------------------------------------------------------------------------


async def test_failed_model_request_retains_usage_without_orphan_chat_state(
    client, file_db, monkeypatch
):
    from app.api import chat as chat_api

    async def failing_post(url, headers, payload):
        raise httpx.ConnectError("provider unreachable")

    monkeypatch.setattr(chat_api.model_gateway, "_post_json", failing_post)

    response = await client.post("/api/chat", json={"message": "Will fail", "mode": "chat"})
    assert response.status_code == 502, response.text

    async with file_db() as db:
        events = (await db.execute(select(ModelUsageEvent))).scalars().all()
        conversations = (await db.execute(select(Conversation))).scalars().all()
        messages = (await db.execute(select(Message))).scalars().all()

    assert len(events) == 1, "a consumed model attempt must leave billing evidence"
    assert events[0].status == "failed"
    assert events[0].failure_category == "unreachable"
    assert events[0].usage_source == "estimated"
    assert conversations == [], "no orphan conversation may be persisted"
    assert messages == [], "no orphan Chat turn may be persisted"


# ---------------------------------------------------------------------------
# 4. Planner invocation coverage
# ---------------------------------------------------------------------------


async def test_structured_planner_invocation_is_metered(file_db, monkeypatch):
    from app.core.config import get_settings
    from app.services import structured_planner as planner_module

    settings = get_settings()
    async with file_db() as db:
        db.add(
            DataSource(
                id="planner-src",
                tenant_id="tnt-local",
                user_id=settings.dev_user_id,
                name="Finance",
                engine="sqlite",
                connection_secret="x",
                status="connected",
                enabled=True,
                schema_json=json.dumps(
                    [
                        {
                            "schema_name": "main",
                            "name": "finance",
                            "qualified_name": "finance",
                            "columns": [{"name": "revenue", "type": "NUMERIC", "nullable": False}],
                            "primary_key": [],
                            "foreign_keys": [],
                        }
                    ]
                ),
            )
        )
        await db.commit()

    async def fake_post(url, headers, payload):
        return 200, _completion(
            '{"use_structured": false, "source_id": "", "sql": "", "rationale": "no match"}',
            {"prompt_tokens": 11, "completion_tokens": 4},
        )

    monkeypatch.setattr(planner_module.structured_planner.model_gateway, "_post_json", fake_post)

    async with file_db() as db:
        await planner_module.structured_planner.plan(
            "What is the total revenue for the finance table?",
            db,
            settings.dev_user_id,
            usage_context=_context("plan-req", execution_class="structured"),
        )
        await db.commit()

    async with file_db() as db:
        events = (await db.execute(select(ModelUsageEvent))).scalars().all()
    assert len(events) == 1, "the structured planner call must appear in the ledger"
    assert events[0].request_id == "plan-req"
    assert events[0].execution_class == "structured"
    assert events[0].usage_source == "provider_reported"
    assert events[0].input_tokens == 11
