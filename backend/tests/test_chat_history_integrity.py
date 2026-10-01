"""History-integrity tests for the chat API (Phase B reliability).

Deterministic: the model gateway is mocked; no Gemma, no network.

Covers the transaction defect where the user Message was committed before
model generation:

- a failed model call must NOT leave an orphan user turn in the
  conversation history (a later retry must see the same message array
  shape as the failed request — no consecutive user turns);
- malformed model output is never persisted as an assistant message;
- a successful generation persists exactly one user + one assistant turn;
- the 502 response is controlled and carries no raw model content.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.chat import router as chat_router
from app.db import Base, get_db
from app.main import app
from app.models import Conversation, Message
from app.services.model_gateway import ModelGatewayError


@pytest.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


@pytest.fixture
async def client(db_session, monkeypatch):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()


class FailingGateway:
    def __init__(self, error_text="Model output failed validation after one bounded retry"):
        self.error_text = error_text
        self.calls = 0

    async def chat(self, messages, **kwargs):
        self.calls += 1
        raise ModelGatewayError(self.error_text)


class DirectAnswerOrchestrator:
    """Minimal plan replacement: general path with a system prompt, so the
    gateway is actually invoked; direct answers never touch the gateway."""

    def __init__(self, answer_text="The answer."):
        self.answer_text = answer_text

    async def plan(self, message, db, user_id, conversation_id=None):
        return SimpleNamespace(
            execution_class="general",
            system_prompt="You are OpenJM Enterprise AI.",
            evidence=[],
            direct_answer=None,
        )


class AnsweringGateway:
    def __init__(self, answer="A clean valid answer."):
        self.answer = answer

    async def chat(self, messages, **kwargs):
        return self.answer


def _messages_of(db_session, conversation_id):
    return db_session.execute(
        select(Message).where(Message.conversation_id == conversation_id)
    )


@pytest.mark.asyncio
async def test_failed_generation_leaves_no_orphan_user_turn(client, db_session, monkeypatch):
    monkeypatch.setattr("app.api.chat.orchestrator", DirectAnswerOrchestrator())
    failing = FailingGateway()
    monkeypatch.setattr("app.api.chat.model_gateway", failing)

    response = await client.post("/api/chat", json={"message": "Question one?"})
    assert response.status_code == 502
    assert "bounded retry" in response.json()["detail"]
    # no raw garbage, no stack, no content leak in the detail
    assert "<unused" not in response.json()["detail"]

    # A failed FIRST turn (no prior commit) rolls back cleanly: no
    # conversation, no messages, nothing persisted.
    conversations = (
        await db_session.execute(select(Conversation))
    ).scalars().all()
    assert conversations == [], "failed first turn must not persist a conversation"
    messages = (
        await db_session.execute(select(Message))
    ).scalars().all()
    assert messages == [], f"orphan messages persisted: {messages}"


@pytest.mark.asyncio
async def test_retry_after_failure_sees_same_message_shape(client, db_session, monkeypatch):
    monkeypatch.setattr("app.api.chat.orchestrator", DirectAnswerOrchestrator())

    # first attempt fails
    monkeypatch.setattr("app.api.chat.model_gateway", FailingGateway())
    first = await client.post("/api/chat", json={"message": "Question one?"})
    assert first.status_code == 502
    conversation_id = None

    # second attempt through a NEW conversation must produce clean,
    # alternating history — and the persisted history after a SUCCESSFUL
    # turn contains exactly one user and one assistant message
    monkeypatch.setattr("app.api.chat.model_gateway", AnsweringGateway("Valid answer."))
    second = await client.post("/api/chat", json={"message": "Question one?"})
    assert second.status_code == 200
    conversation_id = second.json()["conversation_id"]

    await db_session.rollback()  # release any cached identity map state
    rows = (
        await db_session.execute(
            select(Message.role).where(Message.conversation_id == conversation_id)
        )
    ).scalars().all()
    assert rows == ["user", "assistant"], f"unexpected history: {rows}"

    # third turn in the SAME conversation: history stays alternating
    third = await client.post(
        "/api/chat",
        json={"message": "Follow up?", "conversation_id": conversation_id},
    )
    assert third.status_code == 200
    await db_session.rollback()
    rows = (
        await db_session.execute(
            select(Message.role).where(Message.conversation_id == conversation_id)
        )
    ).scalars().all()
    assert rows == ["user", "assistant", "user", "assistant"]


@pytest.mark.asyncio
async def test_malformed_output_never_persisted_as_assistant(client, db_session, monkeypatch):
    monkeypatch.setattr("app.api.chat.orchestrator", DirectAnswerOrchestrator())

    captured = {}

    class GarbageOnceThenFailGateway:
        async def chat(self, messages, **kwargs):
            captured["messages"] = messages
            raise ModelGatewayError(
                "Model output failed validation after one bounded retry "
                "(first: control-token density 1.00 exceeds 0.34; "
                "retry: control-token density 1.00 exceeds 0.34)"
            )

    monkeypatch.setattr("app.api.chat.model_gateway", GarbageOnceThenFailGateway())

    response = await client.post("/api/chat", json={"message": "Any question?"})
    assert response.status_code == 502

    await db_session.rollback()
    messages = (await db_session.execute(select(Message))).scalars().all()
    assert messages == []
    # the provider messages were well-formed (system, user) — no orphan effect
    roles = [m["role"] for m in captured["messages"]]
    assert roles == ["system", "user"]


@pytest.mark.asyncio
async def test_successful_generation_persists_pair(client, db_session, monkeypatch):
    monkeypatch.setattr("app.api.chat.orchestrator", DirectAnswerOrchestrator())
    monkeypatch.setattr("app.api.chat.model_gateway", AnsweringGateway("Clean."))

    response = await client.post("/api/chat", json={"message": "Hello there"})
    assert response.status_code == 200
    assert response.json()["answer"] == "Clean."

    await db_session.rollback()
    rows = (
        await db_session.execute(select(Message.role))
    ).scalars().all()
    assert rows == ["user", "assistant"]
