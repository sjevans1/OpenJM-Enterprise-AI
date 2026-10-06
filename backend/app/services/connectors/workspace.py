"""OpenJM Workspace connector.

This is the only place in the platform that speaks to the Workspace HTTP API.
It implements the :class:`~app.services.connectors.base.Connector` contract
against the published Workspace endpoints and declares its operations in the
connector registry so nothing else can widen what it is allowed to do.

Provider facts this module relies on:

* Every path is absolute under the configured ``base_url``.
* Auth is an opaque bearer token supplied by
  :class:`~app.services.connectors.base.ConnectorHTTP`. This module never
  assembles an Authorization header itself and never logs the token. Browser
  cookies are never used.
* ``GET /api/v1/events/cursor`` returns an opaque, tenant-bound and
  principal-bound cursor. It is passed through verbatim and never parsed.
* ``GET /api/v1/events/reconcile`` is eventually consistent and keyset-paged,
  so an id that is missing from one scan is not evidence of deletion.
* Authorization for an end user is answered only by
  ``GET /api/v1/resources/{id}/permissions/check`` with the mapped user's
  stable UUID. A service-credential read is never sufficient.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime
from typing import Any
from urllib.parse import quote

from app.core.connectors import (
    AuthorizationBehavior,
    ConnectorCapability,
    ConnectorTypeSpec,
    DeclaredOperation,
    OperationClass,
    connector_registry,
)
from app.core.permissions import Permission
from app.services.connectors.base import (
    Connector,
    ConnectorAuthUnavailable,
    ConnectorContext,
    ConnectorError,
    ConnectorHTTP,
    ExternalEvent,
    ExternalResourceRef,
    ResourceContent,
    redact,
)
from app.services.connectors.service import register_implementation

WORKSPACE_CONNECTOR_TYPE = "workspace"
WORKSPACE_CONNECTOR_VERSION = "1.0.0"

_RESOURCES_PATH = "/api/v1/resources"
_EVENTS_CURSOR_PATH = "/api/v1/events/cursor"
_EVENTS_RECONCILE_PATH = "/api/v1/events/reconcile"

_PAGE_CONTENT = "text/markdown"


def _resource_path(external_id: str, suffix: str = "") -> str:
    return f"{_RESOURCES_PATH}/{quote(str(external_id), safe='')}{suffix}"


def _page_content_path(external_id: str) -> str:
    return f"/api/v1/pages/{quote(str(external_id), safe='')}/content"


def _parse_iso(value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp, returning ``None`` when it is unusable."""
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _revision_of(resource: dict) -> str | None:
    updated_at = resource.get("updated_at")
    return str(updated_at) if updated_at else None


def workspace_type_spec() -> ConnectorTypeSpec:
    """The declared contract of the Workspace connector type."""
    return ConnectorTypeSpec(
        type_id=WORKSPACE_CONNECTOR_TYPE,
        version=WORKSPACE_CONNECTOR_VERSION,
        display_name="OpenJM Workspace",
        description=(
            "Read-only access to OpenJM Workspace documents, change events and "
            "per-user resource authorization."
        ),
        capabilities=frozenset(
            {
                ConnectorCapability.DOCUMENTS,
                ConnectorCapability.EVENTS,
                ConnectorCapability.PERMISSIONS,
            }
        ),
        operations=(
            DeclaredOperation(
                name="workspace.list_resources",
                capability=ConnectorCapability.DOCUMENTS,
                operation_class=OperationClass.READ,
                description="Enumerate the Workspace resource tree within a caller bound.",
                required_permission=Permission.CONNECTOR_READ.value,
            ),
            DeclaredOperation(
                name="workspace.fetch_resource",
                capability=ConnectorCapability.DOCUMENTS,
                operation_class=OperationClass.READ,
                description="Fetch one Workspace resource's content as evidence.",
                required_permission=Permission.CONNECTOR_READ.value,
            ),
            DeclaredOperation(
                name="workspace.check_access",
                capability=ConnectorCapability.PERMISSIONS,
                operation_class=OperationClass.READ,
                description="Ask Workspace whether a mapped user may see a resource.",
                required_permission=Permission.CONNECTOR_READ.value,
            ),
            DeclaredOperation(
                name="workspace.update_page",
                capability=ConnectorCapability.DOCUMENTS,
                operation_class=OperationClass.WRITE,
                description="Update a Workspace page. Approval-gated.",
                required_permission=Permission.CONNECTOR_WRITE.value,
                requires_approval=True,
            ),
        ),
        authorization_behavior=AuthorizationBehavior.PROVIDER_CURRENT_STATE,
        requires_user_mapping=True,
        credential_kind="bearer_token",
        credential_fields=("token",),
        credential_rotation="replace",
        event_support=True,
        reconciliation_support=True,
        incremental_support=True,
        supports_test_connection=True,
        timeout_seconds=30,
        max_retries=3,
        initial_sync_limit=500,
    )


class WorkspaceConnector(Connector):
    """Workspace HTTP API implementation of the :class:`Connector` contract."""

    type_id = WORKSPACE_CONNECTOR_TYPE
    version = WORKSPACE_CONNECTOR_VERSION

    # -- lifecycle ---------------------------------------------------------

    async def test_connection(self, ctx: ConnectorContext) -> dict:
        """Prove the configuration and bearer credential work.

        A failure is reported as ``ok=False`` with a redacted detail rather
        than raised, so an operator gets a diagnosis instead of a stack trace.
        """
        try:
            async with ConnectorHTTP(ctx) as http:
                payload = await http.get_json(_RESOURCES_PATH, params={"limit": 1})
        except ConnectorError as exc:
            return {"ok": False, "detail": redact(exc.detail or str(exc))}
        except Exception as exc:  # noqa: BLE001 - a health check must not raise
            return {"ok": False, "detail": redact(str(exc))}
        if payload is None:
            return {"ok": False, "detail": "workspace resource listing returned no result"}
        return {"ok": True, "detail": "workspace resource listing responded"}

    # -- enumeration -------------------------------------------------------

    async def list_resources(
        self, ctx: ConnectorContext, *, limit: int, cursor: str | None = None
    ) -> tuple[list[ExternalResourceRef], str | None]:
        """Enumerate the resource tree breadth-first, bounded by ``limit``.

        The Workspace listing endpoint filters children by ``parent_id`` and
        paginates with ``limit`` and ``offset``. The tree is walked
        breadth-first so pages nested inside spaces (and any deeper nesting)
        are reachable rather than only the top level.

        The cursor is the stringified next offset into the deterministic
        breadth-first stream. Resuming re-walks the same stream from the
        beginning and skips already-returned positions, which keeps the cursor
        a plain offset without inventing an opaque format the provider never
        defined. Deleted resources are skipped but still consume a stream
        position so resumption stays aligned.
        """
        if limit <= 0:
            return [], None

        start = 0
        if cursor not in (None, ""):
            try:
                start = int(cursor)
            except (TypeError, ValueError) as exc:
                raise ConnectorError(
                    "workspace list cursor is not a valid offset", code="invalid_cursor"
                ) from exc

        page_size = max(1, limit)
        collected: list[ExternalResourceRef] = []
        queue: deque[str | None] = deque([None])
        seen_containers: set[str] = set()
        emitted = 0

        async with ConnectorHTTP(ctx) as http:
            while queue and len(collected) < limit:
                parent = queue.popleft()
                offset = 0
                while len(collected) < limit:
                    params: dict[str, Any] = {"limit": page_size, "offset": offset}
                    if parent is not None:
                        params["parent_id"] = parent
                    page = await http.get_json(_RESOURCES_PATH, params=params)
                    if not page:
                        break
                    for item in page:
                        if not isinstance(item, dict):
                            continue
                        position = emitted
                        emitted += 1
                        present = (
                            item.get("deleted_at") is None and item.get("id") is not None
                        )
                        if present:
                            # Every present resource may itself have children, and
                            # an already-emitted parent still needs its children
                            # visited on a resumed walk, so the frontier is built
                            # before the resume skip.
                            container_id = str(item["id"])
                            if container_id not in seen_containers:
                                seen_containers.add(container_id)
                                queue.append(container_id)
                        if not present or position < start:
                            continue
                        collected.append(
                            ExternalResourceRef(
                                external_id=str(item["id"]),
                                resource_type=str(item.get("kind") or ""),
                                external_revision=_revision_of(item),
                                external_parent_id=(
                                    str(item["parent_id"])
                                    if item.get("parent_id")
                                    else None
                                ),
                                title=item.get("title"),
                                updated_at=_parse_iso(item.get("updated_at")),
                                deleted=False,
                            )
                        )
                        if len(collected) >= limit:
                            break
                    offset += len(page)
                    if len(page) < page_size:
                        break

        next_cursor = str(emitted) if len(collected) >= limit else None
        return collected, next_cursor

    async def fetch_resource(
        self, ctx: ConnectorContext, external_id: str
    ) -> ResourceContent | None:
        """Fetch one resource as ingestible evidence, or ``None`` on a 404."""
        async with ConnectorHTTP(ctx) as http:
            resource = await http.get_json(_resource_path(external_id))
            if resource is None:
                return None

            kind = str(resource.get("kind") or "")
            if kind == "page":
                content = await http.get_json(_page_content_path(external_id))
                if content is None:
                    return None
                return ResourceContent(
                    external_id=str(content.get("id") or external_id),
                    revision=(
                        str(content["revision"]) if content.get("revision") is not None else None
                    ),
                    title=str(content.get("title") or resource.get("title") or ""),
                    text=str(content.get("plain_text") or ""),
                    content_type=_PAGE_CONTENT,
                    metadata={
                        "id": str(content.get("id") or external_id),
                        "tenant_id": content.get("tenant_id"),
                        "workspace_id": content.get("workspace_id"),
                        "space_id": content.get("space_id"),
                        "parent_id": content.get("parent_id") or resource.get("parent_id"),
                        "updated_at": content.get("updated_at") or resource.get("updated_at"),
                        "epoch": content.get("epoch"),
                        "kind": kind,
                    },
                )

            return self._summary_content(external_id, resource)

    @staticmethod
    def _summary_content(external_id: str, resource: dict) -> ResourceContent:
        """Build a short metadata summary so non-page kinds stay ingestible."""
        title = str(resource.get("title") or "")
        kind = str(resource.get("kind") or "")
        path = resource.get("path") or []
        breadcrumb_parts = [
            str(entry.get("title"))
            for entry in path
            if isinstance(entry, dict) and entry.get("title")
        ]
        breadcrumb = " > ".join(breadcrumb_parts)
        lines = [f"{title} ({kind})" if title else kind]
        if breadcrumb:
            lines.append(f"Path: {breadcrumb}")
        return ResourceContent(
            external_id=str(resource.get("id") or external_id),
            revision=None,
            title=title,
            text="\n".join(line for line in lines if line),
            content_type=_PAGE_CONTENT,
            metadata={
                "id": str(resource.get("id") or external_id),
                "tenant_id": resource.get("tenant_id"),
                "parent_id": resource.get("parent_id"),
                "updated_at": resource.get("updated_at"),
                "kind": kind,
                "breadcrumb": breadcrumb,
            },
        )

    # -- events and reconciliation ----------------------------------------

    async def enumerate_events(
        self, ctx: ConnectorContext, *, limit: int, cursor: str | None = None
    ) -> tuple[list[ExternalEvent], str | None]:
        """Read the next page of the Workspace change feed.

        ``cursor`` is opaque, tenant-bound and principal-bound. It is forwarded
        verbatim and the returned ``next_cursor`` is returned verbatim.
        """
        params: dict[str, Any] = {"limit": limit}
        if cursor not in (None, ""):
            params["cursor"] = cursor
        async with ConnectorHTTP(ctx) as http:
            payload = await http.get_json(_EVENTS_CURSOR_PATH, params=params)
        if not isinstance(payload, dict):
            return [], None
        raw_events = payload.get("events") or []
        events = [
            ExternalEvent(
                event_id=str(event.get("id")),
                event_type=str(event.get("type") or ""),
                external_id=str(event.get("resource_id")),
                version=(
                    str(event["version"]) if event.get("version") is not None else None
                ),
                created_at=_parse_iso(event.get("created_at")),
            )
            for event in raw_events
            if isinstance(event, dict)
        ]
        return events, payload.get("next_cursor")

    async def reconcile_scan(
        self, ctx: ConnectorContext, *, limit: int, cursor: str | None = None
    ) -> tuple[list[ExternalResourceRef], str | None]:
        """Walk current Workspace state to repair drift.

        This endpoint is eventually consistent and keyset-paged, so a resource
        missing from one scan is never interpreted as a deletion. Entries carry
        references only, never content, and never grant access.
        """
        params: dict[str, Any] = {"limit": limit}
        if cursor not in (None, ""):
            params["cursor"] = cursor
        async with ConnectorHTTP(ctx) as http:
            payload = await http.get_json(_EVENTS_RECONCILE_PATH, params=params)
        if not isinstance(payload, dict):
            return [], None
        raw_resources = payload.get("resources") or []
        refs = [
            ExternalResourceRef(
                external_id=str(entry.get("id")),
                resource_type=str(entry.get("kind") or ""),
                external_revision=_revision_of(entry),
                external_parent_id=(
                    str(entry["parent_id"]) if entry.get("parent_id") else None
                ),
                deleted=False,
            )
            for entry in raw_resources
            if isinstance(entry, dict) and entry.get("id") is not None
        ]
        # ``next_cursor`` is a required string in the provider contract and
        # ``has_more`` is the completion signal. Reporting a cursor on the final
        # page would tell the engine the sweep never finishes, and because the
        # deletion pass only runs after a complete sweep, deletions would never
        # be applied. Completion is therefore expressed as a null cursor.
        has_more = bool(payload.get("has_more"))
        next_cursor = payload.get("next_cursor")
        if not has_more:
            next_cursor = None
        return refs, next_cursor

    # -- current-user authorization ---------------------------------------

    async def check_user_access(
        self, ctx: ConnectorContext, *, external_user_id: str, external_id: str
    ) -> bool:
        """Ask Workspace whether the mapped user may see the resource.

        Returns ``True`` only when the provider answers 200 with ``allowed``
        exactly ``True``. A 404 is an explicit deny. Any other outcome raises
        :class:`ConnectorAuthUnavailable` so the caller fails closed. The
        service credential can read a resource without this ever being true,
        because the answer comes only from the per-user permission check.
        """
        if not external_user_id:
            raise ConnectorAuthUnavailable(
                "workspace user mapping is missing", code="user_mapping_missing"
            )
        try:
            async with ConnectorHTTP(ctx) as http:
                payload = await http.get_json(
                    _resource_path(external_id, "/permissions/check"),
                    params={"user_id": external_user_id},
                )
        except ConnectorAuthUnavailable:
            raise
        except ConnectorError as exc:
            raise ConnectorAuthUnavailable(
                "workspace authorization could not be established",
                code="authorization_unavailable",
                detail=exc.detail or str(exc),
            ) from exc
        if payload is None:
            # 404: the resource is missing or inaccessible to this user.
            return False
        if not isinstance(payload, dict):
            raise ConnectorAuthUnavailable(
                "workspace permission check returned an unexpected payload",
                code="authorization_unavailable",
            )
        return payload.get("allowed") is True


workspace_connector = WorkspaceConnector()


def register_workspace_connector() -> None:
    """Register the Workspace type spec and implementation.

    Safe to call more than once: a second call is a no-op for the spec and an
    idempotent re-registration for the implementation.
    """
    if not connector_registry.has(WORKSPACE_CONNECTOR_TYPE, WORKSPACE_CONNECTOR_VERSION):
        connector_registry.register(workspace_type_spec())
    register_implementation(workspace_connector)


__all__ = [
    "WORKSPACE_CONNECTOR_TYPE",
    "WORKSPACE_CONNECTOR_VERSION",
    "WorkspaceConnector",
    "register_workspace_connector",
    "workspace_connector",
    "workspace_type_spec",
]
