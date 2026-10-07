"""#46 M1 immutable LLM usage metering acceptance.

Covers attribution, provider-reported vs estimated usage, exactly-once
finalization, retry/fallback relationships, tenant isolation, the append-only
database guard, and instrumentation through the central model gateway.
"""

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError

from app.core.usage import estimate_tokens
from app.models import ModelUsageEvent, Tenant
from app.services.usage_metering import (
    ModelCallUsage,
    UsageContext,
    record_model_usage,
    summarize_usage,
)

TENANT_A = "tnt-usage-a"
TENANT_B = "tnt-usage-b"


def _context(request_id: str = "req-1", tenant_id: str = TENANT_A, **kwargs) -> UsageContext:
    return UsageContext(
        tenant_id=tenant_id,
        request_id=request_id,
        provider_route="local",
        model_name="test-model",
        principal_id="p1",
        **kwargs,
    )


@pytest.fixture
async def world(file_db):
    async with file_db() as db:
        db.add_all(
            [
                Tenant(id=TENANT_A, slug="usage-a", name="A", status="active"),
                Tenant(id=TENANT_B, slug="usage-b", name="B", status="active"),
            ]
        )
        await db.commit()
    return {}


# ---------------------------------------------------------------------------
# Provider-reported vs estimated
# ---------------------------------------------------------------------------


def test_estimate_is_deterministic_and_conservative():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("a" * 9) == 3


def test_provider_usage_is_captured_when_present():
    usage = ModelCallUsage.from_provider_usage(
        {
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "prompt_tokens_details": {"cached_tokens": 20},
            "completion_tokens_details": {"reasoning_tokens": 5},
        }
    )
    assert usage is not None
    assert usage.provider_reported is True
    assert usage.usage_source == "provider_reported"
    assert usage.total_tokens == 150
    assert usage.cached_input_tokens == 20
    assert usage.reasoning_tokens == 5


def test_malformed_provider_usage_returns_none():
    assert ModelCallUsage.from_provider_usage(None) is None
    assert ModelCallUsage.from_provider_usage({}) is None
    assert ModelCallUsage.from_provider_usage({"prompt_tokens": "lots"}) is None
    assert ModelCallUsage.from_provider_usage({"prompt_tokens": -1, "completion_tokens": 1}) is None


async def test_local_model_is_metered_as_estimated(world, file_db):
    async with file_db() as db:
        await record_model_usage(
            db,
            context=_context(),
            usage=None,
            prompt_text="a" * 40,
            completion_text="b" * 12,
        )
        await db.commit()
    async with file_db() as db:
        events = (await db.execute(select(ModelUsageEvent))).scalars().all()
    assert len(events) == 1
    assert events[0].usage_source == "estimated"
    assert events[0].input_tokens == 10
    assert events[0].output_tokens == 3
    assert events[0].total_tokens == 13
    assert events[0].tenant_id == TENANT_A
    assert events[0].principal_id == "p1"


# ---------------------------------------------------------------------------
# Exactly-once, retries and fallbacks
# ---------------------------------------------------------------------------


async def test_finalization_is_exactly_once(world, file_db):
    async with file_db() as db:
        first = await record_model_usage(db, context=_context(), prompt_text="x", completion_text="y")
        await db.commit()
        second = await record_model_usage(db, context=_context(), prompt_text="x", completion_text="y")
        await db.commit()
    assert first is not None and second is not None
    assert first.id == second.id, "a replayed request must not create a second row"
    async with file_db() as db:
        count = len((await db.execute(select(ModelUsageEvent))).scalars().all())
    assert count == 1


async def test_retry_and_fallback_are_separate_linked_rows(world, file_db):
    async with file_db() as db:
        primary = await record_model_usage(
            db, context=_context(), call_role="primary", attempt=0, prompt_text="q", completion_text="a"
        )
        retry = await record_model_usage(
            db,
            context=_context(),
            call_role="retry",
            attempt=1,
            parent_call_id=primary.id if primary else None,
            prompt_text="q",
            completion_text="b",
        )
        fallback = await record_model_usage(
            db,
            context=_context(),
            call_role="fallback",
            attempt=2,
            parent_call_id=primary.id if primary else None,
            prompt_text="q",
            completion_text="c",
        )
        await db.commit()
    async with file_db() as db:
        events = (await db.execute(select(ModelUsageEvent))).scalars().all()
    assert len(events) == 3, "each actual call is one row"
    roles = {event.call_role for event in events}
    assert roles == {"primary", "retry", "fallback"}
    assert all(
        event.parent_call_id == primary.id for event in events if event.call_role != "primary"
    )


async def test_duplicate_key_is_rejected_at_the_database_level(world, file_db):
    async with file_db() as db:
        db.add(
            ModelUsageEvent(
                tenant_id=TENANT_A,
                request_id="r",
                provider_route="local",
                model_name="m",
                usage_source="estimated",
                input_tokens=1,
                output_tokens=1,
                total_tokens=2,
                call_role="primary",
                idempotency_key="dup-key",
                status="succeeded",
                started_at=datetime.now(timezone.utc),
                finished_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()
    async with file_db() as db:
        db.add(
            ModelUsageEvent(
                tenant_id=TENANT_A,
                request_id="r",
                provider_route="local",
                model_name="m",
                usage_source="estimated",
                input_tokens=1,
                output_tokens=1,
                total_tokens=2,
                call_role="primary",
                idempotency_key="dup-key",
                status="succeeded",
                started_at=datetime.now(timezone.utc),
                finished_at=datetime.now(timezone.utc),
            )
        )
        with pytest.raises(IntegrityError):
            await db.flush()
        await db.rollback()


# ---------------------------------------------------------------------------
# Tenant isolation and aggregation
# ---------------------------------------------------------------------------


async def test_summary_is_tenant_scoped(world, file_db):
    async with file_db() as db:
        await record_model_usage(db, context=_context("a-1"), prompt_text="aaaa", completion_text="bbbb")
        await record_model_usage(
            db, context=_context("b-1", tenant_id=TENANT_B), prompt_text="aaaa", completion_text="bbbb"
        )
        await db.commit()

    async with file_db() as db:
        a = await summarize_usage(db, tenant_id=TENANT_A)
        b = await summarize_usage(db, tenant_id=TENANT_B)
    assert a["events"] == 1 and b["events"] == 1
    assert a["total_tokens"] == 2


async def test_summary_can_narrow_to_one_request(world, file_db):
    async with file_db() as db:
        await record_model_usage(db, context=_context("req-x"), prompt_text="aaaa", completion_text="bbbb")
        await record_model_usage(db, context=_context("req-y"), prompt_text="aaaa", completion_text="bbbb")
        await db.commit()
    async with file_db() as db:
        narrowed = await summarize_usage(db, tenant_id=TENANT_A, request_id="req-x")
    assert narrowed["events"] == 1


# ---------------------------------------------------------------------------
# Append-only database guard
# ---------------------------------------------------------------------------


def test_migration_0011_creates_table_and_blocks_update(tmp_path):
    from sqlalchemy import create_engine, inspect

    from app.migrations_runner import adopt_and_upgrade, current_revision, sync_url_for

    url = f"sqlite+aiosqlite:///{tmp_path / 'usage.db'}"
    adopt_and_upgrade(url)
    head = current_revision(url)
    assert head is not None
    sync = sync_url_for(url)
    engine = create_engine(sync, future=True)
    try:
        assert "model_usage_events" in set(inspect(engine).get_table_names())
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO model_usage_events (id, tenant_id, request_id, provider_route, "
                    "model_name, usage_source, input_tokens, output_tokens, total_tokens, call_role, "
                    "idempotency_key, status, started_at, finished_at, created_at) VALUES "
                    "('e1','t','r','local','m','estimated',1,1,2,'primary','k1','succeeded',"
                    "'2026-01-01 00:00:00.000000','2026-01-01 00:00:00.000000','2026-01-01 00:00:00.000000')"
                )
            )
        with pytest.raises(Exception):
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE model_usage_events SET total_tokens = 999 WHERE id = 'e1'")
                )
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Instrumentation through the central gateway
# ---------------------------------------------------------------------------


async def test_gateway_records_provider_reported_usage(world, file_db, monkeypatch):
    from app.services.model_gateway import OpenAICompatibleModelGateway

    gateway = OpenAICompatibleModelGateway()

    async def fake_post(url, headers, payload):
        return 200, {
            "choices": [{"message": {"content": "The FY2025 threshold is USD 300."}}],
            "usage": {"prompt_tokens": 40, "completion_tokens": 9},
        }

    monkeypatch.setattr(gateway, "_post_json", fake_post)

    async with file_db() as db:
        answer = await gateway.chat(
            [{"role": "user", "content": "What is the threshold?"}],
            db=db,
            usage_context=_context("gw-1"),
        )
        await db.commit()
    assert "300" in answer

    async with file_db() as db:
        events = (await db.execute(select(ModelUsageEvent))).scalars().all()
    assert len(events) == 1
    event = events[0]
    assert event.usage_source == "provider_reported"
    assert event.input_tokens == 40
    assert event.output_tokens == 9
    assert event.request_id == "gw-1"
    assert event.model_name == "test-model"
    assert event.provider_route == "local"
    assert event.status == "succeeded"


async def test_gateway_without_context_records_nothing(world, file_db, monkeypatch):
    from app.services.model_gateway import OpenAICompatibleModelGateway

    gateway = OpenAICompatibleModelGateway()

    async def fake_post(url, headers, payload):
        return 200, {"choices": [{"message": {"content": "Fine."}}]}

    monkeypatch.setattr(gateway, "_post_json", fake_post)
    async with file_db() as db:
        await gateway.chat([{"role": "user", "content": "hi"}])
        await db.commit()
    async with file_db() as db:
        events = (await db.execute(select(ModelUsageEvent))).scalars().all()
    assert events == []
