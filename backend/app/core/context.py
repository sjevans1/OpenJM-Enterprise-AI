"""Request-scoped principal context.

The route layer resolves and authorizes a :class:`~app.core.identity.Principal`
through the FastAPI dependency in ``app.api.deps``. That validated principal is
published here so service-layer helpers can apply ownership predicates without
every intermediate function having to thread it through explicitly.

This is deliberately fail-closed: reading the context outside a request (a
background task, a misfired job, a unit test with no context) raises rather than
silently falling back to a default identity. There is no "anonymous principal".
"""

from __future__ import annotations

from contextvars import ContextVar

from app.core.identity import AuthenticationError, Principal

_current_principal: ContextVar[Principal | None] = ContextVar(
    "openjm_current_principal", default=None
)


def set_principal(principal: Principal | None) -> None:
    """Publish the validated principal for the current request/task."""
    _current_principal.set(principal)


def reset_principal() -> None:
    _current_principal.set(None)


def current_principal_or_none() -> Principal | None:
    return _current_principal.get()


def current_principal() -> Principal:
    """Return the validated principal, or fail closed."""
    principal = _current_principal.get()
    if principal is None:
        raise AuthenticationError(
            "No trusted identity context is active for this operation",
            code="no_identity_context",
        )
    return principal
