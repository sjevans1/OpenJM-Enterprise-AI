"""VS7 Workspace connector unit tests.

Every test drives the real :class:`WorkspaceConnector` against a stub HTTP
layer, so nothing here touches the network. The stub replaces the module-level
``ConnectorHTTP`` symbol with a factory whose sessions record every call and
answer from a test-supplied responder.
"""

from __future__ import annotations

import pytest

from app.core.connectors import (
    AuthorizationBehavior,
    ConnectorCapability,
    ConnectorRegistryError,
    OperationClass,
    connector_registry,
)
from app.core.permissions import Permission
from app.services.connectors import workspace
from app.services.connectors.base import (
    ConnectorAuthUnavailable,
    ConnectorContext,
    ConnectorError,
)

WORKSPACE_KEY = "workspace@1.0.0"


# ---------------------------------------------------------------------------
# Stub HTTP layer
# ---------------------------------------------------------------------------


class _FakeSession:
    def __init__(self, transport, ctx):
        self._transport = transport
        self._ctx = ctx

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get_json(self, path, *, params=None):
        call = (path, dict(params or {}))
        self._transport.calls.append(call)
        return self._transport.responder(path, params or {})


class _FakeTransport:
    """Records outbound calls and answers from a per-test responder."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.responder = lambda path, params: None

    def factory(self, ctx):
        return _FakeSession(self, ctx)

    def paths(self):
        return [path for path, _ in self.calls]


@pytest.fixture
def transport(monkeypatch):
    fake = _FakeTransport()
    monkeypatch.setattr(workspace, "ConnectorHTTP", fake.factory)
    return fake


@pytest.fixture
def registered():
    workspace.register_workspace_connector()
    try:
        yield
    finally:
        connector_registry.unregister(WORKSPACE_KEY)


def make_ctx() -> ConnectorContext:
    return ConnectorContext(
        tenant_id="tnt-1",
        connector_instance_id="ci-1",
        connector_type="workspace",
        credential={"token": "opaque-workspace-token"},
        base_url="https://workspace.test",
        timeout_seconds=30,
    )


SESSION_TOKEN = "opaque-workspace-token-value-1234567890"


# ---------------------------------------------------------------------------
# Registry spec
# ---------------------------------------------------------------------------


def test_spec_registration_is_idempotent_and_validates(registered):
    # A second registration must not raise.
    workspace.register_workspace_connector()

    spec = connector_registry.get("workspace", "1.0.0")
    assert spec.type_id == "workspace"
    assert spec.version == "1.0.0"
    assert spec.display_name == "OpenJM Workspace"
    assert spec.capabilities == frozenset(
        {
            ConnectorCapability.DOCUMENTS,
            ConnectorCapability.EVENTS,
            ConnectorCapability.PERMISSIONS,
        }
    )
    assert spec.authorization_behavior is AuthorizationBehavior.PROVIDER_CURRENT_STATE
    assert spec.requires_user_mapping is True
    assert spec.credential_kind == "bearer_token"
    assert spec.credential_fields == ("token",)
    assert spec.credential_rotation == "replace"
    assert spec.event_support is True
    assert spec.reconciliation_support is True
    assert spec.incremental_support is True
    assert spec.supports_test_connection is True
    assert spec.timeout_seconds == 30
    assert spec.max_retries == 3
    assert spec.initial_sync_limit == 500

    assert len(spec.operations) == 3
    names = {op.name for op in spec.operations}

    list_op = spec.operation("workspace.list_resources")
    assert list_op.capability is ConnectorCapability.DOCUMENTS
    assert list_op.operation_class is OperationClass.READ
    assert list_op.required_permission == Permission.CONNECTOR_READ.value

    fetch_op = spec.operation("workspace.fetch_resource")
    assert fetch_op.capability is ConnectorCapability.DOCUMENTS
    assert fetch_op.operation_class is OperationClass.READ
    assert fetch_op.required_permission == Permission.CONNECTOR_READ.value

    check_op = spec.operation("workspace.check_access")
    assert check_op.capability is ConnectorCapability.PERMISSIONS
    assert check_op.operation_class is OperationClass.READ
    assert check_op.required_permission == Permission.CONNECTOR_READ.value

    # Workspace advertises no write operation. Its documented write route
    # authorizes the caller's own identity, and the integration credential is a
    # synthetic service principal, so a write through it could not be authorized
    # by the mapped end user. Advertising an operation that cannot honour the
    # connector's own authorization model would be worse than not advertising it.
    with pytest.raises(ConnectorRegistryError) as exc:
        spec.operation("workspace.update_page")
    assert exc.value.code == "unregistered_operation"
    assert all(op.operation_class is OperationClass.READ for op in spec.operations), (
        "the registry must not advertise a Workspace write"
    )

    assert names == {
        "workspace.list_resources",
        "workspace.fetch_resource",
        "workspace.check_access",
    }


def test_implementation_is_registered(registered):
    from app.services.connectors.service import get_implementation

    impl = get_implementation("workspace", "1.0.0")
    assert isinstance(impl, workspace.WorkspaceConnector)


# ---------------------------------------------------------------------------
# list_resources
# ---------------------------------------------------------------------------


def _tree_responder(transport):
    tree = {
        None: [
            {
                "id": "w1",
                "kind": "workspace",
                "parent_id": None,
                "title": "Acme",
                "updated_at": "2024-01-01T00:00:00Z",
                "deleted_at": None,
            }
        ],
        "w1": [
            {
                "id": "s1",
                "kind": "space",
                "parent_id": "w1",
                "title": "Engineering",
                "updated_at": "2024-01-02T00:00:00Z",
                "deleted_at": None,
            },
            {
                "id": "s2",
                "kind": "space",
                "parent_id": "w1",
                "title": "Finance",
                "updated_at": "2024-01-03T00:00:00Z",
                "deleted_at": None,
            },
        ],
        "s1": [
            {
                "id": "p1",
                "kind": "page",
                "parent_id": "s1",
                "title": "Runbook",
                "updated_at": "2024-01-04T00:00:00Z",
                "deleted_at": None,
            },
            {
                "id": "p2",
                "kind": "page",
                "parent_id": "s1",
                "title": "Onboarding",
                "updated_at": "2024-01-05T00:00:00Z",
                "deleted_at": None,
            },
            {
                "id": "p-deleted",
                "kind": "page",
                "parent_id": "s1",
                "title": "Gone",
                "updated_at": "2024-01-06T00:00:00Z",
                "deleted_at": "2024-02-01T00:00:00Z",
            },
        ],
        "s2": [
            {
                "id": "p3",
                "kind": "page",
                "parent_id": "s2",
                "title": "Budget",
                "updated_at": "2024-01-07T00:00:00Z",
                "deleted_at": None,
            }
        ],
    }

    def responder(path, params):
        assert path == "/api/v1/resources"
        parent_id = params.get("parent_id")
        offset = int(params.get("offset", 0))
        page_size = int(params.get("limit", 0))
        rows = tree.get(parent_id, [])
        return rows[offset : offset + page_size]

    transport.responder = responder
    return tree


async def test_list_resources_respects_limit_and_paginates(transport):
    _tree_responder(transport)
    ctx = make_ctx()
    limit = 2

    collected_ids: list[str] = []
    cursor = None
    pages = 0
    while True:
        refs, cursor = await workspace.workspace_connector.list_resources(
            ctx, limit=limit, cursor=cursor
        )
        assert len(refs) <= limit
        collected_ids.extend(ref.external_id for ref in refs)
        pages += 1
        if cursor is None:
            break
        assert pages < 10, "pagination did not terminate"

    # Every page was bounded, and the walk reached pages nested in spaces.
    assert pages > 1
    assert set(collected_ids) == {"w1", "s1", "s2", "p1", "p2", "p3"}
    assert "p-deleted" not in collected_ids

    # Every listing request carried an explicit limit and offset.
    for path, params in transport.calls:
        assert path == "/api/v1/resources"
        assert "limit" in params and "offset" in params


async def test_list_resources_zero_limit_does_no_work(transport):
    called = {"n": 0}

    def responder(path, params):
        called["n"] += 1
        return []

    transport.responder = responder
    refs, cursor = await workspace.workspace_connector.list_resources(
        make_ctx(), limit=0
    )
    assert refs == []
    assert cursor is None
    assert called["n"] == 0


async def test_list_resources_cursor_is_stringified_offset(transport):
    _tree_responder(transport)
    refs, cursor = await workspace.workspace_connector.list_resources(
        make_ctx(), limit=2
    )
    assert [ref.external_id for ref in refs] == ["w1", "s1"]
    assert cursor == "2"


# ---------------------------------------------------------------------------
# fetch_resource
# ---------------------------------------------------------------------------


async def test_fetch_resource_maps_page_content(transport):
    resource = {
        "id": "p1",
        "tenant_id": "tnt-1",
        "parent_id": "s1",
        "kind": "page",
        "title": "Runbook",
        "updated_at": "2024-01-04T00:00:00Z",
        "deleted_at": None,
        "path": [
            {"id": "w1", "title": "Acme", "kind": "workspace"},
            {"id": "s1", "title": "Engineering", "kind": "space"},
        ],
        "favourite": True,
    }
    content = {
        "id": "p1",
        "tenant_id": "tnt-1",
        "workspace_id": "w1",
        "space_id": "s1",
        "parent_id": "s1",
        "title": "Runbook",
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-04T00:00:00Z",
        "blocks": [{"type": "paragraph"}],
        "plain_text": "Deploy the release.",
        "revision": 5,
        "epoch": 2,
    }

    def responder(path, params):
        if path == "/api/v1/resources/p1":
            return resource
        if path == "/api/v1/pages/p1/content":
            return content
        return None

    transport.responder = responder
    result = await workspace.workspace_connector.fetch_resource(make_ctx(), "p1")

    assert result is not None
    assert result.external_id == "p1"
    assert result.revision == "5"
    assert result.title == "Runbook"
    assert result.text == "Deploy the release."
    assert result.content_type == "text/markdown"
    assert result.metadata["tenant_id"] == "tnt-1"
    assert result.metadata["workspace_id"] == "w1"
    assert result.metadata["space_id"] == "s1"
    assert result.metadata["parent_id"] == "s1"
    assert result.metadata["updated_at"] == "2024-01-04T00:00:00Z"
    assert result.metadata["epoch"] == 2


async def test_fetch_resource_returns_none_on_404(transport):
    transport.responder = lambda path, params: None
    assert await workspace.workspace_connector.fetch_resource(make_ctx(), "missing") is None


async def test_fetch_resource_returns_none_when_page_content_is_404(transport):
    resource = {"id": "p1", "kind": "page", "title": "Runbook", "parent_id": "s1"}

    def responder(path, params):
        if path == "/api/v1/resources/p1":
            return resource
        return None

    transport.responder = responder
    assert await workspace.workspace_connector.fetch_resource(make_ctx(), "p1") is None


async def test_fetch_resource_summarizes_non_page_kinds(transport):
    resource = {
        "id": "r1",
        "kind": "record",
        "title": "Invoice 42",
        "parent_id": "db1",
        "updated_at": "2024-05-01T00:00:00Z",
        "path": [
            {"id": "db1", "title": "Invoices", "kind": "database"},
        ],
    }

    def responder(path, params):
        if path == "/api/v1/resources/r1":
            return resource
        return None

    transport.responder = responder
    result = await workspace.workspace_connector.fetch_resource(make_ctx(), "r1")

    assert result is not None
    assert result.external_id == "r1"
    assert result.title == "Invoice 42"
    assert "record" in result.text
    assert "Invoices" in result.text
    assert result.content_type == "text/markdown"


# ---------------------------------------------------------------------------
# check_user_access
# ---------------------------------------------------------------------------


def _permission_responder(transport, payload):
    def responder(path, params):
        if path.endswith("/permissions/check"):
            return payload
        return None

    transport.responder = responder


async def test_check_user_access_true_only_on_explicit_allowed_true(transport):
    _permission_responder(transport, {"allowed": True})
    assert (
        await workspace.workspace_connector.check_user_access(
            make_ctx(), external_user_id="user-uuid-1", external_id="p1"
        )
        is True
    )


async def test_check_user_access_false_on_explicit_deny(transport):
    _permission_responder(transport, {"allowed": False})
    assert (
        await workspace.workspace_connector.check_user_access(
            make_ctx(), external_user_id="user-uuid-1", external_id="p1"
        )
        is False
    )


async def test_check_user_access_false_on_truthy_but_not_true(transport):
    _permission_responder(transport, {"allowed": "yes"})
    assert (
        await workspace.workspace_connector.check_user_access(
            make_ctx(), external_user_id="user-uuid-1", external_id="p1"
        )
        is False
    )


async def test_check_user_access_false_on_404(transport):
    _permission_responder(transport, None)
    assert (
        await workspace.workspace_connector.check_user_access(
            make_ctx(), external_user_id="user-uuid-1", external_id="missing"
        )
        is False
    )


async def test_check_user_access_raises_on_timeout(transport):
    def responder(path, params):
        raise ConnectorAuthUnavailable("timed out", code="provider_timeout")

    transport.responder = responder
    with pytest.raises(ConnectorAuthUnavailable):
        await workspace.workspace_connector.check_user_access(
            make_ctx(), external_user_id="user-uuid-1", external_id="p1"
        )


async def test_check_user_access_raises_on_server_error(transport):
    def responder(path, params):
        raise ConnectorError(
            "external provider returned an error",
            code="provider_error",
            detail="HTTP 500: internal error",
        )

    transport.responder = responder
    with pytest.raises(ConnectorAuthUnavailable):
        await workspace.workspace_connector.check_user_access(
            make_ctx(), external_user_id="user-uuid-1", external_id="p1"
        )


async def test_check_user_access_sends_mapped_uuid(transport):
    _permission_responder(transport, {"allowed": True})
    await workspace.workspace_connector.check_user_access(
        make_ctx(), external_user_id="11111111-2222-3333-4444-555555555555", external_id="p1"
    )
    path, params = transport.calls[-1]
    assert path == "/api/v1/resources/p1/permissions/check"
    assert params["user_id"] == "11111111-2222-3333-4444-555555555555"


async def test_check_user_access_raises_without_user_mapping(transport):
    with pytest.raises(ConnectorAuthUnavailable):
        await workspace.workspace_connector.check_user_access(
            make_ctx(), external_user_id="", external_id="p1"
        )
    assert transport.calls == []


# ---------------------------------------------------------------------------
# enumerate_events
# ---------------------------------------------------------------------------


async def test_enumerate_events_maps_fields_and_preserves_cursor(transport):
    payload = {
        "events": [
            {
                "id": "e1",
                "tenant_id": "tnt-1",
                "type": "page.updated",
                "resource_id": "p1",
                "version": 3,
                "created_at": "2024-03-04T05:06:07Z",
            },
            {
                "id": "e2",
                "tenant_id": "tnt-1",
                "type": "page.deleted",
                "resource_id": "p2",
                "version": None,
                "created_at": "not-a-timestamp",
            },
        ],
        "next_cursor": "opaque-cursor-out",
        "has_more": True,
    }

    def responder(path, params):
        assert path == "/api/v1/events/cursor"
        return payload

    transport.responder = responder
    events, next_cursor = await workspace.workspace_connector.enumerate_events(
        make_ctx(), limit=50, cursor="opaque-cursor-in"
    )

    assert transport.calls[-1][1]["cursor"] == "opaque-cursor-in"
    assert next_cursor == "opaque-cursor-out"
    assert len(events) == 2
    assert events[0].event_id == "e1"
    assert events[0].event_type == "page.updated"
    assert events[0].external_id == "p1"
    assert events[0].version == "3"
    assert events[0].created_at is not None
    assert events[1].version is None
    assert events[1].created_at is None


async def test_enumerate_events_without_cursor_omits_param(transport):
    transport.responder = lambda path, params: {
        "events": [],
        "next_cursor": None,
        "has_more": False,
    }
    events, next_cursor = await workspace.workspace_connector.enumerate_events(
        make_ctx(), limit=10
    )
    assert events == []
    assert next_cursor is None
    assert "cursor" not in transport.calls[-1][1]


# ---------------------------------------------------------------------------
# reconcile_scan
# ---------------------------------------------------------------------------


async def test_reconcile_scan_never_treats_absent_id_as_deleted(transport):
    payload = {
        "resources": [
            {"id": "r1", "kind": "record", "parent_id": "db1", "updated_at": "2024-05-01T00:00:00Z"},
            {"id": "r2", "kind": "record", "parent_id": "db1", "updated_at": "2024-05-02T00:00:00Z"},
        ],
        "next_cursor": "scan-cursor-1",
        "has_more": True,
    }

    def responder(path, params):
        assert path == "/api/v1/events/reconcile"
        return payload

    transport.responder = responder
    refs, next_cursor = await workspace.workspace_connector.reconcile_scan(
        make_ctx(), limit=100
    )

    assert next_cursor == "scan-cursor-1"
    assert [ref.external_id for ref in refs] == ["r1", "r2"]
    # A reference for an id not present in this page must not be synthesized as
    # a deletion, and nothing in the scan is ever marked deleted.
    assert all(ref.deleted is False for ref in refs)
    assert all(ref.external_revision is not None for ref in refs)


async def test_reconcile_scan_blank_cursor_starts_a_new_scan(transport):
    seen = {}

    def responder(path, params):
        seen.update(params)
        return {"resources": [], "next_cursor": None, "has_more": False}

    transport.responder = responder
    refs, next_cursor = await workspace.workspace_connector.reconcile_scan(
        make_ctx(), limit=100, cursor=""
    )
    assert refs == []
    assert next_cursor is None
    assert "cursor" not in seen


# ---------------------------------------------------------------------------
# test_connection
# ---------------------------------------------------------------------------


async def test_test_connection_ok(transport):
    transport.responder = lambda path, params: []
    result = await workspace.workspace_connector.test_connection(make_ctx())
    assert result["ok"] is True
    assert result["detail"]
    assert transport.calls[-1][1]["limit"] == 1


async def test_test_connection_returns_ok_false_on_provider_error(transport):
    def responder(path, params):
        raise ConnectorError(
            "external provider returned an error",
            code="provider_error",
            detail=f"HTTP 500: Bearer {SESSION_TOKEN}",
        )

    transport.responder = responder
    result = await workspace.workspace_connector.test_connection(make_ctx())
    assert result["ok"] is False
    assert SESSION_TOKEN not in result["detail"]
    assert "REDACTED" in result["detail"]


async def test_test_connection_returns_ok_false_on_timeout(transport):
    def responder(path, params):
        raise ConnectorAuthUnavailable("timed out", code="provider_timeout")

    transport.responder = responder
    result = await workspace.workspace_connector.test_connection(make_ctx())
    assert result["ok"] is False
    assert result["detail"]


async def test_test_connection_returns_ok_false_on_404(transport):
    transport.responder = lambda path, params: None
    result = await workspace.workspace_connector.test_connection(make_ctx())
    assert result["ok"] is False


# ---------------------------------------------------------------------------
# Fail closed: the service credential alone never grants user access
# ---------------------------------------------------------------------------


async def test_service_credential_alone_never_grants_user_access(transport):
    """A credential that can read a resource must not imply user authorization."""

    def responder(path, params):
        if path == "/api/v1/resources" and params.get("parent_id") is None:
            # The service credential can list the resource.
            return [{"id": "p1", "kind": "page", "title": "Runbook", "deleted_at": None}]
        if path.endswith("/permissions/check"):
            # The provider answers only about the user and denies.
            return {"allowed": False}
        return None

    transport.responder = responder
    ctx = make_ctx()

    refs, _ = await workspace.workspace_connector.list_resources(ctx, limit=10)
    assert [ref.external_id for ref in refs] == ["p1"]

    allowed = await workspace.workspace_connector.check_user_access(
        ctx, external_user_id="user-uuid-1", external_id="p1"
    )
    assert allowed is False


async def test_permission_check_denies_when_payload_has_no_allowed_key(transport):
    # A body that merely reflects resource data must not be read as access.
    def responder(path, params):
        if path.endswith("/permissions/check"):
            return {"id": "p1", "kind": "page", "title": "Runbook"}
        return None

    transport.responder = responder
    allowed = await workspace.workspace_connector.check_user_access(
        make_ctx(), external_user_id="user-uuid-1", external_id="p1"
    )
    assert allowed is False


async def test_reconcile_scan_reports_completion_through_has_more(transport):
    """A final page must be reported as complete, not as one more cursor.

    Regression: the provider always returns a cursor string and signals
    completion with has_more. Returning that cursor verbatim told the sync
    engine the sweep never finished, so the deletion pass for absent resources
    never ran and a deleted external resource was never marked deleted.
    """
    transport.responder = lambda path, params: {
        "resources": [{"id": "rs-1", "kind": "page", "parent_id": None, "updated_at": None}],
        "next_cursor": "cursor-on-the-final-page",
        "has_more": False,
    }
    refs, next_cursor = await workspace.workspace_connector.reconcile_scan(
        make_ctx(), limit=10, cursor=None
    )
    assert [ref.external_id for ref in refs] == ["rs-1"]
    assert next_cursor is None, "a final page must signal completion with a null cursor"


async def test_reconcile_scan_keeps_the_cursor_while_more_pages_remain(transport):
    """While has_more is true the cursor must be preserved for resumption."""
    transport.responder = lambda path, params: {
        "resources": [{"id": "rs-1", "kind": "page", "parent_id": None, "updated_at": None}],
        "next_cursor": "page-2",
        "has_more": True,
    }
    refs, next_cursor = await workspace.workspace_connector.reconcile_scan(
        make_ctx(), limit=10, cursor=None
    )
    assert [ref.external_id for ref in refs] == ["rs-1"]
    assert next_cursor == "page-2"
