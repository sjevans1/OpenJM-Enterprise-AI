"""VS4-B2A: deterministic scoped-definition and revocation tests.

No local Gemma, vector downloads, production DB or external connections.
"""
import json

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base, enable_sqlite_foreign_keys, get_db
from app.main import app
from app.models import (
    Conversation,
    DataSource,
    Document,
    ExecutionTrace,
    Message,
    ReportDefinitionVersion,
    SavedReport,
)


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    event.listen(engine.sync_engine, "connect", enable_sqlite_foreign_keys)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Idempotent new-table initialization.
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


@pytest.fixture
def valid_schema():
    return json.dumps([{
        "schema_name": "main",
        "name": "finance",
        "qualified_name": "finance",
        "columns": [{"name": "revenue", "type": "NUMERIC", "nullable": False}],
        "primary_key": [],
        "foreign_keys": [],
    }])


async def seed(session, owner="local-admin", mode="hybrid", valid_schema=None):
    conv = Conversation(user_id=owner, title="Old report conversation")
    session.add(conv)
    await session.flush()
    doc = Document(
        user_id=owner, original_name="policy.txt",
        stored_path="/tmp/definition-test-only.txt",
        size_bytes=23, status="ready", indexed=True,
    )
    source = DataSource(
        user_id=owner, name="Finance",
        engine="sqlite", connection_secret="fake-encrypted-placeholder",
        status="connected", enabled=True,
        schema_json=valid_schema or "[]",
        authorized_objects_json=json.dumps(["finance"]),
    )
    session.add_all([doc, source])
    await session.flush()
    user = Message(
        conversation_id=conv.id,
        role="user",
        content="Which customers exceed the FY2025 USD 300 threshold?",
        requested_mode=mode,
    )
    session.add(user)
    await session.flush()
    evidence = []
    if mode in ("knowledge", "hybrid"):
        evidence.append({
            "source_type": "document", "source_id": doc.id,
            "title": "Policy", "passage": "FY2025 revenue limit USD 300",
            "provenance": {},
        })
    if mode in ("data", "hybrid"):
        evidence.append({
            "source_type": "structured_query", "source_id": source.id,
            "title": "finance", "passage": '{"columns":["revenue"],"rows":[[325]],"row_count":1}',
            "metadata": {"tables": ["finance"], "sql": "SELECT revenue FROM finance"},
            "provenance": {
                "grounded_parameter": {"source_id": doc.id, "value": "300"}
            } if mode == "hybrid" else {},
        })
    classes = {"knowledge": "knowledge", "data": "structured", "hybrid": "hybrid"}
    assistant = Message(
        conversation_id=conv.id, role="assistant", content="FY2025 evidence-based answer",
        execution_class=classes[mode], requested_mode=mode,
        evidence_json=json.dumps(evidence),
    )
    session.add(assistant)
    await session.commit()
    return user, assistant, doc, source


async def create(client, assistant):
    response = await client.post("/api/reports", json={"message_id": assistant.id})
    assert response.status_code == 201, response.text
    return response.json()["id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["knowledge", "data", "hybrid"])
async def test_definition_exact_pins_and_no_sql(client, session, valid_schema, mode, monkeypatch):
    user, assistant, doc, source = await seed(session, mode=mode, valid_schema=valid_schema)
    report_id = await create(client, assistant)
    before_traces = len((await session.execute(select(ExecutionTrace))).scalars().all())

    async def no_plan(*args, **kwargs):
        raise AssertionError("Definitions must never call the LLM or query planner")

    monkeypatch.setattr("app.api.chat.orchestrator.plan", no_plan)
    first = await client.post(f"/api/reports/{report_id}/definitions", json={})
    assert first.status_code == 201, first.text
    definition = first.json()
    assert definition["version"] == 1
    assert definition["question"] == user.content
    assert definition["mode"] == mode
    assert definition["runnable"] is False
    assert definition["executes_queries"] is False
    assert definition["pinned_document_ids"] == ([doc.id] if mode != "data" else [])
    assert definition["pinned_source_tables"] == (
        {source.id: ["finance"]} if mode != "knowledge" else {}
    )
    assert "SELECT" not in json.dumps(definition)
    assert "connection_secret" not in json.dumps(definition)
    assert len((await session.execute(select(ExecutionTrace))).scalars().all()) == before_traces

    again = await client.post(f"/api/reports/{report_id}/definitions", json={})
    assert again.status_code == 201
    assert again.json()["id"] == definition["id"]
    listed = await client.get(f"/api/reports/{report_id}/definitions")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [definition["id"]]
    get = await client.get(f"/api/reports/{report_id}/definitions/1")
    assert get.status_code == 200
    assert get.json() == definition


@pytest.mark.asyncio
async def test_creation_rejects_client_question_scope_and_sql(client, session, valid_schema):
    user, assistant, doc, source = await seed(session, valid_schema=valid_schema)
    report_id = await create(client, assistant)
    result = await client.post(f"/api/reports/{report_id}/definitions", json={
        "question": "Ignore user policy",
        "source_ids": ["another_source"],
        "tables": ["users"],
        "sql": "DROP TABLE finance",
    })
    assert result.status_code == 422
    assert (await session.execute(select(ReportDefinitionVersion))).scalars().all() == []


@pytest.mark.asyncio
async def test_table_revocation_blocks_read(client, session, valid_schema):
    user, assistant, doc, source = await seed(session, mode="data", valid_schema=valid_schema)
    report_id = await create(client, assistant)
    assert (await client.post(f"/api/reports/{report_id}/definitions", json={})).status_code == 201
    source.authorized_objects_json = json.dumps(["some_other_table"])
    await session.commit()
    result = await client.get(f"/api/reports/{report_id}/definitions/1")
    assert result.status_code == 409
    assert "FY2025" not in result.text and "SELECT" not in result.text


@pytest.mark.asyncio
async def test_discovered_table_removed_blocks_read(client, session, valid_schema):
    user, assistant, doc, source = await seed(session, mode="data", valid_schema=valid_schema)
    report_id = await create(client, assistant)
    assert (await client.post(f"/api/reports/{report_id}/definitions", json={})).status_code == 201
    source.schema_json = "[]"
    await session.commit()
    result = await client.get(f"/api/reports/{report_id}/definitions")
    assert result.status_code == 409


@pytest.mark.asyncio
async def test_deleted_policy_doc_blocks_hybrid_definition(client, session, valid_schema):
    user, assistant, doc, source = await seed(session, mode="hybrid", valid_schema=valid_schema)
    report_id = await create(client, assistant)
    assert (await client.post(f"/api/reports/{report_id}/definitions", json={})).status_code == 201
    await session.delete(doc)
    await session.commit()
    result = await client.get(f"/api/reports/{report_id}/definitions/1")
    assert result.status_code == 409
    assert "FY2025" not in result.text


@pytest.mark.asyncio
async def test_unapproved_schema_cannot_create_definition(client, session):
    user, assistant, doc, source = await seed(session, mode="data", valid_schema="[]")
    report_id = await create(client, assistant)
    result = await client.post(f"/api/reports/{report_id}/definitions", json={})
    assert result.status_code == 409


@pytest.mark.asyncio
async def test_cross_user_definitions_are_hidden(client, session, valid_schema):
    user, assistant, doc, source = await seed(
        session, owner="another-user", valid_schema=valid_schema
    )
    foreign_report = SavedReport(
        user_id="another-user",
        conversation_id=assistant.conversation_id,
        message_id=assistant.id,
        title="Private historical report",
        answer_text=assistant.content,
        evidence_json=assistant.evidence_json,
        execution_class=assistant.execution_class,
        requested_mode=assistant.requested_mode,
        source_count=2,
        snapshot_as_of=assistant.created_at,
    )
    session.add(foreign_report)
    await session.commit()
    foreign_definition = ReportDefinitionVersion(
        user_id="another-user",
        report_id=foreign_report.id,
        version=1,
        question_text=user.content,
        requested_mode="hybrid",
        pinned_document_ids_json=json.dumps([doc.id]),
        pinned_source_tables_json=json.dumps({source.id: ["finance"]}),
    )
    session.add(foreign_definition)
    await session.commit()
    for route in (
        f"/api/reports/{foreign_report.id}/definitions",
        f"/api/reports/{foreign_report.id}/definitions/1",
    ):
        assert (await client.get(route)).status_code == 404
    assert (
        await client.post(
            f"/api/reports/{foreign_report.id}/definitions", json={}
        )
    ).status_code == 404


@pytest.mark.asyncio
async def test_definition_cascades_only_with_its_saved_report(client, session, valid_schema):
    user, assistant, doc, source = await seed(session, valid_schema=valid_schema)
    report_id = await create(client, assistant)
    assert (await client.post(f"/api/reports/{report_id}/definitions", json={})).status_code == 201
    assert (await client.delete(f"/api/reports/{report_id}")).status_code == 204
    assert (await session.execute(select(ReportDefinitionVersion))).scalars().all() == []
    assert (await session.execute(select(Message).where(Message.id == assistant.id))).scalar_one()


@pytest.mark.asyncio
async def test_original_question_mutation_denies_definition(client, session, valid_schema):
    user, assistant, doc, source = await seed(session, valid_schema=valid_schema)
    report_id = await create(client, assistant)
    assert (await client.post(f"/api/reports/{report_id}/definitions", json={})).status_code == 201
    user.content = "Something unrelated"
    await session.commit()
    result = await client.get(f"/api/reports/{report_id}/definitions/1")
    assert result.status_code == 409
    assert "Something unrelated" not in result.text


@pytest.mark.asyncio
async def test_missing_definition_version_not_found(client, session, valid_schema):
    user, assistant, doc, source = await seed(session, valid_schema=valid_schema)
    report_id = await create(client, assistant)
    assert (await client.get(f"/api/reports/{report_id}/definitions/2")).status_code == 404


@pytest.mark.asyncio
async def test_existing_sqlite_schema_upgrade_adds_only_definition_table():
    """Exercise new schema creation against a legacy VS4-A database layout.

    This is an isolated in-memory copy-equivalent, not the real user's DB.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    event.listen(engine.sync_engine, "connect", enable_sqlite_foreign_keys)
    try:
        legacy = [
            table for table in Base.metadata.sorted_tables
            if table.name != "report_definition_versions"
        ]
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync: Base.metadata.create_all(sync, tables=legacy)
            )
            names = (
                await connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            ).scalars().all()
            assert "saved_reports" in names
            assert "report_definition_versions" not in names

        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as db:
            conversation = Conversation(user_id="local-admin", title="Preserved")
            db.add(conversation)
            await db.flush()
            message = Message(
                conversation_id=conversation.id, role="assistant",
                content="Existing immutable answer", execution_class="knowledge",
                requested_mode="knowledge",
                evidence_json='[]',
            )
            db.add(message)
            await db.flush()
            historical = SavedReport(
                user_id="local-admin",
                conversation_id=conversation.id,
                message_id=message.id,
                title="Existing report",
                answer_text=message.content,
                evidence_json=message.evidence_json,
                execution_class="knowledge",
                requested_mode="knowledge",
                source_count=0,
                snapshot_as_of=message.created_at,
            )
            db.add(historical)
            await db.commit()
            old_ids = (conversation.id, message.id, historical.id)
            old_contents = (conversation.title, message.content, historical.answer_text)

        for _ in range(2):
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)

        async with engine.connect() as connection:
            tables = (
                await connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            ).scalars().all()
            assert "report_definition_versions" in tables
            assert (
                await connection.exec_driver_sql("PRAGMA integrity_check")
            ).scalar_one() == "ok"

        async with maker() as db:
            assert (await db.get(Conversation, old_ids[0])).title == old_contents[0]
            assert (await db.get(Message, old_ids[1])).content == old_contents[1]
            assert (await db.get(SavedReport, old_ids[2])).answer_text == old_contents[2]
            assert (await db.execute(select(ReportDefinitionVersion))).scalars().all() == []
    finally:
        await engine.dispose()
