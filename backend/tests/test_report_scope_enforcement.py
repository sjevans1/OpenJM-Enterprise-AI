"""VS4-B2B: pinned scope is enforced before retrieval, planning and SQL."""
import json

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.models import DataSource, Document
from app.schemas import DataColumnSchema, DataTableSchema, Evidence
from app.services.data_sources import encode_schema
from app.services.report_scope import (
    ReportScopeError,
    ReportSourceScope,
    source_scope_still_authorized,
)
from app.services.structured_planner import (
    StructuredPlanner,
    StructuredPlannerError,
    StructuredPlan,
    StructuredPlanningResult,
)
from app.services.structured_executor import (
    StructuredExecutionError,
    execute_structured_query,
)
from app.services.tools import (
    KnowledgeSearchTool,
    StructuredQueryTool,
    ToolContext,
    ToolPermissionError,
    ToolResult,
)
from app.services import tools as tools_module
from app.services import orchestrator as orchestrator_module
from app.services.orchestrator import OpenJMOrchestrator


def scope(*, docs=None, sources=None):
    return ReportSourceScope.from_pins(
        docs if docs is not None else ["allowed-doc"],
        sources if sources is not None else {"allowed-source": ["finance"]},
    )


def schema():
    return encode_schema([
        DataTableSchema(
            schema_name="main",
            name=name,
            qualified_name=name,
            columns=[
                DataColumnSchema(name="revenue", type="NUMERIC", nullable=False),
            ],
        )
        for name in ("finance", "payroll")
    ])


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        yield db
    await engine.dispose()


async def seed(session):
    session.add_all([
        Document(
            id=ident, user_id=owner, original_name=ident+".md",
            stored_path="/tmp/only-test-"+ident, size_bytes=13,
            status="ready", indexed=True,
        )
        for ident, owner in [
            ("allowed-doc", "local-admin"),
            ("unscoped-doc", "local-admin"),
            ("foreign-doc", "other-user"),
        ]
    ])
    session.add_all([
        DataSource(
            id=ident,
            user_id="local-admin",
            name=ident,
            engine="sqlite",
            connection_secret="not-an-actual-secret",
            status="connected", enabled=True,
            schema_json=schema(),
            authorized_objects_json=json.dumps(["finance", "payroll"]),
        )
        for ident in ("allowed-source", "unscoped-source")
    ])
    await session.commit()


@pytest.mark.parametrize("docs,sources", [
    ([], {}),
    (["x", "x"], {}),
    (["  "], {}),
    ([], {"source": ["finance", "FINANCE"]}),
    ([], {"source": []}),
    ([], {"source": ["finance"] * 33}),
])
def test_malformed_or_unbounded_scope_rejected(docs, sources):
    with pytest.raises(ReportScopeError):
        ReportSourceScope.from_pins(docs, sources)


@pytest.mark.asyncio
async def test_knowledge_candidates_only_include_pinned_docs(session, monkeypatch):
    await seed(session)
    seen = []
    async def fake_retrieve(question, refs, **kwargs):
        seen.extend(refs)
        return [Evidence(
            source_type="document", source_id="allowed-doc",
            title="approved", passage="approved fact",
        )]
    monkeypatch.setattr(tools_module.knowledge_engine, "retrieve", fake_retrieve)
    result = await KnowledgeSearchTool().execute(
        ToolContext(
            user_id="local-admin", db=session, report_scope=scope(),
            permissions=frozenset({"knowledge.read"}),
        ), {"query": "read policy"},
    )
    assert seen == [("allowed-doc", "allowed-doc.md")]
    assert [e.source_id for e in result.evidence] == ["allowed-doc"]


@pytest.mark.asyncio
async def test_missing_pinned_doc_denies_before_retrieval(session, monkeypatch):
    await seed(session)
    doc = await session.get(Document, "allowed-doc")
    doc.indexed = False
    await session.commit()
    async def forbidden(*args, **kwargs):
        pytest.fail("vector retrieval must never start after revocation")
    monkeypatch.setattr(tools_module.knowledge_engine, "retrieve", forbidden)
    with pytest.raises(ToolPermissionError):
        await KnowledgeSearchTool().execute(
            ToolContext(
                user_id="local-admin", db=session, report_scope=scope(),
                permissions=frozenset({"knowledge.read"}),
            ), {"query": "policy"},
        )


@pytest.mark.asyncio
async def test_vector_cannot_return_unpinned_evidence(session, monkeypatch):
    await seed(session)
    async def fake_retrieve(*args, **kwargs):
        return [Evidence(
            source_type="document", source_id="unscoped-doc",
            title="wrong", passage="forbidden data",
        )]
    monkeypatch.setattr(tools_module.knowledge_engine, "retrieve", fake_retrieve)
    with pytest.raises(ToolPermissionError):
        await KnowledgeSearchTool().execute(
            ToolContext(
                user_id="local-admin", db=session, report_scope=scope(),
                permissions=frozenset({"knowledge.read"}),
            ), {"query": "policy"},
        )


@pytest.mark.asyncio
async def test_scoped_planner_prompt_hides_unpinned_sources_and_tables(session, monkeypatch):
    await seed(session)
    planner = StructuredPlanner()
    checked = []
    async def fake_chat(messages, **kwargs):
        prompt = messages[0]["content"]
        checked.append(prompt)
        assert "SOURCE_ID: allowed-source" in prompt
        assert "SOURCE_ID: unscoped-source" not in prompt
        assert "- finance(" in prompt
        assert "- payroll(" not in prompt
        return json.dumps({
            "use_structured": True,
            "source_id": "allowed-source",
            "sql": "SELECT SUM(revenue) FROM finance",
            "rationale": "pinned finance",
        })
    monkeypatch.setattr(planner.model_gateway, "chat", fake_chat)
    decision = await planner.plan(
        "What is total revenue?", session, "local-admin", scope=scope(),
    )
    assert decision.plan.source_id == "allowed-source"
    assert len(checked) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["disabled", "grant", "schema"])
async def test_scoped_planner_fails_before_model_when_scope_changes(
    session, monkeypatch, change
):
    await seed(session)
    source = await session.get(DataSource, "allowed-source")
    if change == "disabled":
        source.enabled = False
    elif change == "grant":
        source.authorized_objects_json = json.dumps(["payroll"])
    else:
        source.schema_json = "[]"
    await session.commit()
    planner = StructuredPlanner()
    async def forbidden(*args, **kwargs):
        pytest.fail("model planning must not see revoked source/schema")
    monkeypatch.setattr(planner.model_gateway, "chat", forbidden)
    with pytest.raises(StructuredPlannerError):
        await planner.plan(
            "What is total revenue?", session, "local-admin", scope=scope(),
        )


@pytest.mark.asyncio
async def test_structured_tool_blocks_unpinned_source_before_execution(session, monkeypatch):
    await seed(session)
    async def forbidden(*args, **kwargs):
        pytest.fail("SQL executor must not run")
    monkeypatch.setattr(tools_module, "execute_structured_query", forbidden)
    with pytest.raises(ToolPermissionError):
        await StructuredQueryTool().execute(
            ToolContext(user_id="local-admin", db=session, report_scope=scope()),
            {"source_id": "unscoped-source", "sql": "SELECT revenue FROM finance"},
        )


@pytest.mark.asyncio
async def test_structured_tool_passes_narrow_table_scope_to_executor(session, monkeypatch):
    await seed(session)
    seen = []
    async def fake_execute(source, proposed_sql, *, scoped_tables=None):
        seen.append((source.id, proposed_sql, scoped_tables))
        raise StructuredExecutionError("Intentional test: do not connect")
    monkeypatch.setattr(tools_module, "execute_structured_query", fake_execute)
    with pytest.raises(StructuredExecutionError):
        await StructuredQueryTool().execute(
            ToolContext(user_id="local-admin", db=session, report_scope=scope()),
            {"source_id": "allowed-source", "sql": "SELECT revenue FROM finance"},
        )
    assert seen == [
        ("allowed-source", "SELECT revenue FROM finance", frozenset({"finance"}))
    ]


@pytest.mark.asyncio
async def test_structured_tool_denies_revoked_pinned_table_before_sql(session, monkeypatch):
    await seed(session)
    source = await session.get(DataSource, "allowed-source")
    source.authorized_objects_json = json.dumps(["payroll"])
    await session.commit()
    async def forbidden(*args, **kwargs):
        pytest.fail("no executor should start after grant revocation")
    monkeypatch.setattr(tools_module, "execute_structured_query", forbidden)
    with pytest.raises(ToolPermissionError):
        await StructuredQueryTool().execute(
            ToolContext(user_id="local-admin", db=session, report_scope=scope()),
            {"source_id": "allowed-source", "sql": "SELECT revenue FROM finance"},
        )


@pytest.mark.asyncio
async def test_sql_policy_rejects_unpinned_table_before_decrypt(session):
    await seed(session)
    source = await session.get(DataSource, "allowed-source")
    with pytest.raises(StructuredExecutionError):
        await execute_structured_query(
            source, "SELECT revenue FROM payroll",
            scoped_tables=frozenset({"finance"}),
        )


@pytest.mark.asyncio
async def test_scoped_orchestrator_preflight_denies_revocation_without_tools(
    session, monkeypatch
):
    await seed(session)
    doc = await session.get(Document, "allowed-doc")
    doc.indexed = False
    await session.commit()
    async def forbidden(*args, **kwargs):
        pytest.fail("planner/retrieval must not run")
    monkeypatch.setattr(orchestrator_module.structured_planner, "plan", forbidden)
    monkeypatch.setattr(orchestrator_module.tool_registry, "execute", forbidden)
    with pytest.raises(ReportScopeError):
        await OpenJMOrchestrator().plan(
            "What is revenue and policy?",
            session, "local-admin", mode="hybrid", scope=scope(),
        )


def test_grants_must_be_current_and_explicit():
    source = DataSource(
        id="allowed-source", user_id="local-admin", name="Finance",
        engine="sqlite", connection_secret="fixture",
        status="connected", enabled=True, schema_json=schema(),
        authorized_objects_json=json.dumps(["finance", "payroll"]),
    )
    assert source_scope_still_authorized(source, frozenset({"finance"}))
    source.authorized_objects_json = None
    assert not source_scope_still_authorized(source, frozenset({"finance"}))


def test_scoped_sql_policy_requires_exact_schema_table_identity():
    """Qualified private.finance must not match an unqualified finance pin."""
    from app.services.sql_policy import SQLPolicyError, validate_and_rewrite_sql
    with pytest.raises(SQLPolicyError, match="unauthorized"):
        validate_and_rewrite_sql(
            "SELECT revenue FROM private.finance",
            dialect="postgres",
            allowed_tables={"finance"},
            allowed_columns={"finance": {"revenue"}},
            max_rows=20,
            require_exact_table_match=True,
        )
    authorized = validate_and_rewrite_sql(
        "SELECT revenue FROM public.finance",
        dialect="postgres",
        allowed_tables={"public.finance"},
        allowed_columns={"public.finance": {"revenue"}},
        max_rows=20,
        require_exact_table_match=True,
    )
    assert authorized.tables == ("public.finance",)


def test_unqualified_ambiguous_and_postgres_scope_pins_are_denied():
    """Duplicate base-table names cannot become a capability for either schema."""
    tables = [
        DataTableSchema(
            schema_name=prefix, name="finance",
            qualified_name=f"{prefix}.finance",
            columns=[
                DataColumnSchema(name="revenue", type="NUMERIC", nullable=False),
            ],
        )
        for prefix in ("public", "private")
    ]
    source = DataSource(
        id="source", user_id="local-admin", name="Finance",
        engine="postgresql", connection_secret="not-actual-credentials",
        status="connected", enabled=True, schema_json=encode_schema(tables),
        authorized_objects_json=json.dumps(["finance", "public.finance"]),
    )
    assert not source_scope_still_authorized(source, frozenset({"finance"}))
    assert source_scope_still_authorized(source, frozenset({"public.finance"}))
    assert not source_scope_still_authorized(source, frozenset({"private.finance"}))


@pytest.mark.asyncio
async def test_scoped_executor_rejects_other_schema_before_credential_decrypt(monkeypatch):
    """Block qualified-name policy bypass even for equal final table names."""
    from app.services import structured_executor as executor_module
    tables = [
        DataTableSchema(
            schema_name=prefix, name="finance",
            qualified_name=f"{prefix}.finance",
            columns=[
                DataColumnSchema(name="revenue", type="NUMERIC", nullable=False),
            ],
        ) for prefix in ("public", "private")
    ]
    source = DataSource(
        id="source", user_id="local-admin", name="Finance",
        engine="postgresql", connection_secret="not-a-secret",
        status="connected", enabled=True,
        schema_json=encode_schema(tables),
        authorized_objects_json=json.dumps(["public.finance"]),
    )
    class ForbiddenVault:
        def decrypt(self, *_args, **_kwargs):
            pytest.fail("Credentials must not be decrypted for an unpinned table")
    monkeypatch.setattr(executor_module, "credential_vault", ForbiddenVault())
    with pytest.raises(StructuredExecutionError, match="unauthorized"):
        await execute_structured_query(
            source,
            "SELECT revenue FROM private.finance",
            scoped_tables=frozenset({"public.finance"}),
        )


def test_lexical_cte_resolution_rejects_unpinned_physical_tables():
    """A CTE alias in another scope must never hide a real unpinned table."""
    from app.services.sql_policy import SQLPolicyError, validate_and_rewrite_sql
    dangerous_queries = [
        "WITH finance AS (SELECT revenue FROM public.finance) "
        "SELECT x.revenue FROM private.finance AS x "
        "JOIN finance ON finance.revenue = x.revenue",
        "SELECT revenue FROM private.finance WHERE EXISTS ("
        "WITH finance AS (SELECT revenue FROM public.finance) "
        "SELECT revenue FROM finance)",
        "WITH payroll AS (SELECT revenue FROM payroll) "
        "SELECT revenue FROM public.finance",
    ]
    for query in dangerous_queries:
        for exact in (False, True):
            with pytest.raises(SQLPolicyError, match="unauthorized"):
                validate_and_rewrite_sql(
                    query, dialect="postgres",
                    allowed_tables={"public.finance"},
                    allowed_columns={"public.finance": {"revenue"}},
                    max_rows=20,
                    require_exact_table_match=exact,
                )


def test_valid_cte_can_reference_exact_pinned_table():
    from app.services.sql_policy import validate_and_rewrite_sql
    safe = "WITH approved AS (SELECT revenue FROM public.finance) SELECT revenue FROM approved"
    result = validate_and_rewrite_sql(
        safe,
        dialect="postgres",
        allowed_tables={"public.finance"},
        allowed_columns={"public.finance": {"revenue"}},
        max_rows=20,
        require_exact_table_match=True,
    )
    assert result.tables == ("public.finance",)


def test_recursive_cte_is_refused_in_pinned_mode():
    from app.services.sql_policy import SQLPolicyError, validate_and_rewrite_sql
    query = (
        "WITH RECURSIVE counter(n) AS "
        "(SELECT 1 UNION ALL SELECT n + 1 FROM counter WHERE n < 10) "
        "SELECT revenue FROM public.finance"
    )
    with pytest.raises(SQLPolicyError, match="Recursive CTE"):
        validate_and_rewrite_sql(
            query,
            dialect="postgres", allowed_tables={"public.finance"},
            allowed_columns={"public.finance": {"revenue"}},
            max_rows=20, require_exact_table_match=True,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("equivalent,source_type", [
    ([{"source_id": "unscoped-doc"}], "document"),
    ([{"source_id": ""}], "document"),
    ([{"unexpected": "unscoped-doc"}], "document"),
    (["unscoped-doc"], "document"),
    (None, "document"),
    ([], "structured_query"),
])
async def test_knowledge_tool_rejects_untrusted_provenance(
    session, monkeypatch, equivalent, source_type
):
    """Evidence cannot launder a foreign source via malformed dedup metadata."""
    await seed(session)
    async def malicious_retrieve(*_args, **_kwargs):
        return [Evidence(
            source_type=source_type, source_id="allowed-doc",
            title="untrusted", passage="external",
            provenance={"equivalent_sources": equivalent},
        )]
    monkeypatch.setattr(tools_module.knowledge_engine, "retrieve", malicious_retrieve)
    with pytest.raises(ToolPermissionError):
        await KnowledgeSearchTool().execute(
            ToolContext(
                user_id="local-admin", db=session, report_scope=scope(),
                permissions=frozenset({"knowledge.read"}),
            ),
            {"query": "policy"},
        )
