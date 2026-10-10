"""Delegated OpenJM support read path (Lane K).

The support-delegation records already existed (``SupportDelegation``, migration
0012) but nothing in the application consumed them. This module is the missing,
deliberately narrow consumer: an OpenJM support actor reaches a **target
tenant's** content only through an explicit, tenant-created, time-bound
``SupportDelegation`` row that names that actor and that tenant and carries the
``content`` scope.

Non-negotiables encoded here (each is asserted by
``tests/test_lane_k_support_content.py``):

* **Delegation is the only authority.** Platform-operator status, a tenant role,
  and a ``metadata``-scope delegation all grant *no* content access.
* **Bounded.** A ``content`` delegation carries an explicit
  ``classification_ceiling``, group allow-list and owning department. A content
  read is permitted only when the document is permitted by *both* the
  delegation's bounds *and* the tenant's classification/source policy; the
  delegation never replaces or widens classification or source authorization.
* **Resolved on every request, from the database.** The delegation is re-read
  here, not cached on the principal, so a revocation or an expiry takes effect
  on the very next request.
* **Tenant-scoped, never cross-tenant.** The delegation row is keyed by the
  target tenant, and every read query additionally predicates on that tenant.
* **Fail closed.** A missing, revoked or expired delegation resolves to a deny
  with an audit row, never to a default allow.
* **Audited.** Both the allow and the deny decision write an ``AuditRecord``.
* **Read-only.** Nothing in this module writes a customer row; a support read
  cannot mutate content.

The tenant-member principal model is deliberately untouched: a support actor is
not made a member of the target tenant and is never given owner/admin
equivalence. Support authority lives in one extra, revocable row.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.governance import (
    DEFAULT_SOURCE_CLASSIFICATION,
    classification_rank,
    is_known_classification,
)
from app.core.identity import AuthorizationError, Principal
from app.models import Document, SupportDelegation, Tenant
from app.services.document_lifecycle import retrievable_filter
from app.services.document_policy import (
    DocumentAccess,
    allowed_group_ids,
    document_is_visible,
)

# The two support scopes the delegation model defines. ``metadata`` administers
# tenant metadata only; ``content`` is the sole scope that reaches customer
# governed content.
SUPPORT_METADATA_SCOPE = "metadata"
SUPPORT_CONTENT_SCOPE = "content"
SUPPORT_SCOPES: tuple[str, ...] = (SUPPORT_METADATA_SCOPE, SUPPORT_CONTENT_SCOPE)


class SupportAccessError(AuthorizationError):
    """A support read was refused: no effective delegation for that scope."""

    status_code = 403
    code = "support_access_denied"


def _as_utc(value: datetime | None) -> datetime | None:
    """Normalise a stored datetime (SQLite returns naive UTC values)."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class SupportReadContext:
    """The resolved, audited authority for one delegated support read.

    For a ``content`` delegation the context carries the *bounded* scope of the
    grant: the maximum classification it may reach (``classification_ceiling``),
    the explicit group allow-list it is confined to, and the owning department it
    is confined to. Those bounds are evaluated against the tenant's existing
    classification/source policy, never in place of it.
    """

    principal: Principal
    tenant_id: str
    scope: str
    delegation_id: str
    classification_ceiling: str = DEFAULT_SOURCE_CLASSIFICATION
    allowed_group_ids: frozenset[str] = frozenset()
    department_id: str | None = None

    def delegation_access(self) -> DocumentAccess:
        """A delegation-derived access context for the governed content policy.

        The ``owner_key`` is deliberately *not* a customer owner key (it is
        namespaced to the delegation), so no owner-scoped document is ever
        auto-visible to a support read. ``steward_scopes`` is empty: a delegation
        confers no stewardship. Only the delegation's own bounded group and
        department grants are applied.
        """
        department_ids = (
            frozenset({self.department_id}) if self.department_id else frozenset()
        )
        return DocumentAccess(
            tenant_id=self.tenant_id,
            principal_id=self.principal.principal_id,
            owner_key=f"support-delegation:{self.delegation_id}",
            group_ids=self.allowed_group_ids,
            department_ids=department_ids,
            steward_scopes=frozenset(),
        )


async def active_support_delegation(
    db: AsyncSession, *, principal_id: str, tenant_id: str, scope: str
) -> SupportDelegation | None:
    """The one effective delegation for (principal, tenant, scope), or ``None``.

    Effective means: the row exists, its ``status`` is ``active``, and it is not
    past its ``expires_at``. Anything else — including an unknown scope — fails
    closed to ``None``.
    """
    if scope not in SUPPORT_SCOPES:
        return None
    row = (
        await db.execute(
            select(SupportDelegation).where(
                SupportDelegation.tenant_id == tenant_id,
                SupportDelegation.principal_id == principal_id,
                SupportDelegation.scope == scope,
            )
        )
    ).scalar_one_or_none()
    if row is None or row.status != "active":
        return None
    expires = _as_utc(row.expires_at)
    if expires is not None and expires <= datetime.now(timezone.utc):
        return None
    return row


async def resolve_support_read(
    db: AsyncSession, *, principal: Principal, tenant_id: str, scope: str
) -> SupportReadContext:
    """Resolve and audit one delegated support read, or fail closed.

    On success returns a :class:`SupportReadContext` and writes an ``allow``
    audit row. On failure writes a ``deny`` audit row and raises
    :class:`SupportAccessError`. The audit row is flushed but not committed; the
    caller (the route dependency) commits it.
    """
    # Imported lazily to avoid an import cycle with app.services.identity.
    from app.services import identity as identity_service

    action = f"support.{scope}.read"
    delegation = await active_support_delegation(
        db, principal_id=principal.principal_id, tenant_id=tenant_id, scope=scope
    )
    if delegation is None:
        await identity_service.record_audit(
            db,
            principal=principal,
            action=action,
            decision="deny",
            tenant_id=tenant_id,
            resource_type="support_delegation",
            resource_id=f"{tenant_id}:{principal.principal_id}:{scope}",
            reason="no active support delegation for this tenant and scope",
        )
        raise SupportAccessError(
            "No active support delegation for this tenant and scope",
            code="support_access_denied",
        )

    await identity_service.record_audit(
        db,
        principal=principal,
        action=action,
        decision="allow",
        tenant_id=tenant_id,
        resource_type="support_delegation",
        resource_id=delegation.id,
        metadata={"scope": scope},
    )
    return SupportReadContext(
        principal=principal,
        tenant_id=tenant_id,
        scope=scope,
        delegation_id=delegation.id,
        classification_ceiling=str(
            delegation.classification_ceiling or DEFAULT_SOURCE_CLASSIFICATION
        ),
        allowed_group_ids=allowed_group_ids(delegation),
        department_id=delegation.department_id,
    )


async def read_tenant_content(
    db: AsyncSession, *, context: SupportReadContext
) -> list[Document]:
    """The target tenant's retrievable governed documents (read-only).

    Requires a ``content``-scope context. A returned document must be permitted
    by **both** gates:

    * the active delegation's *bounded* scope — evaluated through the tenant's
      existing classification/source policy against a delegation-derived
      :class:`DocumentAccess` (the delegation's own group allow-list and owning
      department; no stewardship, and no customer owner key); and
    * the delegation's ``classification_ceiling`` — a document whose
      classification rank exceeds the ceiling is excluded.

    So a ``content`` delegation never grants more than its bounds and never
    replaces the tenant's classification or source authorization. The query is
    tenant-qualified with the *validated* target tenant, so it can never return
    another tenant's rows, and it applies the single authoritative
    ``retrievable_filter`` so partially ingested, failed or deleted documents
    never surface. Unauthorized rows are removed from the result set entirely —
    no title, body or passage is ever built for them.
    """
    if context.scope != SUPPORT_CONTENT_SCOPE:
        raise SupportAccessError(
            "A content-scope delegation is required for a content read",
            code="support_scope_mismatch",
        )
    # Fail closed on a corrupt ceiling: an unknown classification is not a
    # licence to read everything, so no content is returned.
    if not is_known_classification(context.classification_ceiling):
        return []
    ceiling_rank = classification_rank(context.classification_ceiling)
    access = context.delegation_access()

    rows = (
        (
            await db.execute(
                select(Document)
                .where(
                    and_(
                        Document.tenant_id == context.tenant_id,
                        *retrievable_filter(),
                    )
                )
                .order_by(Document.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    permitted: list[Document] = []
    for document in rows:
        if not document_is_visible(access, document):
            continue
        if classification_rank(document.classification) > ceiling_rank:
            continue
        permitted.append(document)
    return permitted


async def read_tenant_metadata(
    db: AsyncSession, *, context: SupportReadContext
) -> dict:
    """The target tenant's control metadata (no customer content).

    Requires a ``metadata``-scope context. Returns only identifying, non-content
    fields, mirroring the separation the control plane already enforces.
    """
    if context.scope != SUPPORT_METADATA_SCOPE:
        raise SupportAccessError(
            "A metadata-scope delegation is required for a metadata read",
            code="support_scope_mismatch",
        )
    tenant = await db.get(Tenant, context.tenant_id)
    if tenant is None:
        raise SupportAccessError("Unknown tenant", code="support_tenant_missing")
    return {
        "id": tenant.id,
        "slug": tenant.slug,
        "name": tenant.name,
        "status": tenant.status,
    }


__all__ = [
    "SUPPORT_CONTENT_SCOPE",
    "SUPPORT_METADATA_SCOPE",
    "SUPPORT_SCOPES",
    "SupportAccessError",
    "SupportReadContext",
    "active_support_delegation",
    "read_tenant_content",
    "read_tenant_metadata",
    "resolve_support_read",
]
