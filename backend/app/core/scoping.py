"""Ownership scoping helpers.

Every tenant-owned row is addressed by two predicates: the tenant (the
isolation boundary) and the principal (the owner). Applying both is what makes
cross-tenant access structurally impossible, including for a principal who is a
member of more than one tenant.

Use ``owned_by(Model, principal)`` inside a ``where(...)`` clause:

    select(Document).where(*owned_by(Document, principal))
"""

from __future__ import annotations

from app.core.identity import Principal


def owned_by(model, principal: Principal) -> tuple:
    return (
        model.tenant_id == principal.tenant_id,
        model.user_id == principal.user_id,
    )


def tenant_scoped(model, principal: Principal) -> tuple:
    """Tenant-only scope, for resources shared inside one tenant."""
    return (model.tenant_id == principal.tenant_id,)
