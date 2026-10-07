"""Knowledge source classification and pre-retrieval authorization (BV1-B).

This module answers one question: *may this principal use this governed
document as evidence?* It is the data side of the RBAC/policy split. It is
applied to the candidate document set **before** any vector search, before any
evidence is built, and therefore before anything reaches a model prompt.

Interpretation note (stricter reading, per Issue #45 package rule): the accepted
data model is owner/connector scoped, so this increment applies classification as
an *additional restriction* on that candidate set. It does not introduce tenant
wide sharing of ``internal`` documents across owners; the last clause of
:func:`document_is_visible` (the tenant-wide fallback) only ever admits a
document the caller could already reach, and is included so the vocabulary is
complete and testable. Enabling tenant-wide internal sharing is a product
decision recorded as NOT RUN in the handoff, not silently taken here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from app.core.governance import (
    TENANT_WIDE_CLASSIFICATIONS,
    StewardScopeType,
    is_known_classification,
)


@dataclass(frozen=True)
class DocumentAccess:
    """The request principal's data-side scopes, for one knowledge request."""

    tenant_id: str
    principal_id: str
    owner_key: str
    group_ids: frozenset[str] = frozenset()
    department_ids: frozenset[str] = frozenset()
    steward_scopes: frozenset[tuple[str, str]] = frozenset()


def access_from_principal(principal) -> DocumentAccess:
    return DocumentAccess(
        tenant_id=principal.tenant_id,
        principal_id=principal.principal_id,
        owner_key=principal.user_id,
        group_ids=principal.group_ids,
        department_ids=principal.department_ids,
        steward_scopes=principal.steward_scopes,
    )


def allowed_group_ids(document) -> frozenset[str]:
    """Parse a document's allowed-group list, ignoring malformed content."""
    raw = getattr(document, "allowed_group_ids_json", None)
    if not raw:
        return frozenset()
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return frozenset()
    if not isinstance(parsed, list):
        return frozenset()
    return frozenset(item for item in parsed if isinstance(item, str) and item)


def document_is_visible(access: DocumentAccess | None, document) -> bool:
    """Fail-closed visibility for one governed Knowledge document.

    ``access`` of ``None`` means the caller supplied no request identity (an
    internal/legacy path); tenant and owner scoping have already been applied by
    the caller's candidate query, so no additional restriction is added.
    """
    if access is None:
        return True
    # Defense in depth: tenant isolation is already enforced by the tenant
    # qualified ownership predicate, but never admit another tenant here either.
    if str(document.tenant_id) != access.tenant_id:
        return False

    classification = getattr(document, "classification", None)
    if not is_known_classification(classification):
        # Unknown/missing/corrupt class fails closed to the strictest behavior.
        classification = None
    else:
        classification = str(classification)

    # The tenant-wide fallback is only for the broad classes, and only when the
    # document is explicitly marked tenant visible. A not-yet-flushed object has
    # no value; the column default is true, so treat "unset" as the default.
    tenant_visible = getattr(document, "tenant_visible", True)
    if tenant_visible is None:
        tenant_visible = True
    if classification in TENANT_WIDE_CLASSIFICATIONS and bool(tenant_visible):
        return True

    # Explicit grants, valid for any classification.
    if allowed_group_ids(document) & access.group_ids:
        return True
    department_id = getattr(document, "department_id", None)
    if department_id:
        if department_id in access.department_ids:
            return True
        if (StewardScopeType.DEPARTMENT.value, department_id) in access.steward_scopes:
            return True
    if (StewardScopeType.TENANT.value, access.tenant_id) in access.steward_scopes:
        return True
    return False


def visible_documents(access: DocumentAccess | None, documents):
    """Filter a candidate set down to the documents this principal may use."""
    if access is None:
        return list(documents)
    return [document for document in documents if document_is_visible(access, document)]
