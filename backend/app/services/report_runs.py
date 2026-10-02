"""VS4-B2C1: internal report-run reservation and finalization logic.

This module is **not** a public endpoint. It provides the internal reserve
and finalize helpers exercised directly by tests. B2C1 introduces no
run-submission API surface; execution is reserved for B2C2.

Security invariants:
- Intent and identity are immutable after reservation; finalization is a single
  compare-and-set UPDATE guarded by status and fingerprint.
- No model, retrieval, SQL-engine, credential, or trace-writing calls are made
  here.
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from uuid import UUID

from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import ReportDefinitionVersion, ReportRun

settings = get_settings()

RUN_DEADLINE_SECONDS = 1800
MAX_ANSWER_CHARS = 24000
MAX_ANSWER_BYTES = 65536
MAX_EVIDENCE_ITEMS = 24
MAX_EVIDENCE_BYTES = 65536
MAX_RESULT_BYTES = 131072

ALLOWED_FAILURE_CATEGORIES = frozenset(
    {
        "authorization",
        "source_unavailable",
        "invalid_definition",
        "planning",
        "retrieval",
        "structured_query",
        "model",
        "incomplete_result",
        "budget_exceeded",
        "result_too_large",
        "internal",
        "deadline_expired",
    }
)


class ReportRunConflict(RuntimeError):
    """A reservation token collides with a different intent."""


def _canonical_key(value: str) -> str:
    """Return the canonical UUID string or raise ValueError.

    Rejects uppercase, braces, compact form, whitespace, and any non-UUID input
    so the key is deterministic across callers and databases.
    """
    return str(UUID(value))


def _fingerprint(
    user_id: str,
    report_id: str,
    definition_id: str,
    definition_version: int,
    requested_mode: str,
    question: str,
    pinned_document_ids: list[str],
    pinned_source_tables: dict[str, list[str]],
) -> str:
    """Stable SHA-256 over canonical trusted intent. Never uses hash()."""
    payload = {
        "user_id": user_id,
        "report_id": report_id,
        "definition_id": definition_id,
        "definition_version": definition_version,
        "requested_mode": requested_mode,
        "question": question or "",
        "pinned_document_ids": sorted(pinned_document_ids),
        "pinned_source_tables": {
            k: sorted(v) for k, v in sorted(pinned_source_tables.items())
        },
    }
    return hashlib.sha256(
        json.dumps(payload, separators=(",", ":"), sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _validate_result(
    answer: str | None,
    evidence: list[dict] | None,
    structured_result: dict | None,
    trace_ids: list[str] | None,
) -> None:
    """Enforce B2C1 payload bounds before persistence. Raises ValueError."""
    answer = answer or ""
    evidence = evidence or []
    structured_result = structured_result or {}
    trace_ids = trace_ids or []

    if len(answer) > MAX_ANSWER_CHARS:
        raise ValueError("answer exceeds character bound")
    if len(answer.encode("utf-8")) > MAX_ANSWER_BYTES:
        raise ValueError("answer exceeds byte bound")
    if not (1 <= len(evidence) <= MAX_EVIDENCE_ITEMS):
        raise ValueError("evidence count out of bounds")
    evidence_bytes = len(
        json.dumps(evidence, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    )
    if evidence_bytes > MAX_EVIDENCE_BYTES:
        raise ValueError("evidence exceeds byte bound")
    aggregate = {
        "answer": answer,
        "evidence": evidence,
        "structured_result": structured_result,
        "trace_ids": trace_ids,
    }
    aggregate_bytes = len(
        json.dumps(aggregate, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    )
    if aggregate_bytes > MAX_RESULT_BYTES:
        raise ValueError("result aggregate exceeds byte bound")
    for trace_id in trace_ids:
        if not isinstance(trace_id, str) or not trace_id.strip():
            raise ValueError("invalid trace id")


async def reserve_report_run(
    *,
    db: AsyncSession,
    report_id: str,
    definition_version: int,
    idempotency_key: str,
    requested_mode: str,
    question: str,
    pinned_document_ids: list[str],
    pinned_source_tables: dict[str, list[str]],
    now: datetime | Callable[[], datetime] | None = None,
) -> tuple[ReportRun, bool]:
    """Reserve a run row for future execution.

    Returns (run, owns_execution). The caller may execute only when
    owns_execution is True. Idempotent: the same key + intent returns the
    existing row with owns_execution=False. Same key + different intent raises
    ReportRunConflict. Concurrent callers resolve through the database unique
    constraint; exactly one wins.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    else:
        now = now() if callable(now) else now
    canonical_key = _canonical_key(idempotency_key)

    # Resolve the trusted definition identity from the source of truth; never
    # trust caller-supplied pins for identity binding.
    scoped = (
        await db.execute(
            select(ReportDefinitionVersion).where(
                ReportDefinitionVersion.report_id == report_id,
                ReportDefinitionVersion.version == definition_version,
            )
        )
    ).scalar_one_or_none()
    if scoped is None:
        raise ReportRunConflict("Definition not found")

    fingerprint = _fingerprint(
        settings.dev_user_id,
        report_id,
        scoped.id,
        definition_version,
        requested_mode,
        question,
        pinned_document_ids,
        pinned_source_tables,
    )

    deadline = now + timedelta(seconds=RUN_DEADLINE_SECONDS)
    insert_stmt = insert(ReportRun).values(
        user_id=settings.dev_user_id,
        report_id=report_id,
        definition_id=scoped.id,
        definition_version=definition_version,
        requested_mode=requested_mode,
        idempotency_key=canonical_key,
        request_fingerprint=fingerprint,
        status="running",
        started_at=now,
        deadline_at=deadline,
        finished_at=None,
        failure_category=None,
        trace_ids_json=None,
        result_json=None,
        result_size_bytes=None,
        result_sha256=None,
    )
    try:
        result = await db.execute(insert_stmt.returning(ReportRun.id))
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = (
            await db.execute(
                select(ReportRun).where(
                    ReportRun.user_id == settings.dev_user_id,
                    ReportRun.idempotency_key == canonical_key,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            raise ReportRunConflict("Reservation conflict") from None
        if existing.request_fingerprint != fingerprint:
            raise ReportRunConflict("Same key, different intent")
        return existing, False
    run_id = result.scalar_one()
    run = await db.get(ReportRun, run_id)
    return run, True


async def finalize_success(
    *,
    db: AsyncSession,
    run_id: str,
    user_id: str,
    fingerprint: str,
    answer: str,
    evidence: list[dict[str, Any]],
    structured_result: dict[str, Any] | None,
    trace_ids: list[str] | None,
) -> int:
    """Atomically finalize a run as succeeded. Returns affected row count."""
    try:
        _validate_result(answer, evidence, structured_result, trace_ids)
    except ValueError:
        return 0
    aggregate = {
        "answer": answer,
        "evidence": evidence,
        "structured_result": structured_result or {},
        "trace_ids": trace_ids or [],
    }
    result_json = json.dumps(aggregate, separators=(",", ":"), ensure_ascii=False)
    result_bytes = len(result_json.encode("utf-8"))
    result_sha = hashlib.sha256(result_json.encode("utf-8")).hexdigest()
    result = await db.execute(
        update(ReportRun)
        .where(
            ReportRun.id == run_id,
            ReportRun.user_id == user_id,
            ReportRun.status == "running",
            ReportRun.request_fingerprint == fingerprint,
            ReportRun.finished_at.is_(None),
        )
        .values(
            status="succeeded",
            finished_at=datetime.now(timezone.utc),
            trace_ids_json=json.dumps(trace_ids or [], separators=(",", ":")),
            result_json=result_json,
            result_size_bytes=result_bytes,
            result_sha256=result_sha,
        )
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    return result.rowcount


async def finalize_failure(
    *,
    db: AsyncSession,
    run_id: str,
    user_id: str,
    fingerprint: str,
    failure_category: str,
) -> int:
    """Atomically finalize a run as failed. Returns affected row count."""
    if failure_category not in ALLOWED_FAILURE_CATEGORIES:
        return 0
    result = await db.execute(
        update(ReportRun)
        .where(
            ReportRun.id == run_id,
            ReportRun.user_id == user_id,
            ReportRun.status == "running",
            ReportRun.request_fingerprint == fingerprint,
            ReportRun.finished_at.is_(None),
        )
        .values(
            status="failed",
            finished_at=datetime.now(timezone.utc),
            failure_category=failure_category,
        )
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    return result.rowcount


async def interrupt_expired_runs(*, db: AsyncSession, now: datetime | Callable[[], datetime] | None = None) -> int:
    """Mark expired running runs as interrupted. Returns affected row count."""
    if now is None:
        now = datetime.now(timezone.utc)
    result = await db.execute(
        update(ReportRun)
        .where(
            ReportRun.status == "running",
            ReportRun.deadline_at <= now,
            ReportRun.finished_at.is_(None),
        )
        .values(
            status="interrupted",
            finished_at=now,
            failure_category="deadline_expired",
        )
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    return result.rowcount
