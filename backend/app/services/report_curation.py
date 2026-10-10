"""BV6-A governed report curation.

Saved Reports are evidence-backed governed snapshots. This module adds a
bounded curation layer on top of them: an explicit state machine, a reason and
audit linkage, and a SEPARATE presentation flag.

State machine (one machine; resolves the planning note's ``under_review``
inconsistency by taking the transition section as authoritative and keeping
``featured`` out of the evidentiary ladder)::

    none --request_review--> under_review --approve--> approved
    approved --mark_authoritative--> authoritative
    any(curated) --withdraw/auto_demote--> none

``featured`` is a boolean presentation/catalog highlight set only by a tenant
admin. It never overwrites ``curation_state`` and is never evidence of
authority: featuring an ``approved`` report leaves it ``approved``.

Design rules:

* **Tenant-scoped, fail closed.** Every read and write is narrowed by the
  actor's active tenant. A report belonging to another tenant is reported as
  absent, never revealed.
* **No stale approval.** Every source-bearing transition (request, approve,
  authoritative, auto-demote) re-runs the *current* source authorization
  predicate under the actor's current access before it is recorded. A revoked
  document, a demoted data source, a closed connector gate or a lost table grant
  makes the transition fail closed. Withdrawal and featuring reduce or are
  neutral to exposure, so they never depend on a fresh grant.
* **Authority is the customer-content axis.** The report owner may request
  review but may not self-approve. A tenant admin or a steward whose scope
  covers the report's pinned sources may approve. ``authoritative`` requires
  TWO distinct authorized principals. A platform operator alone holds no
  content-curation authority.
* **Departmental ownership is derived, never guessed.** The owning department is
  the single department shared by the pinned documents/sources; a scope that
  spans several departments fails closed and only a tenant admin may resolve it.
* **Audited, never rewritten.** Every transition writes an append-only
  ``AuditRecord`` and stores its id; history is preserved through withdrawal
  and demotion.

This module makes no retrieval-ranking decision and trains no model. It only
records who may rely on a saved snapshot and under what governing state.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.governance import StewardScopeType
from app.core.identity import AuthorizationError, Principal
from app.core.permissions import Permission
from app.models import (
    DataSource,
    Document,
    SavedReport,
    now_utc,
)
from app.services.document_policy import allowed_group_ids

# The shared source-authorization predicate and evidence parser live with the
# reports API. They are imported here (the service layer calls into the existing
# predicate rather than re-implementing authorization) and there is no import
# cycle: app.api.reports never imports this module.
from app.api.reports import _parse_evidence, _source_ids, _sources_available

CURATION_NONE = "none"
CURATION_UNDER_REVIEW = "under_review"
CURATION_APPROVED = "approved"
CURATION_AUTHORITATIVE = "authoritative"

CURATION_STATES: tuple[str, ...] = (
    CURATION_NONE,
    CURATION_UNDER_REVIEW,
    CURATION_APPROVED,
    CURATION_AUTHORITATIVE,
)

# States under which a tenant admin may feature the report (presentation only).
FEATUREABLE_STATES: frozenset[str] = frozenset({CURATION_APPROVED, CURATION_AUTHORITATIVE})

_MAX_REASON = 240

AUDIT_ACTION_PREFIX = "governance.report.curation"


class CurationError(ValueError):
    """A request that is invalid against the curation state machine."""


class CurationStateError(CurationError):
    """The report is not in a state from which the transition is allowed."""


@dataclass(frozen=True)
class OwnerScope:
    """Deterministically derived ownership of a report's pinned source scope."""

    department_id: str | None
    # True when the pinned sources span more than one department: no single
    # steward covers it and only a tenant admin may resolve ownership.
    mixed: bool
    # The groups the pinned sources are explicitly granted to.
    group_ids: frozenset[str]


def _normalize_reason(reason: str | None) -> str | None:
    if reason is None:
        return None
    text = reason.strip()
    if not text:
        return None
    if len(text) > _MAX_REASON:
        raise CurationError(f"reason must be at most {_MAX_REASON} characters")
    return text


async def load_report(
    db: AsyncSession, *, principal: Principal, report_id: str
) -> SavedReport | None:
    """Load one report inside the actor's tenant, or None (no foreign leak)."""
    return (
        await db.execute(
            select(SavedReport).where(
                SavedReport.id == report_id,
                SavedReport.tenant_id == principal.tenant_id,
            )
        )
    ).scalar_one_or_none()


def _evidence(report: SavedReport):
    """Parse a report's persisted evidence, failing closed on malformed rows."""
    try:
        return _parse_evidence(report.evidence_json)
    except HTTPException as exc:
        raise CurationStateError(
            "Saved report evidence is not a bounded governed snapshot"
        ) from exc


async def derive_owner_scope(
    db: AsyncSession, *, report: SavedReport, tenant_id: str
) -> OwnerScope:
    """Derive the owning department and granted groups from the pinned sources.

    The department is derived only from the pinned documents/sources' own
    ``department_id``; it is never inferred from a display name or an email.
    """
    evidence = _evidence(report)
    document_ids, source_ids = _source_ids(evidence)

    departments: set[str] = set()
    groups: set[str] = set()

    if document_ids:
        documents = (
            await db.execute(
                select(Document).where(
                    Document.id.in_(document_ids),
                    Document.tenant_id == tenant_id,
                )
            )
        ).scalars().all()
        for document in documents:
            if document.department_id:
                departments.add(str(document.department_id))
            groups |= set(allowed_group_ids(document))

    if source_ids:
        sources = (
            await db.execute(
                select(DataSource).where(
                    DataSource.id.in_(source_ids),
                    DataSource.tenant_id == tenant_id,
                )
            )
        ).scalars().all()
        for source in sources:
            if source.department_id:
                departments.add(str(source.department_id))
            groups |= set(allowed_group_ids(source))

    if len(departments) == 1:
        return OwnerScope(department_id=next(iter(departments)), mixed=False, group_ids=frozenset(groups))
    if len(departments) > 1:
        return OwnerScope(department_id=None, mixed=True, group_ids=frozenset(groups))
    return OwnerScope(department_id=None, mixed=False, group_ids=frozenset(groups))


def _steward_covers(principal: Principal, scope: OwnerScope) -> bool:
    """Does an active steward scope cover the report's pinned source scope?"""
    scopes = principal.steward_scopes
    if (StewardScopeType.TENANT.value, principal.tenant_id) in scopes:
        return True
    if scope.department_id and (
        StewardScopeType.DEPARTMENT.value,
        scope.department_id,
    ) in scopes:
        return True
    for group_id in scope.group_ids:
        if (StewardScopeType.GROUP.value, group_id) in scopes:
            return True
    return False


def _is_tenant_admin(principal: Principal) -> bool:
    return principal.has(Permission.TENANT_ADMIN)


def _is_owner(principal: Principal, report: SavedReport) -> bool:
    return principal.user_id == report.user_id


def _require_approver(principal: Principal, report: SavedReport, scope: OwnerScope) -> None:
    """Who may approve a report (also used to authorize ``authoritative``).

    A tenant admin may approve where policy permits (including resolving a mixed
    scope). A covering steward may approve a single-department or group-visible
    scope. The owner may never self-approve. Platform authority is ignored
    entirely, so an operator without tenant-side authority is refused.
    """
    if _is_owner(principal, report):
        raise AuthorizationError(
            "The report owner may not approve their own report",
            code="self_approval_forbidden",
        )
    if _is_tenant_admin(principal):
        return
    if scope.mixed:
        raise AuthorizationError(
            "A mixed-department report may only be approved by a tenant admin",
            code="missing_scope",
        )
    if _steward_covers(principal, scope):
        return
    raise AuthorizationError(
        "Principal lacks tenant-admin or covering-steward authority over this report",
        code="missing_scope",
    )


async def _revalidate(db: AsyncSession, *, principal: Principal, evidence) -> tuple[bool, str]:
    """Re-run current source authorization; return (ok, reason_category)."""
    ok = await _sources_available(db, evidence, principal=principal)
    return ok, ("pass" if ok else "source_authorization_failed")


async def _record(
    db: AsyncSession,
    *,
    principal: Principal,
    report: SavedReport,
    transition: str,
    prior_state: str,
    reason: str | None,
    revalidated: str,
    department_id: str | None,
    extra: dict | None = None,
) -> None:
    from app.services.identity import record_audit

    metadata = {
        "prior_state": prior_state,
        "new_state": report.curation_state,
        "reason": reason,
        "revalidated": revalidated,
        "department_id": department_id,
    }
    if extra:
        metadata.update(extra)
    audit = await record_audit(
        db,
        principal=principal,
        action=f"{AUDIT_ACTION_PREFIX}.{transition}",
        decision="allow",
        resource_type="saved_report",
        resource_id=report.id,
        reason=reason,
        metadata=metadata,
    )
    report.curation_audit_id = audit.id
    report.curation_updated_at = now_utc()


async def request_review(
    db: AsyncSession, *, principal: Principal, report: SavedReport, reason: str | None = None
) -> SavedReport:
    """``none`` -> ``under_review``. Owner, covering steward or tenant admin."""
    state = report.curation_state
    if state == CURATION_UNDER_REVIEW:
        return report
    if state != CURATION_NONE:
        raise CurationStateError(
            f"Only a non-curated report may be submitted for review (state={state!r})"
        )
    reason = _normalize_reason(reason)
    scope = await derive_owner_scope(db, report=report, tenant_id=principal.tenant_id)
    if not (
        _is_owner(principal, report)
        or _is_tenant_admin(principal)
        or (not scope.mixed and _steward_covers(principal, scope))
    ):
        raise AuthorizationError(
            "Principal may not request review of this report",
            code="missing_scope",
        )
    evidence = _evidence(report)
    ok, category = await _revalidate(db, principal=principal, evidence=evidence)
    if not ok:
        raise CurationStateError("Report sources are no longer authorized")

    prior = report.curation_state
    report.curation_state = CURATION_UNDER_REVIEW
    report.curation_reason = reason
    await _record(
        db,
        principal=principal,
        report=report,
        transition="request_review",
        prior_state=prior,
        reason=reason,
        revalidated=category,
        department_id=scope.department_id,
    )
    await db.flush()
    return report


async def approve(
    db: AsyncSession, *, principal: Principal, report: SavedReport, reason: str | None = None
) -> SavedReport:
    """``under_review`` -> ``approved``. Covering steward or tenant admin."""
    if report.curation_state != CURATION_UNDER_REVIEW:
        raise CurationStateError(
            "Only a report under review may be approved "
            f"(state={report.curation_state!r})"
        )
    reason = _normalize_reason(reason)
    scope = await derive_owner_scope(db, report=report, tenant_id=principal.tenant_id)
    _require_approver(principal, report, scope)
    evidence = _evidence(report)
    ok, category = await _revalidate(db, principal=principal, evidence=evidence)
    if not ok:
        raise CurationStateError("Report sources are no longer authorized")

    prior = report.curation_state
    report.curation_state = CURATION_APPROVED
    report.curation_reason = reason
    report.curation_first_approver_id = principal.principal_id
    report.curation_second_approver_id = None
    if scope.department_id and not scope.mixed:
        report.curation_department_id = scope.department_id
    await _record(
        db,
        principal=principal,
        report=report,
        transition="approve",
        prior_state=prior,
        reason=reason,
        revalidated=category,
        department_id=report.curation_department_id,
        extra={"approver_id": principal.principal_id},
    )
    await db.flush()
    return report


async def mark_authoritative(
    db: AsyncSession, *, principal: Principal, report: SavedReport, reason: str | None = None
) -> SavedReport:
    """``approved`` -> ``authoritative``. Requires two distinct principals."""
    if report.curation_state != CURATION_APPROVED:
        raise CurationStateError(
            "Only an approved report may be marked authoritative "
            f"(state={report.curation_state!r})"
        )
    reason = _normalize_reason(reason)
    scope = await derive_owner_scope(db, report=report, tenant_id=principal.tenant_id)
    _require_approver(principal, report, scope)

    first = report.curation_first_approver_id
    if not first:
        raise CurationStateError("No prior approver is recorded for this report")
    if first == principal.principal_id:
        raise AuthorizationError(
            "Authoritative requires two distinct authorized principals; "
            "the approving principal cannot also confirm",
            code="two_person_rule",
        )
    evidence = _evidence(report)
    ok, category = await _revalidate(db, principal=principal, evidence=evidence)
    if not ok:
        raise CurationStateError("Report sources are no longer authorized")

    prior = report.curation_state
    report.curation_state = CURATION_AUTHORITATIVE
    report.curation_reason = reason
    report.curation_second_approver_id = principal.principal_id
    await _record(
        db,
        principal=principal,
        report=report,
        transition="mark_authoritative",
        prior_state=prior,
        reason=reason,
        revalidated=category,
        department_id=report.curation_department_id,
        extra={
            "first_approver_id": first,
            "second_approver_id": principal.principal_id,
        },
    )
    await db.flush()
    return report


async def set_featured(
    db: AsyncSession,
    *,
    principal: Principal,
    report: SavedReport,
    featured: bool,
    reason: str | None = None,
) -> SavedReport:
    """Tenant-admin-only presentation flag; never changes ``curation_state``."""
    if not _is_tenant_admin(principal):
        raise AuthorizationError(
            "Only a tenant admin may feature a report",
            code="missing_permission",
        )
    reason = _normalize_reason(reason)
    if featured and report.curation_state not in FEATUREABLE_STATES:
        raise CurationStateError(
            "Only an approved or authoritative report may be featured "
            f"(state={report.curation_state!r})"
        )
    if bool(report.featured) == bool(featured):
        return report
    prior = report.curation_state
    report.featured = bool(featured)
    await _record(
        db,
        principal=principal,
        report=report,
        transition="feature" if featured else "unfeature",
        prior_state=prior,
        reason=reason,
        revalidated="n/a",
        department_id=report.curation_department_id,
        extra={"featured": bool(featured)},
    )
    await db.flush()
    return report


async def withdraw(
    db: AsyncSession, *, principal: Principal, report: SavedReport, reason: str | None = None
) -> SavedReport:
    """Any curated state -> ``none``. Owner, covering steward or tenant admin."""
    if report.curation_state == CURATION_NONE:
        return report
    reason = _normalize_reason(reason)
    scope = await derive_owner_scope(db, report=report, tenant_id=principal.tenant_id)
    if not (
        _is_owner(principal, report)
        or _is_tenant_admin(principal)
        or (not scope.mixed and _steward_covers(principal, scope))
    ):
        raise AuthorizationError(
            "Principal may not withdraw this report",
            code="missing_scope",
        )
    prior = report.curation_state
    report.curation_state = CURATION_NONE
    report.curation_reason = reason
    report.curation_first_approver_id = None
    report.curation_second_approver_id = None
    report.featured = False
    await _record(
        db,
        principal=principal,
        report=report,
        transition="withdraw",
        prior_state=prior,
        reason=reason,
        revalidated="n/a",
        department_id=scope.department_id,
    )
    await db.flush()
    return report


async def auto_demote(
    db: AsyncSession,
    *,
    report: SavedReport,
    principal: Principal,
    reason: str | None = None,
) -> SavedReport:
    """System demotion to ``none`` when the pinned sources fail revalidation.

    Live-state change matching the BV1 archive semantics: the row and its audit
    history are preserved; a report already at ``none`` is a no-op. Called under
    the evaluating principal's access, so a source-policy change demotes with no
    admin action.
    """
    if report.curation_state == CURATION_NONE:
        return report
    evidence = _evidence(report)
    ok, category = await _revalidate(db, principal=principal, evidence=evidence)
    if ok:
        return report
    prior = report.curation_state
    report.curation_state = CURATION_NONE
    report.curation_reason = (reason or "auto-demoted: pinned sources no longer authorized")[:_MAX_REASON]
    report.curation_first_approver_id = None
    report.curation_second_approver_id = None
    report.featured = False
    await _record(
        db,
        principal=principal,
        report=report,
        transition="auto_demote",
        prior_state=prior,
        reason=report.curation_reason,
        revalidated=category,
        department_id=report.curation_department_id,
    )
    await db.flush()
    return report
