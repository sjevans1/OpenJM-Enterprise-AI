"""BV6-B opt-in authoritative report candidates.

The BV6-A curation layer records *who may rely on* a saved governed snapshot.
This module is the ONLY place BV6-B touches retrieval, and it is narrow, opt-in
and additive.

Conservative additive mechanism (documented limitation)
------------------------------------------------------
The #69 plan's "preferred candidate" cannot be implemented as a true
ranking/preference signal without materially changing retrieval ranking
semantics, which the plan and Issue #45 forbid ("no ranking change for ordinary
documents"; "saving a report must not alter retrieval priority"). This module
therefore takes the CONSERVATIVE ADDITIVE interpretation:

* The ordinary governed document set is built first, unchanged.
* An eligible ``authoritative`` report may contribute **additional** evidence
  candidates that are APPENDED after the ordinary evidence. It never replaces,
  reorders, re-scores or filters an ordinary document result.
* A candidate is a bounded *reference* to the report (id, title, a reference
  passage and the count of its authorized pinned sources). It never carries a
  source title or a source snippet. The passage is built only AFTER the report's
  pinned scope has revalidated under the *requesting* principal.
* The candidate carries no query-relevance *score* and is never ranked. It
  carries only a boolean eligibility decision (below), which is not a ranking
  signal: an ineligible report is simply omitted, exactly as an unauthorized
  one is.

Eligibility (fail closed, revalidated on EVERY consideration)
-------------------------------------------------------------
An ``authoritative`` SavedReport contributes a candidate only when all hold:

1. ``curation_state == 'authoritative'`` and it is in the requesting principal's
   tenant (a foreign report is never read; there is no cross-tenant leak).
2. its pinned document/source scope currently passes the *existing*
   ``_sources_available`` predicate under the REQUESTING principal's access
   (document policy, group membership, department archival, connector gate and
   structured-table grants), so a revoked source, lost grant or closed connector
   removes the candidate with nothing to expire;
3. it is RELEVANT to the current query under the deterministic gate below;
4. its parent assistant message still exists.

``approved`` and ``featured`` are never candidates. Platform operators hold no
content-curation authority and gain nothing here.

Deterministic query-relevance gate (authority is not relevance)
--------------------------------------------------------------
Authority proves a snapshot is trustworthy, not that it answers *this* query.
An authoritative report therefore becomes a candidate only if it shares
significant terms with the current query. The rule is deliberately small,
deterministic and unit-testable; it is NOT a vector/RAG subsystem and adds no
ranking signal:

* Tokenize a string: lowercase, split on any run of non-alphanumeric characters
  (``[^a-z0-9]+``), drop tokens shorter than 3 characters, drop a small
  fixed English stopword set, and keep only the first 32 surviving tokens in
  order (a hard cap that bounds cost).
* The report's own text is its saved ``title`` plus its stored definition
  ``question_text`` (the latest ``ReportDefinitionVersion`` for that report).
* It is ELIGIBLE iff the intersection of the query's significant-token set and
  the report text's significant-token set has at least
  ``report_authoritative_candidates_min_overlap`` terms (default 1; values < 1
  fall closed to 1).
* FAIL CLOSED: an absent/blank/non-string query, a query whose token set is
  empty, or a report whose text yields no significant tokens (e.g. a malformed
  or empty title with no definition) yields NO candidate.

No model is trained or fine-tuned. Curation changes only whether a governed
snapshot may be *offered* as an extra reference.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.identity import Principal
from app.models import Message, ReportDefinitionVersion, SavedReport
from app.schemas import Evidence

# The shared source-authorization predicate and evidence parsers live with the
# reports API. Reusing them (rather than re-implementing authorization) is the
# contract: a candidate is revalidated by exactly the predicate that governs
# report open/rerun/export. There is no import cycle: app.api.reports never
# imports this module.
from app.api.reports import (
    _parse_evidence,
    _source_ids,
    _sources_available,
    _structured_tables,
)
from app.services.report_curation import CURATION_AUTHORITATIVE

settings = get_settings()

# A BV6-B candidate is an Evidence with this source_type. reports.py recognises
# it and defers its revalidation to the provenance-declared pins below.
CANDIDATE_SOURCE_TYPE = "report"
CANDIDATE_KIND = "authoritative_report"

# --- deterministic relevance gate parameters ---------------------------------
# Split on any run of characters that are not ASCII lowercase or digits (the
# input is lowercased first). Kept as a compiled pattern so tokenization is
# identical across every call and directly unit-testable.
_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")
# Tokens shorter than this are dropped (they carry little retrieval signal).
_MIN_TOKEN_LENGTH = 3
# Hard cap on how many significant tokens are considered, in order.
_MAX_TOKENS = 32
# A small, fixed English stopword set. Deterministic; never derived at runtime.
_STOPWORDS = frozenset(
    {
        "about",
        "after",
        "again",
        "against",
        "all",
        "also",
        "and",
        "any",
        "are",
        "because",
        "been",
        "before",
        "being",
        "between",
        "both",
        "but",
        "can",
        "could",
        "did",
        "does",
        "doing",
        "each",
        "few",
        "for",
        "from",
        "further",
        "had",
        "has",
        "have",
        "having",
        "her",
        "here",
        "hers",
        "him",
        "his",
        "how",
        "into",
        "its",
        "just",
        "like",
        "made",
        "make",
        "many",
        "may",
        "more",
        "most",
        "much",
        "must",
        "new",
        "not",
        "now",
        "off",
        "old",
        "once",
        "only",
        "onto",
        "other",
        "our",
        "ours",
        "out",
        "over",
        "own",
        "per",
        "same",
        "see",
        "she",
        "should",
        "some",
        "such",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "through",
        "too",
        "under",
        "until",
        "upon",
        "use",
        "used",
        "using",
        "very",
        "via",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "whom",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
        "yours",
    }
)


@dataclass(frozen=True)
class CandidateContribution:
    """One recorded candidate contribution (trace/audit shape)."""

    report_id: str
    title: str
    policy_version: str
    pinned_source_count: int


def significant_tokens(text: str | None) -> tuple[str, ...]:
    """Deterministic significant-term tokenization for the relevance gate.

    Returns an ordered, de-duplicated tuple: lowercase, split on non-
    alphanumeric runs, drop short tokens and stopwords, then cap at
    ``_MAX_TOKENS``. A non-string or blank input yields an empty tuple (fail
    closed). This is a pure function, unit-testable without any database.
    """
    if not isinstance(text, str) or not text:
        return ()
    seen: set[str] = set()
    tokens: list[str] = []
    for raw in _TOKEN_SPLIT.split(text.lower()):
        if len(raw) < _MIN_TOKEN_LENGTH:
            continue
        if raw in _STOPWORDS:
            continue
        if raw in seen:
            continue
        seen.add(raw)
        tokens.append(raw)
        if len(tokens) >= _MAX_TOKENS:
            break
    return tuple(tokens)


def report_text_relevant_to_query(
    query: str | None, report_text: str | None, *, min_overlap: int
) -> bool:
    """Deterministic boolean relevance decision. FAIL CLOSED.

    True only when both the query and the report text yield significant tokens
    AND their significant-token sets share at least ``min_overlap`` terms
    (values < 1 fall closed to 1). A malformed/absent report text or an
    unparseable/blank query yields False.
    """
    query_tokens = significant_tokens(query)
    report_tokens = significant_tokens(report_text)
    if not query_tokens or not report_tokens:
        return False
    threshold = min_overlap if min_overlap >= 1 else 1
    return len(set(query_tokens) & set(report_tokens)) >= threshold


async def _report_message_exists(db: AsyncSession, report: SavedReport) -> bool:
    """Tenant-scoped existence of the snapshot's parent assistant message.

    Deliberately NOT owner-scoped: a candidate is offered across owners within
    the tenant (its sources must be independently authorized for the requester),
    but the message must still exist. Deleting the conversation cascades to the
    report, so this is a belt-and-braces live check.
    """
    return (
        (
            await db.execute(
                select(Message.id).where(
                    Message.id == report.message_id,
                    Message.conversation_id == report.conversation_id,
                    Message.role == "assistant",
                )
            )
        )
        .scalars()
        .first()
        is not None
    )


async def _report_definition_question(db: AsyncSession, *, report_id: str) -> str:
    """The report's stored definition question text, or "" when none exists.

    Reads the latest ``ReportDefinitionVersion`` for the report. The report id
    is already tenant-scoped by the caller's query, so this lookup cannot cross
    a tenant. A missing/blank definition is not itself a failure: the report's
    own title still contributes relevance text (and if neither yields
    significant tokens the gate fails closed).
    """
    question = (
        (
            await db.execute(
                select(ReportDefinitionVersion.question_text)
                .where(ReportDefinitionVersion.report_id == report_id)
                .order_by(ReportDefinitionVersion.version.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    return question or ""


async def _authoritative_reports(
    db: AsyncSession, *, tenant_id: str
) -> list[SavedReport]:
    """Every authoritative report in one tenant, in deterministic id order."""
    rows = (
        (
            await db.execute(
                select(SavedReport)
                .where(
                    SavedReport.tenant_id == tenant_id,
                    SavedReport.curation_state == CURATION_AUTHORITATIVE,
                )
                .order_by(SavedReport.id)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


def _reference_passage(report: SavedReport, *, source_count: int) -> str:
    """Bounded reference passage: the report's own title and a source count.

    It never includes a pinned document/source title or snippet. The title here
    is the saved report's own title (tenant customer content), which the plan
    treats as the candidate's contribution; it is only ever built after the
    report revalidated for the requesting principal.
    """
    return (
        f"Governed authoritative report \"{report.title}\": "
        f"{source_count} authorized source(s), snapshot "
        f"{report.snapshot_as_of.isoformat()}."
    )


async def eligible_candidate_evidence(
    db: AsyncSession,
    *,
    principal: Principal | None,
    query: str | None,
    limit: int | None = None,
) -> tuple[list[Evidence], list[CandidateContribution]]:
    """Return (additive candidate evidence, trace records) for one request.

    Nothing is returned when the principal is absent, so an internal call path
    without a validated identity never invents candidate evidence. Nothing is
    returned when the current query yields no significant terms, so an
    unparseable query can never surface an authoritative report.
    """
    if principal is None:
        return [], []
    # FAIL CLOSED on the query: an absent/blank/unparseable query is never
    # relevant to anything, so it contributes no candidate.
    if not significant_tokens(query):
        return [], []
    max_candidates = (
        settings.report_authoritative_candidates_max if limit is None else limit
    )
    if max_candidates <= 0:
        return [], []

    evidence_out: list[Evidence] = []
    records: list[CandidateContribution] = []
    for report in await _authoritative_reports(db, tenant_id=principal.tenant_id):
        if len(evidence_out) >= max_candidates:
            break
        try:
            pinned = _parse_evidence(report.evidence_json)
            pinned_documents, pinned_sources = _source_ids(pinned)
            pinned_tables = _structured_tables(pinned)
        except HTTPException:
            # A malformed snapshot is never a candidate and reveals nothing.
            continue
        # Revalidate the pinned scope under the REQUESTING principal BEFORE any
        # title, reference or evidence is built: a failed check skips the report
        # with no title and no snippet leakage.
        if not await _sources_available(db, pinned, principal=principal):
            continue
        # Query-relevance gate: authority is not relevance. Runs AFTER source
        # authorization (so no report text is read pre-auth) and BEFORE any
        # output is built. Fails closed on malformed/absent report text.
        question = await _report_definition_question(db, report_id=report.id)
        report_text = f"{report.title}\n{question}"
        if not report_text_relevant_to_query(
            query,
            report_text,
            min_overlap=settings.report_authoritative_candidates_min_overlap,
        ):
            continue
        if not await _report_message_exists(db, report):
            continue

        source_count = len(pinned_documents) + len(pinned_sources)
        evidence_out.append(
            Evidence(
                source_type=CANDIDATE_SOURCE_TYPE,
                source_id=report.id,
                title=report.title,
                passage=_reference_passage(report, source_count=source_count),
                score=None,
                provenance={
                    "candidate_kind": CANDIDATE_KIND,
                    "curation_state": report.curation_state,
                    "tenant_id": principal.tenant_id,
                    "policy_version": settings.report_authoritative_candidates_policy_version,
                    # Bounded, authorized pins. reports.py revalidates a saved
                    # snapshot that cites a candidate against exactly these.
                    "pinned_document_ids": sorted(pinned_documents),
                    "pinned_source_tables": {
                        source_id: sorted(pinned_tables.get(source_id, set()))
                        for source_id in sorted(pinned_sources)
                    },
                },
                processing_location="local",
            )
        )
        records.append(
            CandidateContribution(
                report_id=report.id,
                title=report.title,
                policy_version=settings.report_authoritative_candidates_policy_version,
                pinned_source_count=source_count,
            )
        )
    return evidence_out, records


def contribution_records(
    records: list[CandidateContribution],
) -> list[dict]:
    """JSON-serializable trace/audit form of candidate contributions."""
    return [
        {
            "report_id": record.report_id,
            "title": record.title,
            "candidate_kind": CANDIDATE_KIND,
            "policy_version": record.policy_version,
            "pinned_source_count": record.pinned_source_count,
        }
        for record in records
    ]
