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
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.context import current_principal
from app.models import ReportDefinitionVersion, ReportRun, SavedReport, new_id

settings = get_settings()

RUN_DEADLINE_SECONDS = 600
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
        "revoked",
    }
)


class ReportRunConflict(RuntimeError):
    """A reservation token collides with a different intent."""


class BudgetExceeded(RuntimeError):
    """A per-run budget limit (model attempts, SQL, knowledge, wall clock)
    was reached.  Raised only on the report-execution path when a budget
    context is supplied; ordinary Chat passes ``budget=None`` and never
    raises this."""


from dataclasses import dataclass, field
from typing import Optional as _Optional


@dataclass
class ReportRunBudget:
    """Explicit, opt-in per-run budget/limit context for the report path.

    Passed explicitly through ``_execute_owned_run`` → ``orchestrator.plan``
    → model gateway / tools.  When ``None`` (ordinary Chat) no limits are
    enforced and no global mutable state is touched.
    """

    started_at: datetime
    max_model_http_attempts: int = 8
    max_output_tokens: int = 2048
    max_sql_executions: int = 2
    max_knowledge_retrievals: int = 2
    wall_deadline_seconds: int = RUN_DEADLINE_SECONDS
    # live counters (mutated only on the report path)
    model_attempts: int = field(default=0)
    sql_executions: int = field(default=0)
    knowledge_retrievals: int = field(default=0)

    def count_model(self) -> None:
        self.check_wall(datetime.now(timezone.utc))
        self.model_attempts += 1
        if self.model_attempts > self.max_model_http_attempts:
            raise BudgetExceeded(
                f"model HTTP attempts ({self.model_attempts}) exceed "
                f"limit ({self.max_model_http_attempts})"
            )

    def count_sql(self) -> None:
        self.check_wall(datetime.now(timezone.utc))
        self.sql_executions += 1
        if self.sql_executions > self.max_sql_executions:
            raise BudgetExceeded(
                f"SQL executions ({self.sql_executions}) exceed "
                f"limit ({self.max_sql_executions})"
            )

    def count_knowledge(self) -> None:
        self.check_wall(datetime.now(timezone.utc))
        self.knowledge_retrievals += 1
        if self.knowledge_retrievals > self.max_knowledge_retrievals:
            raise BudgetExceeded(
                f"knowledge retrievals ({self.knowledge_retrievals}) exceed "
                f"limit ({self.max_knowledge_retrievals})"
            )

    def check_wall(self, now: datetime) -> None:
        elapsed = (now - self.started_at).total_seconds()
        if elapsed > self.wall_deadline_seconds:
            raise BudgetExceeded(
                f"wall-clock deadline exceeded ({elapsed:.1f}s > "
                f"{self.wall_deadline_seconds}s)"
            )

    def remaining_seconds(self, now: datetime | None = None) -> float:
        current = now or datetime.now(timezone.utc)
        elapsed = (current - self.started_at).total_seconds()
        remaining = self.wall_deadline_seconds - elapsed
        if remaining <= 0:
            raise BudgetExceeded(
                f"wall-clock deadline exceeded ({elapsed:.1f}s >= "
                f"{self.wall_deadline_seconds}s)"
            )
        return remaining

    def enforce_max_tokens(self, max_tokens: _Optional[int]) -> _Optional[int]:
        if max_tokens is None:
            return self.max_output_tokens
        return min(max_tokens, self.max_output_tokens)

    def is_exhausted(self) -> bool:
        return (
            self.model_attempts >= self.max_model_http_attempts
            or self.sql_executions >= self.max_sql_executions
            or self.knowledge_retrievals >= self.max_knowledge_retrievals
        )


_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _canonical_key(value: str) -> str:
    """Return the canonical UUID string or raise ValueError.

    Accepts ONLY the canonical lowercase hyphenated form: rejects braces,
    urn: prefix, compact form, uppercase, surrounding whitespace and any
    non-UUID input so the key is deterministic across callers and databases.
    """
    if not isinstance(value, str) or not _UUID_RE.fullmatch(value):
        raise ValueError("idempotency key must be a canonical lowercase UUID")
    return value


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
    # trust caller-supplied pins for identity binding. The definition AND its
    # parent report must belong to the current user, and caller-supplied
    # intent must exactly match the immutable stored definition.
    scoped = (
        await db.execute(
            select(ReportDefinitionVersion).where(
                ReportDefinitionVersion.report_id == report_id,
                ReportDefinitionVersion.version == definition_version,
                ReportDefinitionVersion.user_id == current_principal().user_id,
            )
        )
    ).scalar_one_or_none()
    if scoped is None:
        raise ReportRunConflict("Definition not found")
    owner_report = (
        await db.execute(
            select(SavedReport.id).where(
                SavedReport.id == report_id,
                SavedReport.user_id == current_principal().user_id,
            )
        )
    ).scalar_one_or_none()
    if owner_report is None:
        raise ReportRunConflict("Report not found")
    try:
        stored_docs = json.loads(scoped.pinned_document_ids_json)
        stored_tables = json.loads(scoped.pinned_source_tables_json)
    except (TypeError, ValueError):
        raise ReportRunConflict("Stored definition scope is unreadable") from None
    if (
        requested_mode != scoped.requested_mode
        or (question or "") != scoped.question_text
        or sorted(pinned_document_ids or []) != sorted(stored_docs or [])
        or {
            key: sorted(value)
            for key, value in (pinned_source_tables or {}).items()
        }
        != {
            key: sorted(value)
            for key, value in (stored_tables or {}).items()
        }
    ):
        raise ReportRunConflict("Caller intent does not match the stored definition")

    fingerprint = _fingerprint(
        current_principal().user_id,
        report_id,
        scoped.id,
        definition_version,
        requested_mode,
        question,
        pinned_document_ids,
        pinned_source_tables,
    )

    deadline = now + timedelta(seconds=RUN_DEADLINE_SECONDS)
    scoped_def_id = scoped.id
    run_id = new_id()
    insert_stmt = insert(ReportRun).values(
        id=run_id,
        user_id=current_principal().user_id,
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
        await db.execute(insert_stmt)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = (
            await db.execute(
                select(ReportRun).where(
                    ReportRun.user_id == current_principal().user_id,
                    ReportRun.idempotency_key == canonical_key,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            # The unique constraint that fired is NOT the idempotency-key
            # constraint (uq_report_run_owner_key).  It is the one-active-run
            # partial unique index (uq_report_run_active_per_definition):
            # a *different* idempotency key was submitted while a run for
            # this exact definition is still 'running'.
            active = (
                await db.execute(
                    select(ReportRun).where(
                        ReportRun.user_id == current_principal().user_id,
                        ReportRun.report_id == report_id,
                        ReportRun.definition_id == scoped_def_id,
                        ReportRun.definition_version == definition_version,
                        ReportRun.status == "running",
                        ReportRun.finished_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if active is not None:
                raise ReportRunConflict(
                    "A run for this definition is already in progress"
                ) from None
            raise ReportRunConflict("Reservation conflict") from None
        if existing.request_fingerprint != fingerprint:
            raise ReportRunConflict("Same key, different intent")
        return existing, False
    run = await db.get(ReportRun, run_id)
    # SQLite DateTime(timezone=True) may round-trip as naive; restore the
    # authoritative tz-aware started_at that was used for the INSERT.
    if run.started_at is None or run.started_at.tzinfo is None:
        run.started_at = now
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
    finished_at = datetime.now(timezone.utc)
    result = await db.execute(
        update(ReportRun)
        .where(
            ReportRun.id == run_id,
            ReportRun.user_id == user_id,
            ReportRun.status == "running",
            ReportRun.request_fingerprint == fingerprint,
            ReportRun.finished_at.is_(None),
            ReportRun.deadline_at > finished_at,
        )
        .values(
            status="succeeded",
            finished_at=finished_at,
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


# ---------------------------------------------------------------------------
# B2C1 history (owner-scoped, read-only) and revocation controls
# ---------------------------------------------------------------------------

MAX_LIST_LIMIT = 50
MAX_LIST_OFFSET = 100_000


def _parse_trace_ids(raw: str | None) -> list[str]:
    """Decode the stored trace_ids_json envelope; fail closed on bad data."""
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(payload, list) or any(not isinstance(item, str) for item in payload):
        return []
    return payload


async def list_report_runs(
    db: AsyncSession,
    user_id: str,
    report_id: str,
    *,
    limit: int = 20,
    offset: int = 0,
) -> list[ReportRun]:
    """Owner-scoped, read-only list of runs for one report.

    Filters on user_id and report_id at the SQL level so a caller can never
    observe another owner's history.
    """
    if not (1 <= limit <= MAX_LIST_LIMIT):
        raise ValueError("limit out of bounds")
    if not (0 <= offset <= MAX_LIST_OFFSET):
        raise ValueError("offset out of bounds")
    rows = (
        await db.execute(
            select(ReportRun)
            .where(
                ReportRun.user_id == user_id,
                ReportRun.report_id == report_id,
            )
            .order_by(ReportRun.started_at.desc(), ReportRun.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    return list(rows)


async def get_report_run(db: AsyncSession, user_id: str, run_id: str) -> ReportRun | None:
    """Owner-scoped single-row fetch; None when the row is absent or not owned."""
    return (
        await db.execute(
            select(ReportRun).where(
                ReportRun.id == run_id,
                ReportRun.user_id == user_id,
            )
        )
    ).scalars().first()


async def revoke_report_run(db: AsyncSession, run_id: str, user_id: str) -> int:
    """Owner-initiated cancellation of a *running* run (running -> interrupted).

    Only transitions status='running'; terminal rows are immutable and return 0.
    Owner-scoping in the WHERE clause prevents cross-tenant revocation. The
    failure_category 'revoked' signals an owner cancellation rather than an
    operational failure.
    """
    result = await db.execute(
        update(ReportRun)
        .where(
            ReportRun.id == run_id,
            ReportRun.user_id == user_id,
            ReportRun.status == "running",
            ReportRun.finished_at.is_(None),
        )
        .values(
            status="interrupted",
            finished_at=datetime.now(timezone.utc),
            failure_category="revoked",
        )
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    return result.rowcount
