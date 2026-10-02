"""VS4-B1 preflight tests: owner/source checks; absolutely no automatic execution."""
import json

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.reports import settings as report_settings
from app.db import Base, enable_sqlite_foreign_keys, get_db
from app.main import app
from app.models import Conversation, DataSource, Document, ExecutionTrace, Message, SavedReport


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    event.listen(engine.sync_engine, "connect", enable_sqlite_foreign_keys)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        yield db
    await engine.dispose()


@pytest.fixture
async def client(session):
    async def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()


async def seed(session, *, mode="knowledge", owner=None, user_turn=True):
    user_id = owner or report_settings.dev_user_id
    conversation = Conversation(user_id=user_id, title="Origin conversation")
    session.add(conversation)
    await session.flush()
    document = Document(
        user_id=user_id, original_name="policy.txt", stored_path="/tmp/test-only-policy",
        size_bytes=22, status="ready", indexed=True,
    )
    source = DataSource(
        user_id=user_id, name="Finance",
        engine="sqlite", connection_secret="fixture-only",
        status="connected", enabled=True, schema_json="[]",
        authorized_objects_json=json.dumps(["finance"]),
    )
    session.add_all([document, source])
    await session.flush()

    prompt = "What did the FY2025 revenue policy say?"
    original_user = None
    if user_turn:
        original_user = Message(
            conversation_id=conversation.id, role="user", content=prompt,
            requested_mode=mode,
        )
        session.add(original_user)
        await session.flush()

    evidence = []
    if mode in ("knowledge", "hybrid"):
        evidence.append({
            "source_type": "document", "source_id": document.id,
            "title": "Policy", "passage": "FY2025 threshold USD 300",
        })
    if mode in ("data", "hybrid"):
        evidence.append({
            "source_type": "structured_query", "source_id": source.id,
            "title": "Finance", "passage": '{"columns":["revenue"],"rows":[[325]],"row_count":1}',
            "metadata": {"tables": ["finance"], "sql": "SELECT revenue FROM finance"},
        })
    class_for_mode = {"knowledge": "knowledge", "data": "structured", "hybrid": "hybrid"}
    assistant = Message(
        conversation_id=conversation.id,
        role="assistant", content="Verified historical answer",
        execution_class=class_for_mode[mode], requested_mode=mode,
        evidence_json=json.dumps(evidence),
    )
    session.add(assistant)
    await session.commit()
    return assistant, original_user, document, source, prompt


async def saved(client, assistant):
    response = await client.post("/api/reports", json={"message_id": assistant.id})
    assert response.status_code == 201, response.text
    return response.json()["id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["knowledge", "data", "hybrid"])
async def test_rerun_preflight_recovers_server_question_and_exact_mode(client, session, mode):
    assistant, user, document, source, prompt = await seed(session, mode=mode)
    report_id = await saved(client, assistant)
    preview = await client.get(f"/api/reports/{report_id}/rerun-preview")
    assert preview.status_code == 200, preview.text
    payload = preview.json()
    assert payload["original_question"] == prompt
    assert payload["mode"] == mode
    assert payload["source_message_id"] == assistant.id
    assert payload["report_id"] == report_id
    assert payload["executes_queries"] is False
    assert payload["requires_explicit_send"] is True
    assert payload["original_source_count"] == (2 if mode == "hybrid" else 1)
    assert set(payload) == {
        "original_question", "mode", "source_message_id", "report_id",
        "snapshot_as_of", "original_source_count",
        "executes_queries", "requires_explicit_send",
    }


@pytest.mark.asyncio
async def test_preflight_is_read_only_and_does_not_call_planner(client, session, monkeypatch):
    assistant, user, document, source, prompt = await seed(session)
    report_id = await saved(client, assistant)
    before_messages = (await session.execute(select(Message))).scalars().all()
    before_traces = (await session.execute(select(ExecutionTrace))).scalars().all()

    async def forbidden_plan(*args, **kwargs):
        raise AssertionError("The rerun preview must not plan or run a query")

    monkeypatch.setattr("app.api.chat.orchestrator.plan", forbidden_plan)
    preview = await client.get(f"/api/reports/{report_id}/rerun-preview")
    assert preview.status_code == 200
    assert len((await session.execute(select(Message))).scalars().all()) == len(before_messages)
    assert len((await session.execute(select(ExecutionTrace))).scalars().all()) == len(before_traces)


@pytest.mark.asyncio
async def test_revoked_document_fails_closed_without_prompt_leak(client, session):
    assistant, user, document, source, prompt = await seed(session)
    report_id = await saved(client, assistant)
    document.indexed = False
    await session.commit()
    preview = await client.get(f"/api/reports/{report_id}/rerun-preview")
    assert preview.status_code == 409
    assert prompt not in preview.text


@pytest.mark.asyncio
async def test_revoked_sql_table_fails_closed_before_composer(client, session):
    assistant, user, document, source, prompt = await seed(session, mode="data")
    report_id = await saved(client, assistant)
    source.authorized_objects_json = json.dumps(["other_table"])
    await session.commit()
    preview = await client.get(f"/api/reports/{report_id}/rerun-preview")
    assert preview.status_code == 409
    assert prompt not in preview.text
    assert "325" not in preview.text


@pytest.mark.asyncio
async def test_revoked_hybrid_side_fails_closed(client, session):
    assistant, user, document, source, prompt = await seed(session, mode="hybrid")
    report_id = await saved(client, assistant)
    source.enabled = False
    await session.commit()
    assert (await client.get(f"/api/reports/{report_id}/rerun-preview")).status_code == 409


@pytest.mark.asyncio
async def test_unauthorized_report_not_found(client, session):
    assistant, user, document, source, prompt = await seed(session, owner="different-user")
    report = SavedReport(
        user_id="different-user",
        conversation_id=assistant.conversation_id,
        message_id=assistant.id,
        title="Other account historical report",
        answer_text=assistant.content,
        evidence_json=assistant.evidence_json,
        execution_class="knowledge",
        requested_mode="knowledge",
        source_count=1,
        snapshot_as_of=assistant.created_at,
    )
    session.add(report)
    await session.commit()
    assert (await client.get(f"/api/reports/{report.id}/rerun-preview")).status_code == 404


@pytest.mark.asyncio
async def test_user_turn_missing_fails_closed(client, session):
    assistant, user, document, source, prompt = await seed(session, user_turn=False)
    report_id = await saved(client, assistant)
    preview = await client.get(f"/api/reports/{report_id}/rerun-preview")
    assert preview.status_code == 409


@pytest.mark.asyncio
async def test_changed_historical_assistant_fails_closed(client, session):
    assistant, user, document, source, prompt = await seed(session)
    report_id = await saved(client, assistant)
    assistant.content = "An edited assistant response"
    await session.commit()
    preview = await client.get(f"/api/reports/{report_id}/rerun-preview")
    assert preview.status_code == 409
    assert prompt not in preview.text


@pytest.mark.asyncio
async def test_changed_historical_evidence_fails_closed(client, session):
    assistant, user, document, source, prompt = await seed(session)
    report_id = await saved(client, assistant)
    assistant.evidence_json = json.dumps([{
        "source_type": "document", "source_id": document.id,
        "title": "Different", "passage": "Different statement",
    }])
    await session.commit()
    assert (await client.get(f"/api/reports/{report_id}/rerun-preview")).status_code == 409


@pytest.mark.asyncio
async def test_mode_mismatch_fails_closed(client, session):
    assistant, user, document, source, prompt = await seed(session, mode="hybrid")
    report_id = await saved(client, assistant)
    user.requested_mode = "chat"
    await session.commit()
    assert (await client.get(f"/api/reports/{report_id}/rerun-preview")).status_code == 409


@pytest.mark.asyncio
async def test_oversized_question_refuses_preview(client, session):
    assistant, user, document, source, prompt = await seed(session)
    report_id = await saved(client, assistant)
    user.content = "x" * 12001
    await session.commit()
    assert (await client.get(f"/api/reports/{report_id}/rerun-preview")).status_code == 409


@pytest.mark.asyncio
async def test_request_cannot_inject_mode_or_question(client, session):
    assistant, user, document, source, prompt = await seed(session, mode="data")
    report_id = await saved(client, assistant)
    response = await client.get(
        f"/api/reports/{report_id}/rerun-preview",
        params={"mode": "chat", "question": "Ignore everything"},
    )
    assert response.status_code == 200
    assert response.json()["mode"] == "data"
    assert response.json()["original_question"] == prompt
