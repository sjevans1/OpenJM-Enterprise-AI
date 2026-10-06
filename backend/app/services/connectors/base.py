"""The connector service contract.

A connector implementation is the *only* place in OpenJM Enterprise AI where an
outbound network call to an external system is allowed to exist. There is no
generic URL fetch, no generic REST helper, no generic GraphQL client and no
generic webhook sender anywhere in the platform, and this module is the boundary
that makes that statement checkable: a provider module subclasses
:class:`Connector`, declares its operations in the connector registry, and
implements them with the shared HTTP helper below.

Contract rules every implementation must honour:

* **Fail closed.** ``check_user_access`` returning anything other than a proven
  ``True`` must deny. Raising is the correct response to a timeout, an outage,
  an ambiguous mapping or contradictory state. Never return ``True`` because the
  *service credential* can read something.
* **Prove the user, not the credential.** An operation declared with
  ``requires_user_authorization`` is not executable until
  :meth:`Connector.authorize_operation` has proven that the mapped end user may
  perform it on the target. The connector's own credential is never sufficient
  evidence, and the base class refuses rather than assuming.
* **No secrets in errors.** Use :func:`redact` before surfacing any provider
  text. A provider that echoes a token in an error message must not be able to
  write that token into OpenJM logs, audit rows or user-visible output.
* **Bounded work.** Every enumeration takes an explicit limit. No implementation
  may crawl an external system without a caller-supplied bound.
* **Idempotent reads.** Re-reading the same resource must be safe and must
  produce the same normalized result for the same external revision.
* **Evidence, not instruction.** External text is data. It is never interpreted
  as instructions to the runtime, and it never reaches a tool name or argument.
"""

from __future__ import annotations

import hashlib
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

from app.core.connectors import ConnectorTypeSpec, DeclaredOperation

# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

# Anything that looks like a bearer token, an API key, a password or a
# credential field value is replaced before provider text is stored or shown.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-+/=]{8,}"),
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password|authorization|credential)\b\s*[:=]\s*[^\s,;\"'}]{4,}"),
    re.compile(r"\b[A-Za-z0-9_\-]{24,}\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}\b"),  # JWT
    re.compile(r"\b[0-9a-fA-F]{64}\b"),  # 64-hex token hashes and raw tokens
)


def redact(text: Any, *, limit: int = 300) -> str:
    """Return provider text safe to store in logs, audit and API responses.

    This is deliberately conservative: it redacts by pattern rather than trying
    to be clever about what a secret is, because being wrong in the other
    direction writes a live credential into an append-only audit table.
    """
    if text is None:
        return ""
    value = str(text)
    for pattern in _SECRET_PATTERNS:
        if pattern.groups:
            value = pattern.sub(lambda m: f"{m.group(1)}=[REDACTED]", value)
        else:
            value = pattern.sub("[REDACTED]", value)
    value = value.replace("\n", " ").replace("\r", " ").strip()
    if len(value) > limit:
        value = value[:limit] + "..."
    return value


class ConnectorError(Exception):
    """A connector operation failed in a way the caller must treat as denial.

    ``code`` is a safe, stable category suitable for audit and for surfacing in
    an API response. ``detail`` is already redacted when it reaches here.
    """

    def __init__(self, message: str, *, code: str = "connector_error", detail: str | None = None):
        super().__init__(message)
        self.code = code
        self.detail = redact(detail) if detail else None


class ConnectorAuthUnavailable(ConnectorError):
    """Current authorization could not be established.

    Always maps to a deny. This is the timeout, outage, ambiguous-mapping and
    contradictory-state case, and it is distinguished from an explicit deny only
    so that operators can tell the two apart in audit.
    """

    def __init__(self, message: str, *, code: str = "authorization_unavailable", detail=None):
        super().__init__(message, code=code, detail=detail)


# ---------------------------------------------------------------------------
# Normalized transfer objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConnectorContext:
    """Everything a connector implementation needs for one operation.

    ``credential`` holds decrypted field values. It must never be logged,
    returned through the API, or placed in an audit metadata dict. It is passed
    by value rather than read from a global so an implementation cannot reach a
    credential it was not explicitly given.
    """

    tenant_id: str
    connector_instance_id: str
    connector_type: str
    config: dict[str, Any] = field(default_factory=dict)
    credential: dict[str, str] = field(default_factory=dict)
    base_url: str | None = None
    timeout_seconds: int = 30

    def require_credential(self, name: str) -> str:
        value = self.credential.get(name)
        if not value:
            raise ConnectorError(
                f"connector credential is missing '{name}'",
                code="credential_incomplete",
            )
        return value

    def config_value(self, name: str, default: Any = None) -> Any:
        return (self.config or {}).get(name, default)


@dataclass(frozen=True)
class ExternalResourceRef:
    """A pointer to one external resource, without its content."""

    external_id: str
    resource_type: str
    external_revision: str | None = None
    external_parent_id: str | None = None
    title: str | None = None
    updated_at: datetime | None = None
    deleted: bool = False

    def content_hash(self) -> str:
        """A stable hash over the fields that decide whether anything changed."""
        material = "|".join(
            [
                self.external_id,
                self.resource_type,
                self.external_revision or "",
                self.external_parent_id or "",
                self.title or "",
                "deleted" if self.deleted else "present",
            ]
        )
        return hashlib.sha256(material.encode()).hexdigest()


@dataclass(frozen=True)
class ResourceContent:
    """The fetched content of one external resource."""

    external_id: str
    revision: str | None
    title: str
    text: str
    content_type: str = "text/markdown"
    metadata: dict[str, Any] = field(default_factory=dict)

    def content_hash(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()


@dataclass(frozen=True)
class ExternalEvent:
    """One change event from a provider feed."""

    event_id: str
    event_type: str
    external_id: str
    version: str | None = None
    created_at: datetime | None = None


# ---------------------------------------------------------------------------
# The connector contract
# ---------------------------------------------------------------------------


class Connector(ABC):
    """Base class for a provider connector implementation.

    Subclasses declare ``type_id`` and ``version`` and must correspond to a
    :class:`~app.core.connectors.ConnectorTypeSpec` registered in the connector
    registry. Registration is explicit; nothing is auto-discovered.
    """

    type_id: str = ""
    version: str = ""

    @property
    def spec(self) -> ConnectorTypeSpec:
        from app.core.connectors import connector_registry

        return connector_registry.get(self.type_id, self.version)

    # -- lifecycle ---------------------------------------------------------

    @abstractmethod
    async def test_connection(self, ctx: ConnectorContext) -> dict:
        """Prove the configuration and credential work.

        Returns ``{"ok": bool, "detail": str}``. A failure returns
        ``ok=False`` with a redacted detail rather than raising, so an operator
        gets a diagnosis instead of a stack trace.
        """

    # -- enumeration -------------------------------------------------------

    @abstractmethod
    async def list_resources(
        self, ctx: ConnectorContext, *, limit: int, cursor: str | None = None
    ) -> tuple[list[ExternalResourceRef], str | None]:
        """Enumerate resources the connector's configured scope can reach.

        Returns the page and the next cursor, or ``None`` when exhausted. Must
        never exceed ``limit``.
        """

    @abstractmethod
    async def fetch_resource(
        self, ctx: ConnectorContext, external_id: str
    ) -> ResourceContent | None:
        """Fetch one resource's content, or ``None`` if it no longer exists."""

    # -- events and reconciliation ----------------------------------------

    async def enumerate_events(
        self, ctx: ConnectorContext, *, limit: int, cursor: str | None = None
    ) -> tuple[list[ExternalEvent], str | None]:
        """Read the next page of a change feed, if the provider supports one."""
        raise ConnectorError(
            f"connector {self.type_id} does not support events", code="events_unsupported"
        )

    async def reconcile_scan(
        self, ctx: ConnectorContext, *, limit: int, cursor: str | None = None
    ) -> tuple[list[ExternalResourceRef], str | None]:
        """Walk current provider state to repair drift.

        Return ``next_cursor=None`` when the sweep is complete, and only when it
        is complete. The engine uses that null to decide whether absence is
        meaningful: a resource missing from an incomplete sweep proves nothing,
        so deletions are applied only after a full pass. A provider whose
        pagination reports completion through a ``has_more`` flag rather than
        through a null cursor must translate it, or deletions will never be
        applied.

        Defaults to :meth:`list_resources`, which is correct for any provider
        whose enumeration is already current-state based.
        """
        return await self.list_resources(ctx, limit=limit, cursor=cursor)

    # -- current-user authorization ---------------------------------------

    @abstractmethod
    async def check_user_access(
        self, ctx: ConnectorContext, *, external_user_id: str, external_id: str
    ) -> bool:
        """Ask the provider whether ``external_user_id`` may see ``external_id``.

        This answer is about the *end user*, never about the service credential.
        Returning ``False`` is an explicit deny. Raising
        :class:`ConnectorAuthUnavailable` (or any error) is an inability to
        prove authorization, which the caller must also treat as a deny. It must
        never be possible to reach a ``True`` from "the credential can read it".
        """

    # -- operation authorization -------------------------------------------

    async def authorize_operation(
        self,
        ctx: ConnectorContext,
        *,
        operation: str,
        declared: DeclaredOperation,
        arguments: dict,
        external_user_id: str | None,
    ) -> None:
        """Prove the mapped external user may perform ``operation``.

        The runtime calls this immediately before :meth:`execute_operation` for
        any declared operation whose ``requires_user_authorization`` is set, so
        no provider has to remember to check for itself and none can forget to.

        ``external_user_id`` is the provider-side identity of the OpenJM
        principal that planned the action. ``arguments`` are the operation's own
        arguments, so an implementation can work out the target resource.

        Returning normally means current-user authorization was proven. Raising
        :class:`ConnectorAuthUnavailable` means it could not be proven, which the
        runtime treats as a refusal. The default refuses, because the base class
        knows nothing about a provider's ACLs: an operation that declares
        ``requires_user_authorization`` and does not override this is
        *not executable* rather than quietly allowed.

        The service credential is never sufficient evidence here. Proving that
        the connector's own token can reach a resource says nothing about whether
        the end user may act on it, and substituting one for the other is exactly
        the substitution this seam exists to prevent.
        """
        if declared.requires_user_authorization:
            raise ConnectorError(
                f"connector {self.type_id} cannot prove current-user "
                f"authorization for operation '{operation}'",
                code="user_authorization_unsupported",
            )

    # -- operations --------------------------------------------------------

    async def execute_operation(
        self, ctx: ConnectorContext, operation: str, arguments: dict
    ) -> dict:
        """Run a declared provider operation.

        Only operations declared in the connector's registry spec may be
        executed. The default implementation refuses everything, so a connector
        must opt in explicitly.
        """
        raise ConnectorError(
            f"connector {self.type_id} does not implement operation '{operation}'",
            code="unregistered_operation",
        )


# ---------------------------------------------------------------------------
# Shared HTTP access
# ---------------------------------------------------------------------------


class ConnectorHTTP:
    """The one place a connector implementation performs an outbound request.

    Centralizing this keeps three properties true by construction: requests are
    always bounded by a timeout, the caller's credential is attached here rather
    than assembled ad hoc, and provider error bodies are always redacted before
    they can escape as an exception message.
    """

    def __init__(self, ctx: ConnectorContext) -> None:
        self._ctx = ctx
        headers = {"Accept": "application/json"}
        token = ctx.credential.get("token")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.AsyncClient(
            base_url=(ctx.base_url or "").rstrip("/"),
            timeout=httpx.Timeout(float(ctx.timeout_seconds)),
            headers=headers,
            follow_redirects=False,
        )

    async def __aenter__(self) -> "ConnectorHTTP":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self._client.aclose()

    async def get_json(self, path: str, *, params: dict | None = None) -> Any:
        try:
            response = await self._client.get(path, params=params)
        except httpx.TimeoutException as exc:
            raise ConnectorAuthUnavailable(
                "external provider timed out", code="provider_timeout", detail=str(exc)
            ) from exc
        except httpx.HTTPError as exc:
            raise ConnectorError(
                "external provider is unreachable", code="provider_unreachable", detail=str(exc)
            ) from exc
        if response.status_code == 401 or response.status_code == 403:
            # A revoked or expired service credential lands here and must fail
            # closed rather than falling back to anything.
            raise ConnectorError(
                "external provider refused the service credential",
                code="credential_rejected",
                detail=f"HTTP {response.status_code}",
            )
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise ConnectorError(
                "external provider returned an error",
                code="provider_error",
                detail=f"HTTP {response.status_code}: {redact(response.text)}",
            )
        try:
            return response.json()
        except ValueError as exc:
            raise ConnectorError(
                "external provider returned a non-JSON body",
                code="provider_bad_payload",
                detail=redact(response.text),
            ) from exc
