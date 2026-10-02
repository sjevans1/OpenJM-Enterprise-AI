"""VS4-B2A: read-only, source-bound report definition **records**.

There is intentionally no execution endpoint. These immutable version-1 records
are NOT executable plans, stored SQL or grants. VS4-B2B must thread scope into
knowledge retrieval and structured SQL planning BEFORE VS4-B2C may run reports.
"""
import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.reports import (
    _available,
    _owned_report,
    _parse_evidence,
    _source_ids,
    _structured_tables,
    preview_report_rerun,
)
from app.core.config import get_settings
from app.db import get_db
from app.models import DataSource, ReportDefinitionVersion, SavedReport
from app.schemas import (
    CreateReportDefinitionRequest,
    Evidence,
    ReportDefinitionVersionOut,
)
from app.services.data_sources import decode_schema
from app.services.report_scope import ReportSourceScope, ReportScopeError

settings = get_settings()
router = APIRouter(tags=["report-definitions"])
MAX_PINNED_DOCUMENTS = 32
MAX_PINNED_SOURCES = 8
MAX_PINNED_TABLES = 32
MAX_SERIALIZED_PINS_BYTES = 12000


def _pins_from_evidence(evidence: list[Evidence]) -> tuple[list[str], dict[str, list[str]]]:
    """Extract the exact source scope from trusted, persisted evidence."""
    document_ids, source_ids = _source_ids(evidence)
    by_source = _structured_tables(evidence)
    if (
        len(document_ids) > MAX_PINNED_DOCUMENTS
        or len(source_ids) > MAX_PINNED_SOURCES
        or sum(map(len, by_source.values())) > MAX_PINNED_TABLES
        or set(by_source) != source_ids
        or any(not tables for tables in by_source.values())
    ):
        raise HTTPException(status_code=422, detail="Report source scope is not bounded")
    return (
        sorted(document_ids),
        {source_id: sorted(by_source[source_id]) for source_id in sorted(source_ids)},
    )


async def _schema_scope_current(
    db: AsyncSession,
    source_tables: dict[str, list[str]],
) -> bool:
    """Current discovered schema must still contain every pinned table."""
    if not source_tables:
        return True
    sources = (
        await db.execute(
            select(DataSource).where(
                DataSource.id.in_(list(source_tables)),
                DataSource.user_id == settings.dev_user_id,
                DataSource.enabled.is_(True),
                DataSource.status == "connected",
            )
        )
    ).scalars().all()
    if len(sources) != len(source_tables):
        return False
    for source in sources:
        # _available() independently enforces authorized_objects_json;
        # do not equate an old authorization list to the current schema.
        decoded = decode_schema(source.schema_json)
        discovered = {
            identifier.lower()
            for table in decoded
            for identifier in (table.name, table.qualified_name)
        }
        if not discovered or not set(source_tables[source.id]).issubset(discovered):
            return False
    return True


async def _owned_available_report(db: AsyncSession, report_id: str) -> SavedReport:
    report = await _owned_report(db, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Saved report not found")
    if not await _available(db, report):
        raise HTTPException(status_code=409, detail="Report source is no longer authorized")
    return report


async def _current_scope(
    db: AsyncSession, report: SavedReport
) -> tuple[list[str], dict[str, list[str]]]:
    pins = _pins_from_evidence(_parse_evidence(report.evidence_json))
    if not await _schema_scope_current(db, pins[1]):
        raise HTTPException(status_code=409, detail="Pinned table is no longer available")
    return pins


def _stored_scope(row: ReportDefinitionVersion) -> tuple[list[str], dict[str, list[str]]]:
    """Treat stored definition scope as untrusted; reject tampering/corruption."""
    try:
        if (
            len(row.pinned_document_ids_json.encode("utf-8")) > MAX_SERIALIZED_PINS_BYTES
            or len(row.pinned_source_tables_json.encode("utf-8")) > MAX_SERIALIZED_PINS_BYTES
        ):
            raise ValueError("exceeded scope bound")
        docs = json.loads(row.pinned_document_ids_json)
        sources = json.loads(row.pinned_source_tables_json)
        if (
            not isinstance(docs, list)
            or len(docs) > MAX_PINNED_DOCUMENTS
            or any(not isinstance(item, str) or not item for item in docs)
            or docs != sorted(set(docs))
            or not isinstance(sources, dict)
            or len(sources) > MAX_PINNED_SOURCES
        ):
            raise ValueError("invalid scope types")
        for identifier, table_names in sources.items():
            if (
                not isinstance(identifier, str)
                or not identifier
                or not isinstance(table_names, list)
                or not (1 <= len(table_names) <= MAX_PINNED_TABLES)
                or any(not isinstance(table, str) or not table for table in table_names)
                or table_names != sorted(set(table_names))
            ):
                raise ValueError("invalid table scope")
        if sum(len(tables) for tables in sources.values()) > MAX_PINNED_TABLES:
            raise ValueError("too many scoped tables")
    except (ValueError, TypeError):
        raise HTTPException(status_code=409, detail="Saved definition is invalid") from None
    return docs, sources


async def _definition_out(
    db: AsyncSession, report: SavedReport, definition: ReportDefinitionVersion
) -> ReportDefinitionVersionOut:
    current_docs, current_tables = await _current_scope(db, report)
    stored_docs, stored_tables = _stored_scope(definition)
    if stored_docs != current_docs or stored_tables != current_tables:
        raise HTTPException(status_code=409, detail="Report scope no longer matches definition")
    # Strict historical transcript/mode check; never silently broaden sources.
    original = await preview_report_rerun(report.id, db)
    if (
        original.original_question != definition.question_text
        or original.mode != definition.requested_mode
        or definition.user_id != settings.dev_user_id
        or definition.report_id != report.id
        or definition.version != 1
    ):
        raise HTTPException(status_code=409, detail="Saved definition no longer matches its origin")
    return ReportDefinitionVersionOut(
        id=definition.id,
        report_id=report.id,
        version=definition.version,
        question=definition.question_text,
        mode=definition.requested_mode,
        pinned_document_ids=stored_docs,
        pinned_source_tables=stored_tables,
        created_at=definition.created_at,
        executes_queries=False,
        runnable=False,
    )


@router.post(
    "/reports/{report_id}/definitions",
    response_model=ReportDefinitionVersionOut,
    status_code=201,
)
async def create_definition(
    report_id: str,
    request: CreateReportDefinitionRequest,
    db: AsyncSession = Depends(get_db),
):
    """Idempotently register v1 from an owned, still-governed report only.

    Request has NO arbitrary question, source, table or executable SQL fields.
    """
    report = await _owned_available_report(db, report_id)
    original = await preview_report_rerun(report_id, db)
    document_ids, source_tables = await _current_scope(db, report)
    if not document_ids and not source_tables:
        raise HTTPException(status_code=422, detail="Definition requires report sources")

    previous = (
        await db.execute(
            select(ReportDefinitionVersion).where(
                ReportDefinitionVersion.report_id == report.id,
                ReportDefinitionVersion.user_id == settings.dev_user_id,
                ReportDefinitionVersion.version == 1,
            )
        )
    ).scalars().first()
    if previous is not None:
        return await _definition_out(db, report, previous)

    row = ReportDefinitionVersion(
        user_id=settings.dev_user_id,
        report_id=report.id,
        version=1,
        question_text=original.original_question,
        requested_mode=original.mode,
        pinned_document_ids_json=json.dumps(document_ids),
        pinned_source_tables_json=json.dumps(source_tables, sort_keys=True),
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = (
            await db.execute(
                select(ReportDefinitionVersion).where(
                    ReportDefinitionVersion.report_id == report.id,
                    ReportDefinitionVersion.user_id == settings.dev_user_id,
                    ReportDefinitionVersion.version == 1,
                )
            )
        ).scalars().first()
        if existing is None:
            raise HTTPException(status_code=409, detail="Definition save conflict") from None
        return await _definition_out(db, report, existing)
    await db.refresh(row)
    return await _definition_out(db, report, row)


@router.get(
    "/reports/{report_id}/definitions",
    response_model=list[ReportDefinitionVersionOut],
)
async def list_definitions(
    report_id: str,
    db: AsyncSession = Depends(get_db),
):
    report = await _owned_available_report(db, report_id)
    versions = (
        await db.execute(
            select(ReportDefinitionVersion)
            .where(
                ReportDefinitionVersion.report_id == report.id,
                ReportDefinitionVersion.user_id == settings.dev_user_id,
            )
            .order_by(ReportDefinitionVersion.version.desc())
            .limit(20)
        )
    ).scalars().all()
    return [await _definition_out(db, report, row) for row in versions]


@router.get(
    "/reports/{report_id}/definitions/{version}",
    response_model=ReportDefinitionVersionOut,
)
async def get_definition(
    report_id: str,
    version: int,
    db: AsyncSession = Depends(get_db),
):
    report = await _owned_available_report(db, report_id)
    result = (
        await db.execute(
            select(ReportDefinitionVersion).where(
                ReportDefinitionVersion.report_id == report.id,
                ReportDefinitionVersion.user_id == settings.dev_user_id,
                ReportDefinitionVersion.version == version,
            )
        )
    ).scalars().first()
    if result is None:
        raise HTTPException(status_code=404, detail="Report definition not found")
    return await _definition_out(db, report, result)


async def load_validated_definition_scope(
    db: AsyncSession, report_id: str, version: int, user_id: str
) -> tuple[str, str, ReportSourceScope]:
    """Internal future-B2C gateway: never trust pins from browser/old Evidence.

    Re-checks actual owner, current source/table availability, stored immutable
    scope, historical question and requested mode on every load. Does not run
    SQL, retrieval or model calls. Not a public execution endpoint.
    """
    if user_id != settings.dev_user_id:
        raise HTTPException(status_code=404, detail="Report definition not found")
    report = await _owned_available_report(db, report_id)
    definition = (
        await db.execute(
            select(ReportDefinitionVersion).where(
                ReportDefinitionVersion.report_id == report_id,
                ReportDefinitionVersion.user_id == user_id,
                ReportDefinitionVersion.version == version,
            )
        )
    ).scalars().first()
    if definition is None:
        raise HTTPException(status_code=404, detail="Report definition not found")
    verified = await _definition_out(db, report, definition)
    try:
        scope = ReportSourceScope.from_pins(
            verified.pinned_document_ids, verified.pinned_source_tables
        )
    except ReportScopeError:
        raise HTTPException(status_code=409, detail="Saved definition scope is invalid") from None
    return verified.question, verified.mode, scope
