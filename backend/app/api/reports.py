"""VS4-A: immutable, permission-checked saved Evidence snapshots.

This is NOT a report execution engine. No LLM, RAG retrieval, SQL planning,
SQL execution, scheduled job or Workspace dependency is used by this module.
The current product still has a trusted single-development-user context.
"""
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db import get_db
from app.models import Conversation, DataSource, Document, Message, SavedReport
from app.schemas import (
    Evidence,
    SaveReportRequest,
    SavedReportDetail,
    SavedReportSummary,
)

router = APIRouter(prefix="/reports", tags=["reports"])
settings = get_settings()

MAX_EVIDENCE = 24
MAX_SNAPSHOT_BYTES = 65536
MAX_ANSWER_CHARS = 24000
ALLOWED_EVIDENCE_TYPES = {"document", "structured_query"}
ALLOWED_EXECUTION_CLASSES = {"knowledge", "structured", "hybrid"}


def _parse_evidence(raw: str | None) -> list[Evidence]:
    """Validate server-persisted evidence; malformed/oversized values fail closed."""
    if not raw or len(raw.encode("utf-8")) > MAX_SNAPSHOT_BYTES:
        raise HTTPException(status_code=422, detail="No bounded report evidence")
    try:
        payload = json.loads(raw)
        if not isinstance(payload, list) or not (1 <= len(payload) <= MAX_EVIDENCE):
            raise ValueError("unsupported evidence count")
        evidence = [Evidence.model_validate(item) for item in payload]
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail="No valid report evidence") from None
    if any(
        item.source_type not in ALLOWED_EVIDENCE_TYPES or not item.source_id
        for item in evidence
    ):
        raise HTTPException(status_code=422, detail="Unsupported report evidence")
    return evidence


def _source_ids(evidence: list[Evidence]) -> tuple[set[str], set[str]]:
    """Include equivalent document sources used by evidence deduplication."""
    documents: set[str] = set()
    sources: set[str] = set()
    for item in evidence:
        if item.source_type == "structured_query":
            sources.add(item.source_id)
            # Dependent-Hybrid SQL Evidence references the policy document used
            # to derive its predicate. Revocation of that policy must invalidate
            # the snapshot even if its original DOC Evidence was omitted.
            parameter = item.provenance.get("grounded_parameter")
            if parameter is not None:
                if not isinstance(parameter, dict):
                    raise HTTPException(status_code=422, detail="Invalid policy provenance")
                policy_source = parameter.get("source_id")
                if not isinstance(policy_source, str) or not policy_source:
                    raise HTTPException(status_code=422, detail="Invalid policy provenance")
                documents.add(policy_source)
            continue
        documents.add(item.source_id)
        equivalent = item.provenance.get("equivalent_sources", [])
        if not isinstance(equivalent, list) or len(equivalent) > MAX_EVIDENCE:
            raise HTTPException(status_code=422, detail="Invalid evidence provenance")
        for ref in equivalent:
            if not isinstance(ref, dict):
                raise HTTPException(status_code=422, detail="Invalid evidence provenance")
            identifier = ref.get("source_id")
            if not isinstance(identifier, str) or not identifier:
                raise HTTPException(status_code=422, detail="Invalid evidence provenance")
            documents.add(identifier)
    return documents, sources


async def _sources_available(db: AsyncSession, evidence: list[Evidence]) -> bool:
    document_ids, source_ids = _source_ids(evidence)
    user_id = settings.dev_user_id  # Replace only via VS5 trusted identity context.
    if document_ids:
        documents = (
            await db.execute(
                select(Document.id).where(
                    Document.id.in_(document_ids),
                    Document.user_id == user_id,
                    Document.status == "ready",
                    Document.indexed.is_(True),
                )
            )
        ).scalars().all()
        if set(documents) != document_ids:
            return False
    if source_ids:
        sources = (
            await db.execute(
                select(DataSource.id).where(
                    DataSource.id.in_(source_ids),
                    DataSource.user_id == user_id,
                    DataSource.status == "connected",
                    DataSource.enabled.is_(True),
                    DataSource.schema_json.is_not(None),
                )
            )
        ).scalars().all()
        if set(sources) != source_ids:
            return False
    return True


async def _owned_report(db: AsyncSession, report_id: str) -> SavedReport | None:
    return (
        await db.execute(
            select(SavedReport).where(
                SavedReport.id == report_id,
                SavedReport.user_id == settings.dev_user_id,
            )
        )
    ).scalars().first()


async def _source_message_exists(db: AsyncSession, report: SavedReport) -> bool:
    return (
        await db.execute(
            select(Message.id)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(
                Message.id == report.message_id,
                Message.conversation_id == report.conversation_id,
                Message.role == "assistant",
                Conversation.user_id == settings.dev_user_id,
            )
        )
    ).scalars().first() is not None


async def _available(db: AsyncSession, report: SavedReport) -> bool:
    if not await _source_message_exists(db, report):
        return False
    try:
        return await _sources_available(db, _parse_evidence(report.evidence_json))
    except HTTPException:
        return False


def _summary(report: SavedReport, available: bool) -> SavedReportSummary:
    # Do not reveal a revoked document's old title through listing metadata.
    return SavedReportSummary(
        id=report.id,
        title=report.title if available else "Unavailable saved report",
        conversation_id=report.conversation_id,
        message_id=report.message_id,
        execution_class=report.execution_class,
        snapshot_as_of=report.snapshot_as_of,
        created_at=report.created_at,
        source_count=report.source_count if available else 0,
        available=available,
    )


async def _detail(db: AsyncSession, report: SavedReport) -> SavedReportDetail:
    if not await _available(db, report):
        raise HTTPException(
            status_code=409,
            detail="Saved report source is no longer available or authorized",
        )
    summary = _summary(report, True)
    return SavedReportDetail(
        **summary.model_dump(),
        answer=report.answer_text,
        evidence=_parse_evidence(report.evidence_json),
        is_live=False,
    )


@router.post("", response_model=SavedReportDetail, status_code=201)
async def create_report(
    payload: SaveReportRequest,
    db: AsyncSession = Depends(get_db),
):
    """Save the actual persisted assistant response, never client-supplied data."""
    message = (
        await db.execute(
            select(Message)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(
                Message.id == payload.message_id,
                Conversation.user_id == settings.dev_user_id,
                Message.role == "assistant",
                Message.execution_class.in_(ALLOWED_EXECUTION_CLASSES),
            )
        )
    ).scalars().first()
    if message is None:
        raise HTTPException(status_code=404, detail="Message not available")

    prior = (
        await db.execute(
            select(SavedReport).where(
                SavedReport.user_id == settings.dev_user_id,
                SavedReport.message_id == message.id,
            )
        )
    ).scalars().first()
    if prior is not None:
        return await _detail(db, prior)

    if payload.title is not None and not payload.title.strip():
        raise HTTPException(status_code=422, detail="Report title must not be blank")
    if not message.content or len(message.content) > MAX_ANSWER_CHARS or len(message.content.encode("utf-8")) > MAX_SNAPSHOT_BYTES:
        raise HTTPException(status_code=422, detail="Answer is not a bounded report")
    evidence = _parse_evidence(message.evidence_json)
    if not await _sources_available(db, evidence):
        raise HTTPException(
            status_code=409,
            detail="Report sources are no longer available or authorized",
        )

    document_ids, data_source_ids = _source_ids(evidence)
    report = SavedReport(
        user_id=settings.dev_user_id,
        conversation_id=message.conversation_id,
        message_id=message.id,
        title=payload.title.strip() if payload.title else "Saved report",
        answer_text=message.content,
        evidence_json=json.dumps(
            [item.model_dump(mode="json") for item in evidence],
            separators=(",", ":"),
        ),
        execution_class=message.execution_class,
        requested_mode=message.requested_mode,
        source_count=len(document_ids) + len(data_source_ids),
        snapshot_as_of=message.created_at,
    )
    db.add(report)
    try:
        await db.commit()
    except IntegrityError:
        # The unique owner/message constraint makes competing saves idempotent.
        await db.rollback()
        prior = (
            await db.execute(
                select(SavedReport).where(
                    SavedReport.user_id == settings.dev_user_id,
                    SavedReport.message_id == message.id,
                )
            )
        ).scalars().first()
        if prior is None:
            raise HTTPException(status_code=409, detail="Report save conflict") from None
        return await _detail(db, prior)
    await db.refresh(report)
    return await _detail(db, report)


@router.get("", response_model=list[SavedReportSummary])
async def list_reports(
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0, le=100000),
    db: AsyncSession = Depends(get_db),
):
    reports = (
        await db.execute(
            select(SavedReport)
            .where(SavedReport.user_id == settings.dev_user_id)
            .order_by(SavedReport.created_at.desc(), SavedReport.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    return [_summary(report, await _available(db, report)) for report in reports]


@router.get("/{report_id}", response_model=SavedReportDetail)
async def get_report(report_id: str, db: AsyncSession = Depends(get_db)):
    report = await _owned_report(db, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Saved report not found")
    return await _detail(db, report)


@router.delete("/{report_id}", status_code=204)
async def delete_report(report_id: str, db: AsyncSession = Depends(get_db)):
    """Idempotent owner-scoped deletion affects the snapshot alone."""
    report = await _owned_report(db, report_id)
    if report is not None:
        await db.delete(report)
        await db.commit()
    return Response(status_code=204)
