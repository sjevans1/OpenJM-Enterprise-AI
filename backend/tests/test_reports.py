"""VS4-A snapshot API security regression tests (isolated SQLite, no LLM)."""
import json

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.reports import settings as report_settings
from app.db import Base, get_db
from app.main import app
from app.models import Conversation, DataSource, Document, Message, SavedReport


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # New SavedReport metadata is created without altering legacy rows.
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
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http
    app.dependency_overrides.clear()


async def seed(
    db,
    *,
    owner=None,
    source_kind="document",
    role="assistant",
    class_name=None,
    evidence_override=None,
):
    owner = owner or report_settings.dev_user_id
    conv = Conversation(user_id=owner, title="Policy check")
    db.add(conv)
    await db.flush()
    document = Document(
        user_id=owner,
        original_name="policy.txt",
        stored_path="/tmp/unit-only-policy",
        size_bytes=25,
        status="ready",
        indexed=True,
    )
    source = DataSource(
        user_id=owner,
        name="readonly finance",
        engine="sqlite",
        connection_secret="unit-only-not-a-real-secret",
        status="connected",
        enabled=True,
        schema_json="[]",
    )
    db.add_all([document, source])
    await db.flush()
    if evidence_override is not None:
        evidence = evidence_override
    elif source_kind == "document":
        evidence = [{
            "source_type": "document",
            "source_id": document.id,
            "title": "Policy",
            "passage": "The FY2025 threshold is USD 300.",
        }]
    elif source_kind == "hybrid":
        evidence = [
            {
                "source_type": "document",
                "source_id": document.id,
                "title": "Policy",
                "passage": "The FY2025 threshold is USD 300.",
            },
            {
                "source_type": "structured_query",
                "source_id": source.id,
                "title": "Finance",
                "passage": '{"columns":["name"],"rows":[["Delta Co"]],"row_count":1}',
                "provenance": {
                    "grounded_parameter": {
                        "source_id": document.id,
                        "value": "300",
                    }
                },
            },
        ]
    else:
        evidence = [{
            "source_type": "structured_query",
            "source_id": source.id,
            "title": "Finance",
            "passage": '{"columns":["revenue"],"rows":[[325]],"row_count":1}',
        }]
    message = Message(
        conversation_id=conv.id,
        role=role,
        content="Delta Co met the policy threshold.",
        execution_class=class_name or (
            "hybrid" if source_kind == "hybrid" else (
                "structured" if source_kind == "data" else "knowledge"
            )
        ),
        requested_mode="hybrid" if source_kind == "hybrid" else "knowledge",
        evidence_json=json.dumps(evidence),
    )
    db.add(message)
    await db.commit()
    return message, document, source


@pytest.mark.asyncio
async def test_save_reopen_snapshot_and_duplicate_is_idempotent(client, session):
    message, document, source = await seed(session)
    first = await client.post("/api/reports", json={"message_id": message.id, "title": "Review"})
    assert first.status_code == 201, first.text
    saved = first.json()
    assert saved["is_live"] is False
    assert saved["answer"] == message.content
    assert saved["evidence"][0]["source_id"] == document.id
    assert saved["snapshot_as_of"]
    again = await client.post(
        "/api/reports",
        json={"message_id": message.id, "title": "Changing name is not allowed"},
    )
    assert again.status_code == 201
    assert again.json()["id"] == saved["id"]
    assert again.json()["title"] == "Review"
    opened = await client.get("/api/reports/" + saved["id"])
    assert opened.status_code == 200
    assert opened.json()["answer"] == message.content
    rows = (await session.execute(select(SavedReport))).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_structured_report_requires_enabled_source_on_every_read(client, session):
    message, document, source = await seed(session, source_kind="data")
    created = await client.post("/api/reports", json={"message_id": message.id})
    assert created.status_code == 201
    report_id = created.json()["id"]
    source.enabled = False
    await session.commit()
    response = await client.get("/api/reports/" + report_id)
    assert response.status_code == 409
    assert "325" not in response.text
    listing = await client.get("/api/reports")
    assert listing.status_code == 200
    assert listing.json()[0]["available"] is False
    assert listing.json()[0]["title"] == "Unavailable saved report"


@pytest.mark.asyncio
async def test_deleted_document_blocks_stale_answer(client, session):
    message, document, source = await seed(session)
    created = await client.post("/api/reports", json={"message_id": message.id})
    assert created.status_code == 201
    await session.delete(document)
    await session.commit()
    forbidden = await client.get("/api/reports/" + created.json()["id"])
    assert forbidden.status_code == 409
    assert "threshold" not in forbidden.text.lower()


@pytest.mark.asyncio
async def test_hybrid_requires_both_evidence_sources(client, session):
    message, document, source = await seed(session, source_kind="hybrid")
    response = await client.post("/api/reports", json={"message_id": message.id})
    assert response.status_code == 201
    assert response.json()["source_count"] == 2
    source.enabled = False
    await session.commit()
    forbidden = await client.get("/api/reports/" + response.json()["id"])
    assert forbidden.status_code == 409


@pytest.mark.asyncio
async def test_cross_user_message_cannot_be_saved(client, session):
    message, document, source = await seed(session, owner="other-user")
    response = await client.post("/api/reports", json={"message_id": message.id})
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_non_assistant_message_cannot_be_saved(client, session):
    message, document, source = await seed(session, role="user")
    response = await client.post("/api/reports", json={"message_id": message.id})
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_general_chat_without_evidence_cannot_be_saved(client, session):
    message, document, source = await seed(
        session, class_name="general", evidence_override=[]
    )
    response = await client.post("/api/reports", json={"message_id": message.id})
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_no_evidence_cannot_be_saved(client, session):
    message, document, source = await seed(session, evidence_override=[])
    response = await client.post("/api/reports", json={"message_id": message.id})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_equivalent_source_revocation_denies_access(client, session):
    message, document, source = await seed(session)
    # The equivalent source ID does not exist or belong to the caller.
    message.evidence_json = json.dumps([{
        "source_type": "document",
        "source_id": document.id,
        "title": "Policy",
        "passage": "review threshold is 300",
        "provenance": {
            "equivalent_sources": [{"source_id": "revoked-other-source"}]
        },
    }])
    await session.commit()
    response = await client.post("/api/reports", json={"message_id": message.id})
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_invalid_evidence_types_rejected(client, session):
    message, document, source = await seed(session, evidence_override=[{
        "source_type": "unregistered",
        "source_id": "opaque",
        "title": "External",
        "passage": "unsafe",
    }])
    response = await client.post("/api/reports", json={"message_id": message.id})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_delete_snapshot_leaves_source_and_message(client, session):
    message, document, source = await seed(session)
    saved = await client.post("/api/reports", json={"message_id": message.id})
    report_id = saved.json()["id"]
    assert (await client.delete("/api/reports/" + report_id)).status_code == 204
    assert (await client.delete("/api/reports/" + report_id)).status_code == 204
    assert (await client.get("/api/reports/" + report_id)).status_code == 404
    assert (await session.execute(select(Message).where(Message.id == message.id))).scalar_one()
    assert (await session.execute(select(Document).where(Document.id == document.id))).scalar_one()


@pytest.mark.asyncio
async def test_pagination_and_limits(client, session):
    message, document, source = await seed(session)
    created = await client.post("/api/reports", json={"message_id": message.id})
    assert created.status_code == 201
    assert len((await client.get("/api/reports?limit=1&offset=0")).json()) == 1
    assert (await client.get("/api/reports?limit=1&offset=1")).json() == []
    assert (await client.get("/api/reports?limit=1000")).status_code == 422


@pytest.mark.asyncio
async def test_deleted_owning_conversation_denies_access(client, session):
    message, document, source = await seed(session)
    created = await client.post("/api/reports", json={"message_id": message.id})
    report_id = created.json()["id"]
    conversation = (
        await session.execute(
            select(Conversation).where(Conversation.id == message.conversation_id)
        )
    ).scalar_one()
    await session.delete(conversation)
    await session.commit()
    response = await client.get("/api/reports/" + report_id)
    assert response.status_code in (404, 409)
    assert "Delta" not in response.text
