"""OPS1 OpenJM Control Center acceptance: capability separation and safe projections.

Covers the reviewed boundaries for the operator plane:

* a tenant owner/admin holds zero platform authority and is refused by every OPS1
  route;
* a metadata observer may read safe metadata and may not mutate;
* no OPS1 response exposes customer content, a message/document body, a model
  prompt or answer, a connector credential or a secret;
* the audit explorer projects the free-form audit metadata to a bounded
  allow-list;
* the diagnostics surface never returns plan/executed SQL or free-form trace
  metadata;
* a customer tenant identity cannot enumerate the platform fleet.
"""

from __future__ import annotations

from app.core.platform import PlatformCapability
from app.core.tenancy import LEGACY_PRINCIPAL_ID
from app.main import app
from app.models import AuditRecord, ExecutionTrace
from app.services import access_governance as governance

OPS1_ROUTES = (
    "/api/platform/operations/overview",
    "/api/platform/operations/health",
    "/api/platform/operations/releases",
    "/api/platform/operations/tenants",
    "/api/platform/operations/usage",
    "/api/platform/audit",
    "/api/platform/audit/summary",
    "/api/platform/security",
    "/api/platform/diagnostics",
)

# Keys that must never appear anywhere in an operator payload. Content and
# credential vocabulary, checked case-insensitively against the raw JSON text.
FORBIDDEN_SUBSTRINGS = (
    "planned_sql",
    "executed_sql",
    "input_hash",
    "prompt",
    "document_text",
    "passage",
    "api_key",
    "client_secret",
    "bearer ",
    "password",
)


async def _grant_local_operator(file_db, *capabilities) -> None:
    """Give the dev-authenticated principal real platform grants."""
    async with file_db() as db:
        await governance.grant_platform_operator(
            db, principal_id=LEGACY_PRINCIPAL_ID, capabilities=list(capabilities)
        )
        await db.commit()


# ---------------------------------------------------------------------------
# 1-2. Tenant roles hold no platform authority
# ---------------------------------------------------------------------------


async def test_tenant_owner_refused_by_every_ops1_route(client, file_db):
    # Dev auth resolves the legacy principal as a tenant owner with no platform grant.
    for path in OPS1_ROUTES:
        response = await client.get(path)
        assert response.status_code == 403, (path, response.status_code, response.text)


async def test_tenant_identity_cannot_enumerate_fleet(client, file_db):
    response = await client.get("/api/platform/operations/tenants")
    assert response.status_code == 403
    # A tenant identity must not learn that the fleet route exists in a usable form.
    assert "tenants" not in response.text or "platform capability" in response.text


# ---------------------------------------------------------------------------
# 3-4. Metadata observer reads, and cannot mutate
# ---------------------------------------------------------------------------


async def test_metadata_observer_can_read_safe_metadata(client, file_db):
    await _grant_local_operator(file_db, PlatformCapability.METADATA_READ)
    for path in OPS1_ROUTES:
        response = await client.get(path)
        assert response.status_code == 200, (path, response.status_code, response.text)


async def test_metadata_observer_cannot_mutate(client, file_db):
    await _grant_local_operator(file_db, PlatformCapability.METADATA_READ)

    created = await client.post(
        "/api/platform/tenants", json={"slug": "observer-probe", "name": "Observer Probe"}
    )
    assert created.status_code == 403, created.text

    # And no OPS1 route is a mutation.
    mutation_paths = [
        getattr(route, "path", "")
        for route in app.routes
        if getattr(route, "path", "").startswith("/api/platform/operations/")
        and any(
            getattr(route, "methods", set()) & {"POST", "PUT", "PATCH", "DELETE"}
        )
    ]
    assert mutation_paths == []


# ---------------------------------------------------------------------------
# 5-6. No content, no secrets, in any operator payload
# ---------------------------------------------------------------------------


def _assert_no_forbidden(payload_text: str, path: str) -> None:
    lowered = payload_text.lower()
    for token in FORBIDDEN_SUBSTRINGS:
        if token in ("prompt", "passage"):
            # Word-level check: "prompt_style" is not a leak, a bare field is.
            assert f'"{token}"' not in lowered, (path, token)
        else:
            assert token not in lowered, (path, token)


async def test_operator_payloads_expose_no_content_or_secrets(client, file_db):
    await _grant_local_operator(file_db, PlatformCapability.METADATA_READ)
    for path in OPS1_ROUTES:
        response = await client.get(path)
        assert response.status_code == 200
        _assert_no_forbidden(response.text, path)


async def test_ops1_routes_are_metadata_only():
    ops1_paths = {
        getattr(route, "path", "")
        for route in app.routes
        if getattr(route, "path", "").startswith("/api/platform/")
    }
    # The new plane adds only operations/audit/security/diagnostics surfaces.
    new = {p for p in ops1_paths if p not in ()}
    assert not any(
        token in path
        for path in new
        for token in ("documents", "sources", "reports", "chat", "evidence", "content")
    )


# ---------------------------------------------------------------------------
# 10. Audit explorer projects the free-form metadata
# ---------------------------------------------------------------------------


async def test_audit_explorer_projects_metadata_and_never_dumps_raw_payload(client, file_db):
    await _grant_local_operator(file_db, PlatformCapability.METADATA_READ)

    async with file_db() as db:
        db.add(
            AuditRecord(
                tenant_id="tnt-live",
                principal_id="prn-1",
                role="admin",
                action="governance.document.policy",
                decision="allow",
                resource_type="document",
                resource_id="doc-1",
                reason="policy updated",
                # An assumed-unsafe payload: a known-safe key plus two that must
                # never reach an operator screen.
                metadata_json='{"status":"updated","document_text":"CUSTOMER SECRET BODY",'
                '"credential":"should-not-appear"}',
            )
        )
        await db.commit()

    response = await client.get("/api/platform/audit", params={"action": "governance.document.policy"})
    assert response.status_code == 200
    body = response.text
    assert "CUSTOMER SECRET BODY" not in body
    assert "should-not-appear" not in body
    assert "document_text" not in body

    events = response.json()["events"]
    assert events, "expected the seeded audit event"
    event = events[0]
    assert event["metadata"] == {"status": "updated"}, event["metadata"]
    assert event["action"] == "governance.document.policy"
    assert event["family"] == "governance"


async def test_audit_page_size_is_capped_server_side(client, file_db):
    await _grant_local_operator(file_db, PlatformCapability.METADATA_READ)
    response = await client.get("/api/platform/audit", params={"limit": 100000})
    assert response.status_code == 200
    from app.services import operator_console

    assert response.json()["limit"] == operator_console.MAX_AUDIT_PAGE


# ---------------------------------------------------------------------------
# 11. Diagnostics never returns SQL or free-form trace metadata
# ---------------------------------------------------------------------------


async def test_diagnostics_never_returns_sql_or_trace_metadata(client, file_db):
    await _grant_local_operator(file_db, PlatformCapability.METADATA_READ)

    async with file_db() as db:
        db.add(
            ExecutionTrace(
                tenant_id="tnt-live",
                user_id="prn-1",
                tool_name="structured_query",
                operation_class="read",
                risk_level="low",
                input_hash="hash-of-the-request",
                status="failed",
                error_class="SqlPolicyError",
                model_name="glm-5.3-flash",
                planned_sql="SELECT ssn FROM customers WHERE name = 'Ada Lovelace'",
                executed_sql="SELECT ssn FROM customers WHERE name = 'Ada Lovelace'",
                metadata_json='{"note":"CUSTOMER VALUE"}',
            )
        )
        await db.commit()

    response = await client.get("/api/platform/diagnostics")
    assert response.status_code == 200
    body = response.text
    assert "Ada Lovelace" not in body
    assert "select ssn" not in body.lower()
    assert "CUSTOMER VALUE" not in body
    assert "planned_sql" not in body
    assert "executed_sql" not in body

    payload = response.json()
    assert payload["traces"], "expected the seeded trace"
    trace = payload["traces"][0]
    assert trace["tool_name"] == "structured_query"
    assert trace["error_class"] == "SqlPolicyError"
    assert "planned_sql" not in trace and "metadata_json" not in trace

    # The operational-log approach is reported, not silently persisted.
    assert payload["operational_logs"]["aggregated_into_metadata_db"] is False


# ---------------------------------------------------------------------------
# Content-support capability alone grants nothing here
# ---------------------------------------------------------------------------


async def test_content_support_capability_adds_no_console_content(client, file_db):
    await _grant_local_operator(
        file_db, PlatformCapability.METADATA_READ, PlatformCapability.CONTENT_SUPPORT
    )
    for path in OPS1_ROUTES:
        response = await client.get(path)
        assert response.status_code == 200
        _assert_no_forbidden(response.text, path)
