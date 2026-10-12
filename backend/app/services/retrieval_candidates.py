"""CA2-B: integration seam between the accepted CA2-A candidate contract and existing consumers.

STATUS: PROTOTYPE, OFF BY DEFAULT.

``retrieval_candidate_mode`` defaults to ``legacy``, in which every function here delegates to
the existing production behaviour and reports coverage as *unknown* rather than claiming a
completeness it cannot observe. The candidate mode is an experimental surface. It must not be
enabled as a production default, and it must not be enabled at all until the CA2-B correctness
and performance gates pass. Nothing in this module changes the retrieval engine: Chroma, DB-GPT,
the embedding model, the Rahkia boundary and the identity architecture are untouched.

What this module exists to do
-----------------------------
``governed_tenant_documents`` builds the Knowledge candidate set with a corpus-wide connector
authorization gate that stops at 100 checks without ordering, so a connector-origin document that
is genuinely authorized but outside that window is dropped **silently**. Measured on the frozen
baseline: 100 of 150 reachable, 50 omitted, and zero denials recorded for the 50.

This module keeps that exact behaviour in legacy mode and, in candidate mode, produces the same
candidate set through the CA2-A contract instead:

* local classification and policy first, before any provider budget is spent;
* a bounded CA2-A traversal of the connector scope, paged with safe continuation;
* truthful coverage, so partial coverage is *reported* rather than silent;
* an application-generated partial-coverage notice that carries no counts, identifiers, titles
  or provider detail.

Two accessors, deliberately different
-------------------------------------
* :func:`governed_candidates` is for **exploratory** requests. It may return a partial set, and it
  says so.
* :func:`strict_pinned_documents` is for **authoritative** requests: pinned reports, reruns,
  snapshots and exports. It revalidates the exact requested scope through
  :func:`revalidate_document_ids` and requires :func:`require_strict_scope` to succeed, so it
  never assembles a result from authorizations collected on earlier pages, and it refuses rather
  than quietly emitting a subset.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Document
from app.services.connectors.candidate_authorization import (
    DEFAULT_PAGE_BUDGET,
    CoverageStatus,
    ScopeKind,
    StrictScopeError,
    authorize_candidates,
    revalidate_document_ids,
    require_strict_scope,
)
from app.services.document_lifecycle import retrievable_filter
from app.services.document_policy import (
    DocumentAccess,
    connector_origin_document_ids,
    document_is_visible,
)

MODE_LEGACY = "legacy"
MODE_CANDIDATE = "candidate"
_VALID_MODES = frozenset({MODE_LEGACY, MODE_CANDIDATE})

# Request-level provider-check cap for the candidate mode. Deliberately ABOVE the legacy
# corpus-wide bound of 100 so that enabling the candidate mode cannot check fewer resources than
# the path it replaces. It is finite and it is a per-request cap, not a product limit.
DEFAULT_REQUEST_CHECK_CAP = 256

# Bound on how many traversal pages one request will walk, so a continuation loop is finite even
# if every page is scan-bound rather than budget-bound.
MAX_TRAVERSAL_PAGES = 64

# Hard ceiling on the provider authorization checks a single request may request. Enforced HERE,
# not only at the API, so no caller can bypass the per-request guard by calling the service
# directly. It is a ceiling, not a default: the default remains DEFAULT_REQUEST_CHECK_CAP (256) and
# the legacy corpus bound of 100 is untouched.
MAX_REQUEST_CHECK_CAP = 1024

COVERAGE_NOTICE_CODE = "partial_coverage"

# One fixed, application-generated message per cause. Fixed prose on purpose: a notice built from
# data would risk disclosing counts, identifiers or titles, and it must be surfaceable to a user
# as it stands.
REASON_PROVIDER_CAP = "provider_cap_reached"
COVERAGE_NOTICE_MESSAGES = {
    "budget_exhausted": (
        "Some connector sources were not reached within this request's authorization budget, so "
        "this answer may not include every source you are authorized to read."
    ),
    REASON_PROVIDER_CAP: (
        "This request reached its authorization budget before every connector source could be "
        "checked, so this answer may not include every source you are authorized to read. Ask a "
        "narrower question, or ask again to continue checking."
    ),
    "scan_bound_reached": (
        "Only part of the source set could be checked in this request, so this answer may not "
        "include every source you are authorized to read. Ask again to continue checking."
    ),
    "authorization_unavailable": (
        "Some connector sources could not be verified because the provider did not answer, so "
        "this answer may not include every source you are authorized to read. Try again shortly."
    ),
    "corpus_changed": (
        "The source set changed while it was being checked, so this answer may not include every "
        "source you are authorized to read. Ask again to search the current set."
    ),
}

COVERAGE_NOTICE_FALLBACK = (
    "Some sources could not be checked in this request, so this answer may not include every "
    "source you are authorized to read."
)


def configured_mode() -> str:
    """The configured integration mode, defaulting to legacy for any unrecognised value."""
    settings = get_settings()
    raw = getattr(settings, "retrieval_candidate_mode", MODE_LEGACY)
    mode = str(raw or MODE_LEGACY).strip().lower()
    return mode if mode in _VALID_MODES else MODE_LEGACY


def candidate_mode_enabled() -> bool:
    return configured_mode() == MODE_CANDIDATE


@dataclass(frozen=True)
class CandidateSet:
    """The governed candidate set plus the truth about how complete it is."""

    documents: list[Document]
    mode: str
    scope_kind: ScopeKind
    # None means "not knowable in this mode". The legacy gate cannot observe whether it truncated,
    # so legacy mode reports None rather than claiming completeness.
    coverage: CoverageStatus | None
    notice: dict[str, Any] | None
    provider_checks: int
    sql_reads: int
    cursor: str | None
    # The typed cause behind a partial coverage verdict, from the contract. Empty when complete.
    reason: str = ""

    @property
    def is_partial(self) -> bool:
        return self.coverage is CoverageStatus.PARTIAL

    def notice_for_user(self) -> dict[str, Any] | None:
        """The only coverage shape safe to expose. Returns None when nothing needs saying."""
        return self.notice


def _partial_notice(reason: str) -> dict[str, Any]:
    """The coverage notice. Fixed prose per cause, and never any data about the sources."""
    return {
        "code": COVERAGE_NOTICE_CODE,
        "coverage": CoverageStatus.PARTIAL.value,
        "reason": reason,
        "message": COVERAGE_NOTICE_MESSAGES.get(reason, COVERAGE_NOTICE_FALLBACK),
    }


async def _legacy_governed_documents(db: AsyncSession, access: DocumentAccess) -> list[Document]:
    """The unchanged production candidate set. Imported locally to avoid an import cycle."""
    from app.services.document_policy import governed_tenant_documents

    return list(await governed_tenant_documents(db, access))


async def _visible_tenant_documents(db: AsyncSession, access: DocumentAccess) -> list[Document]:
    """Tenant, retrievable and locally policy-visible, in the legacy ordering.

    This is the same first step the legacy gate performs, exposed so the candidate path can apply
    local policy before spending any provider budget.
    """
    rows = (
        (
            await db.execute(
                select(Document)
                .where(Document.tenant_id == access.tenant_id, *retrievable_filter())
                .order_by(Document.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [document for document in rows if document_is_visible(access, document)]


async def governed_candidates(
    db: AsyncSession,
    access: DocumentAccess,
    *,
    query: str,
    check_cap: int = DEFAULT_REQUEST_CHECK_CAP,
    page_budget: int = DEFAULT_PAGE_BUDGET,
    connector_instance_ids: Sequence[str] | None = None,
    cursor: str | None = None,
    include_native: bool = True,
) -> CandidateSet:
    """The governed candidate set for an exploratory request.

    Legacy mode returns the unchanged production set and reports ``coverage=None``, because the
    legacy gate gives no way to know whether it truncated.

    Candidate mode applies local policy first, then walks the connector scope through the CA2-A
    contract, paging with safe continuation until the scope is exhausted or the request's
    provider-check cap is reached. Partial coverage is reported, never hidden, and a document
    beyond the first window remains reachable on a later page rather than being dropped.
    """
    mode = configured_mode()
    if mode == MODE_LEGACY:
        documents = await _legacy_governed_documents(db, access)
        return CandidateSet(
            documents=documents,
            mode=mode,
            scope_kind=ScopeKind.TRAVERSAL,
            coverage=None,
            notice=None,
            provider_checks=0,
            sql_reads=0,
            cursor=None,
        )

    if check_cap <= 0:
        raise ValueError("check_cap must be positive; the provider budget is always finite")
    if check_cap > MAX_REQUEST_CHECK_CAP:
        raise ValueError(
            f"check_cap {check_cap} exceeds MAX_REQUEST_CHECK_CAP {MAX_REQUEST_CHECK_CAP}; "
            "a single request is bounded"
        )
    if page_budget <= 0:
        raise ValueError("page_budget must be positive")

    local_visible = await _visible_tenant_documents(db, access)
    connector_origin = await connector_origin_document_ids(db, tenant_id=access.tenant_id)

    allowed_connector_documents: set[str] = set()
    provider_checks = 0
    sql_reads = 0
    coverage = CoverageStatus.PARTIAL
    reason = REASON_PROVIDER_CAP
    # Continuation continues where a previous page stopped, in this request or in an earlier one.
    # The cursor is tenant, principal, query and scope bound and AEAD sealed, so it cannot be
    # replayed across principals or steered at another scope.
    page_cursor: str | None = cursor

    for _ in range(MAX_TRAVERSAL_PAGES):
        # The cap is enforced BEFORE the page is issued, so the total can never overshoot it, even
        # when the cap is not a multiple of the page budget: a 100 check cap with a 32 check page
        # budget issues 32, 32, 32, then 4, and stops at exactly 100.
        remaining = check_cap - provider_checks
        if remaining <= 0:
            coverage, reason = CoverageStatus.PARTIAL, REASON_PROVIDER_CAP
            break
        result = await authorize_candidates(
            db,
            access=access,
            query=query,
            budget=min(page_budget, remaining),
            cursor=page_cursor,
            connector_instance_ids=connector_instance_ids,
        )
        provider_checks += result.provider_checks
        sql_reads += result.sql_reads
        allowed_connector_documents |= set(result.authorized_document_ids)
        page_cursor = result.cursor
        if page_cursor is None:
            # The scope was fully walked, so the page's own coverage verdict stands, including a
            # PARTIAL verdict caused by an unresolved authorization on the final page.
            coverage = result.coverage
            reason = result.coverage_reason or REASON_PROVIDER_CAP
            break
        if provider_checks >= check_cap:
            coverage, reason = CoverageStatus.PARTIAL, REASON_PROVIDER_CAP
            break
    else:
        # The page bound was reached with work still outstanding.
        coverage, reason = CoverageStatus.PARTIAL, REASON_PROVIDER_CAP

    # The same intersection rule as the legacy gate: native sources pass on local policy alone,
    # connector-origin sources need both gates. Ordering is preserved from the legacy ordering.
    native_documents = [
        document for document in local_visible if document.id not in connector_origin
    ]
    # Native sources need no authorization traversal, and re-emitting them on every continuation
    # page would duplicate them, so they belong to the FIRST page only. A caller continuing with a
    # cursor receives connector pages, which are the pages that can actually be incomplete.
    keep = set(allowed_connector_documents)
    if include_native:
        keep |= {document.id for document in native_documents}
    documents = [document for document in local_visible if document.id in keep]
    partial = coverage is CoverageStatus.PARTIAL
    return CandidateSet(
        documents=documents,
        mode=mode,
        scope_kind=ScopeKind.TRAVERSAL,
        coverage=coverage,
        reason=reason,
        notice=_partial_notice(reason) if partial else None,
        provider_checks=provider_checks,
        sql_reads=sql_reads,
        cursor=page_cursor,
    )


async def strict_pinned_documents(
    db: AsyncSession,
    access: DocumentAccess,
    *,
    document_ids: Sequence[str],
) -> list[Document]:
    """The exact requested scope, or a loud refusal. For authoritative consumers only.

    Pinned reports, reruns, snapshots and exports must use this rather than a candidate set,
    because a candidate set is a best-effort view of a scope while a pinned scope is a promise
    about specific sources.

    Connector-origin sources are revalidated at consumption time through the CA2-A exact-source
    contract and must satisfy ``require_strict_scope``, so a source that was denied, revoked,
    unresolved, locally restricted or left undecided raises ``StrictScopeError`` instead of
    silently disappearing. Native sources are governed by local policy alone, because they have no
    provider resource to revalidate. Nothing here assembles a result from authorizations collected
    on earlier pages.
    """
    requested = {str(document_id) for document_id in document_ids}
    if not requested:
        return []

    connector_origin = await connector_origin_document_ids(db, tenant_id=access.tenant_id)
    connector_part = sorted(requested & connector_origin)
    native_part = sorted(requested - connector_origin)

    if connector_part:
        revalidated = await revalidate_document_ids(
            db, access=access, document_ids=connector_part
        )
        # Raises StrictScopeError unless every requested connector source is currently authorized.
        require_strict_scope(revalidated)

    rows = (
        (
            await db.execute(
                select(Document).where(
                    Document.tenant_id == access.tenant_id,
                    Document.id.in_(requested),
                    *retrievable_filter(),
                )
            )
        )
        .scalars()
        .all()
    )
    visible = {document.id: document for document in rows if document_is_visible(access, document)}

    unresolved = requested - set(visible)
    if unresolved:
        # Covers a native source that is no longer locally readable, and any source that vanished
        # between revalidation and this read.
        raise StrictScopeError(
            "pinned_source_not_readable",
            "a pinned source is not currently readable under policy",
        )
    # Ordered the same way the governed candidate set is (created_at DESC), so a scoped report run
    # keeps the ordering its output contract already had.
    ordered = sorted(visible.values(), key=lambda document: document.created_at, reverse=True)
    return [document for document in ordered if document.id in requested]
