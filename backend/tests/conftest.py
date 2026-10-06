"""Shared test fixtures for OpenJM backend tests.

pytest fixtures (file_db, sessions, client, session) are auto-available to
every test module in this directory. The plain helpers (_pragmas, _Clock,
seed_definition) are importable via `from conftest import ...`.
"""
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.db import Base, enable_sqlite_foreign_keys, get_db, _migrate_add_active_run_index
from app.main import app

settings = get_settings()


@pytest.fixture(autouse=True)
def _identity_context():
    """Publish the local development principal for tests.

    VS5 moved identity out of a settings string and into a validated,
    request-scoped principal. Tests that call the API get one through the
    dependency; tests that call services directly get the same trusted context
    here, so no test can accidentally run with an implicit "no identity" state.
    """
    from app.core.context import reset_principal, set_principal
    from app.core.identity import Principal
    from app.core.permissions import permissions_for_role
    from app.core.tenancy import (
        LEGACY_PRINCIPAL_ID,
        LEGACY_PRINCIPAL_SUBJECT,
        LEGACY_TENANT_ID,
    )

    principal = Principal(
        principal_id=LEGACY_PRINCIPAL_ID,
        tenant_id=LEGACY_TENANT_ID,
        subject=LEGACY_PRINCIPAL_SUBJECT,
        role="owner",
        membership_id="test-membership",
        auth_method="local-dev",
        permissions=permissions_for_role("owner"),
    )
    set_principal(principal)
    try:
        yield principal
    finally:
        reset_principal()


@pytest.fixture(autouse=True)
def _enable_report_runs(monkeypatch):
    """VS4-B2C2 Phase 5: enable report-run execution by default in tests.

    The release gate defaults to False (424); tests that exercise the
    POST /runs path need it True. Individual tests may override via their
    own monkeypatch.setattr on report_runs_api.settings.
    """
    import app.api.report_runs as _rr_api
    monkeypatch.setattr(
        _rr_api, "settings",
        _rr_api.settings.model_copy(update={"report_runs_enabled": True}),
    )


def _pragmas(dbapi_connection, _record):
    enable_sqlite_foreign_keys(dbapi_connection, _record)
    cur = dbapi_connection.cursor()
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()


class _Clock:
    """Deterministic callable datetime factory for tests."""

    def __init__(self, start):
        self._now = start

    def __call__(self):
        return self._now


@pytest.fixture
async def file_db(tmp_path):
    """File-backed SQLite engine + async_sessionmaker; tables created, reused per test."""
    path = tmp_path / "b2c1.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{path}", pool_recycle=999, connect_args={"timeout": 10}
    )
    event.listen(engine.sync_engine, "connect", _pragmas)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _migrate_add_active_run_index(engine=engine)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    # Provision the local tenant/principal/membership so the trusted-identity
    # dependency resolves a real principal from the database, exactly as a
    # migrated deployment would.
    from app.services.identity import ensure_local_identity

    async with maker() as db:
        await ensure_local_identity(db)
    try:
        yield maker
    finally:
        await engine.dispose()


@pytest.fixture
async def sessions(file_db):
    """Two distinct sessions over the same file-backed store."""
    async with file_db() as a, file_db() as b:
        yield a, b


@pytest.fixture
async def client(file_db):
    """ASGI test client with get_db overridden to the file_db engine."""
    async def override_db():
        async with file_db() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http
    app.dependency_overrides.clear()


@pytest.fixture
async def session():
    """In-memory SQLite async session for backend tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    from app.services.identity import ensure_local_identity

    async with maker() as provision:
        await ensure_local_identity(provision)
    async with maker() as db:
        yield db
    await engine.dispose()


async def seed_definition(maker) -> dict:
    """Persist a minimal report + definition version for reuse.

    Uses the real (trusted) model path: a Conversation -> assistant Message ->
    SavedReport -> ReportDefinitionVersion, mirroring the production create flow.
    Returns a dict with report_id, definition_id, definition_version, assistant_id.
    """
    import json as _json
    from app.models import (
        Conversation,
        DataSource,
        Document,
        Message,
        ReportDefinitionVersion,
        SavedReport,
    )

    async with maker() as db:
        conv = Conversation(user_id=settings.dev_user_id, title="Conv")
        db.add(conv)
        await db.flush()
        doc = Document(
            user_id=settings.dev_user_id,
            original_name="policy.txt",
            stored_path="/tmp/b2c1-test-only-policy",
            size_bytes=22,
            status="ready",
            indexed=True,
        )
        src = DataSource(
            user_id=settings.dev_user_id,
            name="Finance",
            engine="sqlite",
            connection_secret="fixture-only-not-real",
            status="connected",
            enabled=True,
            schema_json=_json.dumps(
                [
                    {
                        "schema_name": "main",
                        "name": "finance",
                        "qualified_name": "finance",
                        "columns": [
                            {"name": "revenue", "type": "NUMERIC", "nullable": False}
                        ],
                        "primary_key": [],
                        "foreign_keys": [],
                    }
                ]
            ),
            authorized_objects_json=_json.dumps(["finance"]),
        )
        db.add_all([doc, src])
        await db.flush()
        user = Message(
            conversation_id=conv.id,
            role="user",
            content="Which customers exceed the FY2025 USD 300 threshold?",
            requested_mode="hybrid",
        )
        db.add(user)
        await db.flush()
        evidence = [
            {
                "source_type": "document",
                "source_id": doc.id,
                "title": "Policy",
                "passage": "FY2025 threshold USD 300",
            },
            {
                "source_type": "structured_query",
                "source_id": src.id,
                "title": "Finance",
                "passage": '{"columns":["revenue"],"rows":[[325]],"row_count":1}',
                "metadata": {"tables": ["finance"], "sql": "SELECT revenue FROM finance"},
                "provenance": {
                    "grounded_parameter": {"source_id": doc.id, "value": "300"}
                },
            },
        ]
        assistant = Message(
            conversation_id=conv.id,
            role="assistant",
            content="Delta Co exceeded the FY2025 USD 300 threshold.",
            execution_class="hybrid",
            requested_mode="hybrid",
            evidence_json=_json.dumps(evidence),
        )
        db.add(assistant)
        await db.flush()
        report = SavedReport(
            user_id=settings.dev_user_id,
            conversation_id=conv.id,
            message_id=assistant.id,
            title="Budget review",
            answer_text=assistant.content,
            evidence_json=assistant.evidence_json,
            execution_class="hybrid",
            requested_mode="hybrid",
            source_count=2,
            snapshot_as_of=assistant.created_at,
        )
        db.add(report)
        await db.flush()
        definition = ReportDefinitionVersion(
            user_id=settings.dev_user_id,
            report_id=report.id,
            version=1,
            question_text=user.content,
            requested_mode="hybrid",
            pinned_document_ids_json=_json.dumps([doc.id]),
            pinned_source_tables_json=_json.dumps({src.id: ["finance"]}),
        )
        db.add(definition)
        await db.commit()
        return {
            "report_id": report.id,
            "definition_id": definition.id,
            "definition_version": 1,
            "assistant_id": assistant.id,
            "question": user.content,
            "requested_mode": "hybrid",
            "pinned_document_ids": [doc.id],
            "pinned_source_tables": {src.id: ["finance"]},
        }
