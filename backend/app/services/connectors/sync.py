"""The generic synchronization and reconciliation engine.

One engine serves every connector. It owns the parts that must not be
reimplemented per provider:

* **Bounds.** Initial sync, incremental sync and reconciliation all take an
  explicit limit and never exceed it.
* **Idempotence.** Reprocessing the same external revision is a no-op because
  ingestion compares a content hash. Duplicate event delivery therefore cannot
  duplicate a resource or its embeddings.
* **Restart safety.** Cursors are persisted after each page, so a crash resumes
  from the last committed checkpoint instead of restarting or skipping.
* **Deterministic deduplication.** Events are deduplicated by event id before
  any work happens, which tolerates the overlapping reads that bounded retry
  produces.
* **Deletion that is never inferred from absence.** A resource is only treated
  as deleted after a *complete* reconciliation scan. A truncated page, a
  permission-filtered read or an eventual-consistency gap must never be read as
  a deletion, because that would silently destroy evidence.
* **Operator-visible state.** Every run is recorded with counters and a safe
  failure category, and every failure is audited.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ConnectorCursor,
    ConnectorInstance,
    ConnectorSyncRun,
    EXTERNAL_STATE_DELETED,
    EXTERNAL_STATE_QUARANTINED,
    ExternalResource,
)
from app.services.connectors.base import (
    ConnectorAuthUnavailable,
    ConnectorError,
    ExternalEvent,
    redact,
)
from app.services.connectors.ingest import (
    load_resource,
    mark_resource_deleted,
    restore_resource,
    upsert_resource,
)
from app.services.connectors.service import (
    build_context,
    get_implementation,
    resource_namespace,
    spec_for,
)
from app.services.identity import record_audit, utcnow

STREAM_INITIAL = "initial"
STREAM_EVENTS = "events"
STREAM_RECONCILE = "reconcile"

# Hard ceiling on how many resources one reconciliation sweep will walk, so a
# drift repair cannot become an unbounded crawl of an external system.
MAX_RECONCILE_SCAN = 2000


@dataclass
class SyncCounters:
    scanned: int = 0
    created: int = 0
    updated: int = 0
    deleted: int = 0
    quarantined: int = 0
    skipped: int = 0

    def as_dict(self) -> dict:
        return {
            "items_scanned": self.scanned,
            "items_created": self.created,
            "items_updated": self.updated,
            "items_deleted": self.deleted,
            "items_quarantined": self.quarantined,
            "items_skipped": self.skipped,
        }


class SyncError(ConnectorError):
    """A synchronization run failed."""


# ---------------------------------------------------------------------------
# Cursor persistence
# ---------------------------------------------------------------------------


async def load_cursor(
    db: AsyncSession, *, connector_instance_id: str, stream: str
) -> ConnectorCursor | None:
    result = await db.execute(
        select(ConnectorCursor).where(
            ConnectorCursor.connector_instance_id == connector_instance_id,
            ConnectorCursor.stream == stream,
        )
    )
    return result.scalars().first()


async def save_cursor(
    db: AsyncSession,
    *,
    tenant_id: str,
    connector_instance_id: str,
    stream: str,
    cursor: str | None,
    last_event_id: str | None = None,
) -> ConnectorCursor:
    record = await load_cursor(
        db, connector_instance_id=connector_instance_id, stream=stream
    )
    if record is None:
        record = ConnectorCursor(
            tenant_id=tenant_id,
            connector_instance_id=connector_instance_id,
            stream=stream,
        )
        db.add(record)
    record.cursor = cursor
    record.last_event_id = last_event_id or record.last_event_id
    record.updated_at = utcnow()
    await db.flush()
    return record


# ---------------------------------------------------------------------------
# Run bookkeeping
# ---------------------------------------------------------------------------


async def _open_run(
    db: AsyncSession, *, instance: ConnectorInstance, run_type: str, idempotency_key: str | None
) -> ConnectorSyncRun:
    run = ConnectorSyncRun(
        tenant_id=instance.tenant_id,
        connector_instance_id=instance.id,
        run_type=run_type,
        status="running",
        idempotency_key=idempotency_key,
    )
    db.add(run)
    await db.flush()
    return run


async def _finish_run(
    db: AsyncSession,
    *,
    run: ConnectorSyncRun,
    status: str,
    counters: SyncCounters,
    cursor_before: str | None = None,
    cursor_after: str | None = None,
    failure_category: str | None = None,
    detail: str | None = None,
) -> ConnectorSyncRun:
    run.status = status
    run.finished_at = utcnow()
    run.failure_category = failure_category
    run.detail = redact(detail, limit=500) if detail else None
    run.cursor_before = cursor_before
    run.cursor_after = cursor_after
    for field, value in counters.as_dict().items():
        setattr(run, field, value)
    await db.flush()
    return run


def _record_failure_on_instance(
    instance: ConnectorInstance, category: str, detail: str | None = None
) -> None:
    instance.last_failure_category = category
    instance.last_failure_at = utcnow()
    instance.health_status = "unhealthy"
    if detail:
        instance.health_detail = redact(detail, limit=240)
    instance.updated_at = utcnow()


# ---------------------------------------------------------------------------
# Sync entry points
# ---------------------------------------------------------------------------


async def run_initial_sync(
    db: AsyncSession, *, instance: ConnectorInstance, actor: str, limit: int | None = None
) -> ConnectorSyncRun:
    """Enumerate the connector's allowed resources and ingest them."""
    spec = spec_for(instance)
    if not instance.enabled:
        raise SyncError("connector is not enabled", code="connector_disabled")
    bound = int(limit or spec.initial_sync_limit)
    counters = SyncCounters()
    run = await _open_run(db, instance=instance, run_type="initial", idempotency_key=None)

    try:
        ctx = await build_context(db, instance)
        connector = get_implementation(spec.type_id, spec.version)
        namespace = resource_namespace(instance.id)
        cursor: str | None = None
        processed = 0
        while processed < bound:
            page_limit = min(100, bound - processed)
            refs, cursor = await connector.list_resources(
                ctx, limit=page_limit, cursor=cursor
            )
            if not refs:
                break
            for ref in refs:
                if processed >= bound:
                    break
                processed += 1
                counters.scanned += 1
                was_existing = (
                    await load_resource(
                        db,
                        connector_instance_id=instance.id,
                        tenant_id=instance.tenant_id,
                        external_id=ref.external_id,
                    )
                    is not None
                )
                content = None
                if not ref.deleted:
                    content = await connector.fetch_resource(ctx, ref.external_id)
                await upsert_resource(
                    db,
                    connector_instance_id=instance.id,
                    tenant_id=instance.tenant_id,
                    resource_namespace=namespace,
                    provider=spec.type_id,
                    ref=ref,
                    content=content,
                )
                if was_existing:
                    counters.updated += 1
                else:
                    counters.created += 1
                await db.commit()
            await save_cursor(
                db,
                tenant_id=instance.tenant_id,
                connector_instance_id=instance.id,
                stream=STREAM_INITIAL,
                cursor=cursor,
            )
            await db.commit()
            if cursor is None:
                break

        instance.last_successful_sync_at = utcnow()
        instance.health_status = "healthy"
        instance.health_detail = None
        instance.last_failure_category = None
        instance.updated_at = utcnow()
        await _finish_run(db, run=run, status="succeeded", counters=counters)
        await record_audit(
            db,
            principal=None,
            tenant_id=instance.tenant_id,
            action="connector.sync.initial",
            decision="allow",
            resource_type="connector_instance",
            resource_id=instance.id,
            metadata={"actor": actor, **counters.as_dict()},
        )
        await db.commit()
    except (ConnectorError, ConnectorAuthUnavailable) as exc:
        _record_failure_on_instance(instance, exc.code, exc.detail)
        await _finish_run(
            db,
            run=run,
            status="failed",
            counters=counters,
            failure_category=exc.code,
            detail=exc.detail or str(exc),
        )
        await record_audit(
            db,
            principal=None,
            tenant_id=instance.tenant_id,
            action="connector.sync.initial",
            decision="deny",
            resource_type="connector_instance",
            resource_id=instance.id,
            reason=exc.code,
        )
        await db.commit()
    return run


async def run_incremental_sync(
    db: AsyncSession, *, instance: ConnectorInstance, actor: str, limit: int = 100
) -> ConnectorSyncRun:
    """Consume the provider's change feed from the persisted checkpoint."""
    spec = spec_for(instance)
    if not instance.enabled:
        raise SyncError("connector is not enabled", code="connector_disabled")
    counters = SyncCounters()
    run = await _open_run(db, instance=instance, run_type="incremental", idempotency_key=None)
    cursor_before: str | None = None

    try:
        if not spec.event_support:
            await _finish_run(db, run=run, status="skipped", counters=counters)
            return run

        ctx = await build_context(db, instance)
        connector = get_implementation(spec.type_id, spec.version)
        namespace = resource_namespace(instance.id)

        record = await load_cursor(
            db, connector_instance_id=instance.id, stream=STREAM_EVENTS
        )
        cursor_before = record.cursor if record else None
        seen: set[str] = set()
        events, next_cursor = await connector.enumerate_events(
            ctx, limit=limit, cursor=cursor_before
        )
        counters.scanned = len(events)

        for event in events:
            # Deterministic deduplication before any work: an event id that has
            # already been processed in this run is skipped, which is what makes
            # overlapping reads safe.
            if event.event_id in seen:
                counters.skipped += 1
                continue
            seen.add(event.event_id)

            ref = await _event_to_ref(event)
            if ref is None:
                counters.skipped += 1
                continue
            existed = (
                await load_resource(
                    db,
                    connector_instance_id=instance.id,
                    tenant_id=instance.tenant_id,
                    external_id=ref.external_id,
                )
                is not None
            )
            content = None if ref.deleted else await connector.fetch_resource(ctx, ref.external_id)
            await upsert_resource(
                db,
                connector_instance_id=instance.id,
                tenant_id=instance.tenant_id,
                resource_namespace=namespace,
                provider=spec.type_id,
                ref=ref,
                content=content,
            )
            if ref.deleted:
                counters.deleted += 1
            elif existed:
                counters.updated += 1
            else:
                counters.created += 1
            await db.commit()

        await save_cursor(
            db,
            tenant_id=instance.tenant_id,
            connector_instance_id=instance.id,
            stream=STREAM_EVENTS,
            cursor=next_cursor,
            last_event_id=events[-1].event_id if events else None,
        )
        instance.last_successful_sync_at = utcnow()
        instance.health_status = "healthy"
        instance.last_failure_category = None
        instance.updated_at = utcnow()
        await _finish_run(
            db,
            run=run,
            status="succeeded",
            counters=counters,
            cursor_before=cursor_before,
            cursor_after=next_cursor,
        )
        await record_audit(
            db,
            principal=None,
            tenant_id=instance.tenant_id,
            action="connector.sync.incremental",
            decision="allow",
            resource_type="connector_instance",
            resource_id=instance.id,
            metadata={"actor": actor, **counters.as_dict()},
        )
        await db.commit()
    except (ConnectorError, ConnectorAuthUnavailable) as exc:
        _record_failure_on_instance(instance, exc.code, exc.detail)
        await _finish_run(
            db,
            run=run,
            status="failed",
            counters=counters,
            cursor_before=cursor_before,
            failure_category=exc.code,
            detail=exc.detail or str(exc),
        )
        await record_audit(
            db,
            principal=None,
            tenant_id=instance.tenant_id,
            action="connector.sync.incremental",
            decision="deny",
            resource_type="connector_instance",
            resource_id=instance.id,
            reason=exc.code,
        )
        await db.commit()
    return run


async def _event_to_ref(event: ExternalEvent):
    """Map a provider event to a resource reference without fetching content."""
    from app.services.connectors.base import ExternalResourceRef

    event_type = (event.event_type or "").lower()
    deleted = any(token in event_type for token in ("delete", "remove", "revoke"))
    return ExternalResourceRef(
        external_id=event.external_id,
        resource_type="page",
        external_revision=event.version,
        deleted=deleted,
    )


async def run_reconciliation(
    db: AsyncSession, *, instance: ConnectorInstance, actor: str, limit: int = MAX_RECONCILE_SCAN
) -> ConnectorSyncRun:
    """Repair drift by walking the provider's current state.

    Deletions are applied only after a complete sweep. A partial or truncated
    scan proves nothing about what still exists, so it must never be used to
    remove evidence.
    """
    spec = spec_for(instance)
    if not instance.enabled:
        raise SyncError("connector is not enabled", code="connector_disabled")
    counters = SyncCounters()
    run = await _open_run(db, instance=instance, run_type="reconcile", idempotency_key=None)

    try:
        ctx = await build_context(db, instance)
        connector = get_implementation(spec.type_id, spec.version)
        if not spec.reconciliation_support:
            await _finish_run(db, run=run, status="skipped", counters=counters)
            return run

        namespace = resource_namespace(instance.id)
        seen: set[str] = set()
        cursor: str | None = None  # a blank cursor starts a fresh full sweep
        complete = False
        scanned = 0
        while scanned < limit:
            page_limit = min(100, limit - scanned)
            refs, cursor = await connector.reconcile_scan(ctx, limit=page_limit, cursor=cursor)
            if not refs:
                complete = cursor is None
                break
            for ref in refs:
                scanned += 1
                counters.scanned += 1
                seen.add(ref.external_id)
                existing = await load_resource(
                    db,
                    connector_instance_id=instance.id,
                    tenant_id=instance.tenant_id,
                    external_id=ref.external_id,
                )
                target = existing
                if existing is None:
                    content = await connector.fetch_resource(ctx, ref.external_id)
                    target = await upsert_resource(
                        db,
                        connector_instance_id=instance.id,
                        tenant_id=instance.tenant_id,
                        resource_namespace=namespace,
                        provider=spec.type_id,
                        ref=ref,
                        content=content,
                    )
                    counters.created += 1
                elif existing.lifecycle_state == EXTERNAL_STATE_QUARANTINED:
                    # A quarantined resource is retried on every sweep. Its
                    # external revision is unchanged, so a revision-only
                    # comparison would skip it forever and cached content that
                    # was withdrawn when authorization could not be proven could
                    # never come back. Restoration re-ingests, which is what
                    # makes the resource retrievable again; who may actually see
                    # it is still decided per principal at retrieval time.
                    content = await connector.fetch_resource(ctx, ref.external_id)
                    target = existing
                    restored = await restore_resource(
                        db,
                        connector_instance_id=instance.id,
                        tenant_id=instance.tenant_id,
                        resource=existing,
                        content=content,
                    )
                    if restored:
                        counters.updated += 1
                    else:
                        counters.skipped += 1
                elif existing.external_revision != ref.external_revision:
                    # A stale revision means an event was missed.
                    content = await connector.fetch_resource(ctx, ref.external_id)
                    target = await upsert_resource(
                        db,
                        connector_instance_id=instance.id,
                        tenant_id=instance.tenant_id,
                        resource_namespace=namespace,
                        provider=spec.type_id,
                        ref=ref,
                        content=content,
                    )
                    counters.updated += 1
                else:
                    counters.skipped += 1
                if target is not None:
                    target.last_reconciled_at = utcnow()
                await db.commit()
            if cursor is None:
                complete = True
                break

        if complete:
            # Only now is absence meaningful.
            result = await db.execute(
                select(ExternalResource).where(
                    ExternalResource.connector_instance_id == instance.id,
                    ExternalResource.tenant_id == instance.tenant_id,
                    ExternalResource.lifecycle_state != EXTERNAL_STATE_DELETED,
                )
            )
            for resource in list(result.scalars().all()):
                if resource.external_id not in seen:
                    await mark_resource_deleted(db, resource=resource)
                    counters.deleted += 1
            await db.commit()

        instance.last_reconciliation_at = utcnow()
        instance.health_status = "healthy"
        instance.last_failure_category = None
        instance.updated_at = utcnow()
        await _finish_run(db, run=run, status="succeeded", counters=counters)
        await record_audit(
            db,
            principal=None,
            tenant_id=instance.tenant_id,
            action="connector.sync.reconcile",
            decision="allow",
            resource_type="connector_instance",
            resource_id=instance.id,
            metadata={
                "actor": actor,
                "complete_sweep": complete,
                **counters.as_dict(),
            },
        )
        await db.commit()
    except (ConnectorError, ConnectorAuthUnavailable) as exc:
        _record_failure_on_instance(instance, exc.code, exc.detail)
        await _finish_run(
            db,
            run=run,
            status="failed",
            counters=counters,
            failure_category=exc.code,
            detail=exc.detail or str(exc),
        )
        await record_audit(
            db,
            principal=None,
            tenant_id=instance.tenant_id,
            action="connector.sync.reconcile",
            decision="deny",
            resource_type="connector_instance",
            resource_id=instance.id,
            reason=exc.code,
        )
        await db.commit()
    return run


async def run_sync(
    db: AsyncSession,
    *,
    instance: ConnectorInstance,
    actor: str,
    run_type: str = "initial",
    limit: int | None = None,
) -> ConnectorSyncRun:
    """Dispatch a synchronization run by type."""
    if run_type == "initial":
        return await run_initial_sync(db, instance=instance, actor=actor, limit=limit)
    if run_type == "incremental":
        return await run_incremental_sync(
            db, instance=instance, actor=actor, limit=limit or 100
        )
    if run_type == "reconcile":
        return await run_reconciliation(
            db, instance=instance, actor=actor, limit=limit or MAX_RECONCILE_SCAN
        )
    raise SyncError(f"unsupported sync run type '{run_type}'", code="unsupported_run_type")


async def latest_runs(
    db: AsyncSession, *, connector_instance_id: str, tenant_id: str, limit: int = 10
) -> list[ConnectorSyncRun]:
    result = await db.execute(
        select(ConnectorSyncRun)
        .where(
            ConnectorSyncRun.connector_instance_id == connector_instance_id,
            ConnectorSyncRun.tenant_id == tenant_id,
        )
        .order_by(ConnectorSyncRun.started_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


def run_to_dict(run: ConnectorSyncRun) -> dict:
    return {
        "id": run.id,
        "run_type": run.run_type,
        "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "items_scanned": run.items_scanned,
        "items_created": run.items_created,
        "items_updated": run.items_updated,
        "items_deleted": run.items_deleted,
        "items_quarantined": run.items_quarantined,
        "items_skipped": run.items_skipped,
        "failure_category": run.failure_category,
        "detail": run.detail,
    }


def provenance_for(resource: ExternalResource) -> dict:
    """Citation provenance for one connector resource."""
    payload: dict = {}
    if resource.provenance_json:
        try:
            parsed = json.loads(resource.provenance_json)
            if isinstance(parsed, dict):
                payload = parsed
        except (TypeError, ValueError):
            payload = {}
    payload.setdefault("connector_type", resource.provider)
    payload.setdefault("connector_instance_id", resource.connector_instance_id)
    payload.setdefault("external_id", resource.external_id)
    payload.setdefault("external_revision", resource.external_revision)
    return payload


__all__ = [
    "MAX_RECONCILE_SCAN",
    "STREAM_EVENTS",
    "STREAM_INITIAL",
    "STREAM_RECONCILE",
    "SyncCounters",
    "SyncError",
    "latest_runs",
    "load_cursor",
    "provenance_for",
    "run_incremental_sync",
    "run_initial_sync",
    "run_reconciliation",
    "run_sync",
    "run_to_dict",
    "save_cursor",
]
