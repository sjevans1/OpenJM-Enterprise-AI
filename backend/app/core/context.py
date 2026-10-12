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


# CA2-B: the application-generated partial-coverage notice for the current request.
#
# Request scoped, like the principal above, and deliberately NOT cached across requests: it
# describes one execution's coverage and must never be replayed into another. Both the orchestrator
# and the chat response layer read it, so a coverage signal produced deep in the Knowledge path
# reaches the response without adding fields to every intermediate return type.
_current_coverage_notice: ContextVar[dict | None] = ContextVar(
    "current_coverage_notice", default=None
)


def set_coverage_notice(notice: dict | None) -> None:
    """Publish the coverage notice for this request, overwriting any previous value.

    Overwriting rather than accumulating is deliberate: a request that does not produce a notice
    must not inherit one from an earlier request on the same task.
    """
    _current_coverage_notice.set(notice)


def current_coverage_notice() -> dict | None:
    return _current_coverage_notice.get()


def reset_coverage_notice() -> None:
    _current_coverage_notice.set(None)
