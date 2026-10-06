"""Connector-owned Knowledge ingestion, quarantine and deletion.

External content enters OpenJM through the *accepted* VS1 document lifecycle. No
parallel vector pipeline is created here: this module drives the same
``acquire_lease`` / ``begin_ingest`` / ``commit_ingest`` sequence the upload path
uses, so connector documents inherit the same concurrency protection, the same
tenant isolation, the same deletion semantics, the same vector cleanup and the
same retrieval authorization as an uploaded file.

Two things are specific to connector content.

**Namespacing.** A connector document is owned by a derived key that no
principal can hold, and every external resource is namespaced by its connector
instance, so connector content can never collide with an upload, a structured
source, another connector or another tenant.

**Quarantine.** When current authorization cannot be proven, cached content must
stop being retrievable immediately without being deleted, so reconciliation can
restore it later. Quarantine flips both the connector-side lifecycle state and
the Knowledge-side retrievability fields, leaving ``deleted_at`` unset and the
vectors on disk.

Hostile provider text is stored as evidence and nothing else. It is never parsed
as instructions, never used to build a tool name or argument, and never allowed
to influence the runtime.
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.tenancy import DOC_STATE_FAILED, DOC_STATE_PENDING, DOC_STATE_READY
from app.models import (
    Document,
    EXTERNAL_PERMISSION_ALLOWED,
    EXTERNAL_PERMISSION_REVOKED,
    EXTERNAL_PERMISSION_UNKNOWN,
    EXTERNAL_STATE_ACTIVE,
    EXTERNAL_STATE_DELETED,
    EXTERNAL_STATE_QUARANTINED,
    ExternalResource,
)
from app.services import document_lifecycle as lifecycle
from app.services.connectors.base import (
    ExternalResourceRef,
    ResourceContent,
)
from app.services.identity import utcnow


class IngestionError(Exception):
    """Connector content could not be brought into the Knowledge pipeline."""

    def __init__(self, message: str, *, code: str = "connector_ingestion_error"):
        super().__init__(message)
        self.code = code


CONNECTOR_OWNER_PREFIX = "__connector__"


def connector_owner_key(tenant_id: str, connector_instance_id: str) -> str:
    """The derived ``Document.user_id`` for connector-owned content.

    A connector document must be reachable by several principals of a tenant,
    which a per-principal ``user_id`` cannot express. This key is deliberately
    outside the principal namespace (it starts with a reserved prefix and
    contains a colon), so it can never equal ``Principal.user_id`` for a real
    principal. Retrieval therefore never treats it as user-owned; connector
    documents are admitted only through the explicit authorization gate in
    :mod:`app.services.connectors.authorization`.
    """
    return f"{CONNECTOR_OWNER_PREFIX}{tenant_id}:{connector_instance_id}"


def _storage_dir(tenant_id: str, connector_instance_id: str) -> Path:
    settings = get_settings()
    path = Path(settings.upload_dir) / "connectors" / tenant_id / connector_instance_id
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# Resource lookup
# ---------------------------------------------------------------------------


async def load_resource(
    db: AsyncSession, *, connector_instance_id: str, tenant_id: str, external_id: str
) -> ExternalResource | None:
    result = await db.execute(
        select(ExternalResource).where(
            ExternalResource.connector_instance_id == connector_instance_id,
            ExternalResource.tenant_id == tenant_id,
            ExternalResource.external_id == external_id,
        )
    )
    return result.scalars().first()


async def list_resources(
    db: AsyncSession, *, connector_instance_id: str, tenant_id: str
) -> list[ExternalResource]:
    result = await db.execute(
        select(ExternalResource)
        .where(
            ExternalResource.connector_instance_id == connector_instance_id,
            ExternalResource.tenant_id == tenant_id,
        )
        .order_by(ExternalResource.external_id)
    )
    return list(result.scalars().all())


async def _load_document(db: AsyncSession, resource: ExternalResource) -> Document | None:
    if not resource.document_id:
        return None
    result = await db.execute(
        select(Document).where(
            Document.id == resource.document_id,
            Document.tenant_id == resource.tenant_id,
        )
    )
    return result.scalars().first()


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------


async def _index_document(
    db: AsyncSession, document: Document, *, text: str, source_name: str
) -> None:
    """Drive the accepted lease/ingest/commit sequence to (re)build vectors.

    Stale vectors are dropped first so an update replaces content rather than
    appending to it. The document id is stable across an update, which keeps
    citation identity and provenance stable while the content is replaced.
    """
    from app.services.knowledge import KnowledgeEngineError, knowledge_engine

    path = Path(document.stored_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    document.size_bytes = len(text.encode("utf-8"))

    lease = await lifecycle.acquire_lease(db, document.id, purpose="ingest")
    if lease is None:
        raise IngestionError(
            "connector document is busy in another worker", code="document_busy"
        )
    try:
        # Remove any previously indexed chunks for this document so an update
        # replaces rather than accumulates.
        try:
            await knowledge_engine.delete(document.id)
        except KnowledgeEngineError:
            # A collection that does not exist yet is the normal first-ingest
            # case, not a failure.
            pass

        started = await lifecycle.begin_ingest(db, document.id)
        if started is None:
            raise IngestionError(
                "connector document is not ingestable", code="document_not_ingestable"
            )
        token, _version = started
        try:
            await knowledge_engine.ingest(document.id, path, source_name=source_name)
        except KnowledgeEngineError as exc:
            await lifecycle.fail_ingest(db, document.id, token, error=str(exc))
            raise IngestionError(str(exc), code="vector_ingest_failed") from exc
        except Exception as exc:  # noqa: BLE001 - never leave a half-open attempt
            await lifecycle.fail_ingest(db, document.id, token, error=str(exc))
            raise

        committed = await lifecycle.commit_ingest(db, document.id, token)
        if not committed:
            # Deleted or superseded while vectors were being written. Do not
            # publish; remove what was just written.
            try:
                await knowledge_engine.delete(document.id)
            except KnowledgeEngineError:
                pass
            raise IngestionError(
                "connector document was removed while it was being ingested",
                code="document_superseded",
            )
    finally:
        await lifecycle.release_lease(db, document.id, lease)
    await db.refresh(document)


async def upsert_resource(
    db: AsyncSession,
    *,
    connector_instance_id: str,
    tenant_id: str,
    resource_namespace: str,
    provider: str,
    ref: ExternalResourceRef,
    content: ResourceContent | None,
) -> ExternalResource:
    """Create or update one external resource and its Knowledge document.

    Idempotent: re-processing the same external revision is a no-op, which is
    what makes duplicate event delivery and overlapping rescans safe.
    """
    resource = await load_resource(
        db,
        connector_instance_id=connector_instance_id,
        tenant_id=tenant_id,
        external_id=ref.external_id,
    )
    created = resource is None
    if resource is None:
        resource = ExternalResource(
            tenant_id=tenant_id,
            connector_instance_id=connector_instance_id,
            resource_namespace=resource_namespace,
            provider=provider,
            resource_type=ref.resource_type,
            external_id=ref.external_id,
            lifecycle_state=EXTERNAL_STATE_ACTIVE,
            permission_state=EXTERNAL_PERMISSION_UNKNOWN,
        )
        db.add(resource)
        await db.flush()

    # Provider-side metadata is refreshed on every observation regardless of
    # whether the content changed, so drift is visible even for a no-op.
    resource.external_revision = ref.external_revision
    resource.external_parent_id = ref.external_parent_id
    resource.title = ref.title or resource.title
    resource.last_observed_at = utcnow()
    resource.updated_at = utcnow()

    if ref.deleted:
        await mark_resource_deleted(db, resource=resource)
        return resource

    if content is None:
        # The provider listed it but would not return content (for example, a
        # permission-filtered read). Record the reference without content.
        return resource

    new_hash = content.content_hash()
    unchanged = (
        not created
        and resource.content_hash == new_hash
        and resource.lifecycle_state == EXTERNAL_STATE_ACTIVE
        and resource.document_id is not None
    )
    if unchanged:
        resource.last_synced_at = utcnow()
        return resource

    document = await _load_document(db, resource)
    if document is None or document.lifecycle_state == "deleted":
        document = Document(
            tenant_id=tenant_id,
            user_id=connector_owner_key(tenant_id, connector_instance_id),
            original_name=(content.title or ref.external_id)[:500],
            stored_path=str(
                _storage_dir(tenant_id, connector_instance_id) / f"{resource.id}.md"
            ),
            mime_type=content.content_type,
            size_bytes=0,
            status="indexing",
            indexed=False,
        )
        document.lifecycle_state = DOC_STATE_PENDING
        document.lifecycle_version = 1
        db.add(document)
        await db.flush()
        resource.document_id = document.id

    resource.content_hash = new_hash
    resource.source_metadata_json = json.dumps(
        {
            "connector_type": provider,
            "connector_instance_id": connector_instance_id,
            "external_id": ref.external_id,
            "external_revision": ref.external_revision,
            "resource_type": ref.resource_type,
            **(content.metadata or {}),
        }
    )
    resource.provenance_json = json.dumps(
        {
            "origin": "connector",
            "connector_type": provider,
            "connector_instance_id": connector_instance_id,
            "external_id": ref.external_id,
            "external_revision": ref.external_revision,
            "synchronized_at": utcnow().isoformat(),
        }
    )
    resource.lifecycle_state = EXTERNAL_STATE_ACTIVE
    resource.quarantine_reason = None
    resource.quarantined_at = None
    await db.flush()

    await _index_document(
        db, document, text=content.text, source_name=content.title or ref.external_id
    )
    resource.last_synced_at = utcnow()
    return resource


# ---------------------------------------------------------------------------
# Quarantine, restore, deletion
# ---------------------------------------------------------------------------


async def quarantine_resource(
    db: AsyncSession, *, resource: ExternalResource, reason: str
) -> None:
    """Exclude cached content from retrieval without deleting it.

    The connector-side row records why. The Knowledge-side document is moved out
    of every retrievable state (``lifecycle_state``, ``status`` and ``indexed``
    all stop matching the authoritative predicate and the tool predicate) while
    ``deleted_at`` stays unset and the vectors stay on disk, so a later
    reconciliation can restore it.
    """
    resource.lifecycle_state = EXTERNAL_STATE_QUARANTINED
    resource.quarantine_reason = reason
    resource.quarantined_at = utcnow()
    resource.permission_state = EXTERNAL_PERMISSION_UNKNOWN
    resource.updated_at = utcnow()

    document = await _load_document(db, resource)
    if document is not None and document.deleted_at is None:
        document.lifecycle_state = DOC_STATE_FAILED
        document.status = "error"
        document.indexed = False
        document.ingest_token = None
        document.lifecycle_version = int(document.lifecycle_version or 0) + 1
    await db.flush()


async def record_permission_state(
    db: AsyncSession, *, resource: ExternalResource, allowed: bool
) -> None:
    """Record a proven current-authorization outcome."""
    resource.permission_state = (
        EXTERNAL_PERMISSION_ALLOWED if allowed else EXTERNAL_PERMISSION_REVOKED
    )
    resource.updated_at = utcnow()
    await db.flush()


async def restore_resource(
    db: AsyncSession,
    *,
    connector_instance_id: str,
    tenant_id: str,
    resource: ExternalResource,
    content: ResourceContent | None,
) -> bool:
    """Bring a quarantined resource back once authorization is provable again.

    Restoration is deliberately a re-ingest rather than a flag flip: the cached
    vectors were dropped from retrieval, so they are rebuilt from freshly
    fetched content, and the resource only becomes retrievable after a
    successful commit. Returns True when the resource is retrievable again.
    """
    if content is None:
        return False
    resource.lifecycle_state = EXTERNAL_STATE_ACTIVE
    resource.quarantine_reason = None
    resource.quarantined_at = None
    # Restoration proves the resource is still fetchable by the connector's
    # service credential. It does NOT prove any particular user may see it, so
    # the permission state is deliberately reset to unknown rather than claimed
    # as allowed. Per-principal authorization is re-derived at retrieval time.
    resource.permission_state = EXTERNAL_PERMISSION_UNKNOWN
    resource.content_hash = content.content_hash()
    resource.provenance_json = json.dumps(
        {
            "origin": "connector",
            "connector_instance_id": connector_instance_id,
            "external_id": resource.external_id,
            "external_revision": resource.external_revision,
            "restored_at": utcnow().isoformat(),
        }
    )
    await db.flush()

    document = await _load_document(db, resource)
    if document is None:
        # Content was purged while quarantined. Recreate it through the normal
        # path so identity and provenance are rebuilt consistently.
        ref = ExternalResourceRef(
            external_id=resource.external_id,
            resource_type=resource.resource_type,
            external_revision=resource.external_revision,
            external_parent_id=resource.external_parent_id,
            title=resource.title,
        )
        await upsert_resource(
            db,
            connector_instance_id=connector_instance_id,
            tenant_id=tenant_id,
            resource_namespace=resource.resource_namespace,
            provider=resource.provider,
            ref=ref,
            content=content,
        )
        return True

    document.deleted_at = None
    document.lifecycle_state = DOC_STATE_PENDING
    await db.flush()
    await _index_document(
        db, document, text=content.text, source_name=content.title or resource.external_id
    )
    resource.last_synced_at = utcnow()
    return True


async def mark_resource_deleted(db: AsyncSession, *, resource: ExternalResource) -> None:
    """The provider reports the resource gone: stop serving its evidence."""
    resource.lifecycle_state = EXTERNAL_STATE_DELETED
    resource.deleted_at = utcnow()
    resource.permission_state = EXTERNAL_PERMISSION_REVOKED
    resource.updated_at = utcnow()

    document = await _load_document(db, resource)
    if document is not None and document.deleted_at is None:
        document.lifecycle_state = DOC_STATE_FAILED
        document.status = "error"
        document.indexed = False
        document.ingest_token = None
        document.lifecycle_version = int(document.lifecycle_version or 0) + 1
    await db.flush()


async def delete_resource_content(db: AsyncSession, *, resource: ExternalResource) -> None:
    """Physically remove a connector document and its vectors.

    Uses the accepted deletion path so vector cleanup, the deletion lease, the
    lifecycle transition and the audit record all behave exactly as they do for
    an uploaded document.
    """
    from app.services.knowledge import KnowledgeEngineError, knowledge_engine

    document = await _load_document(db, resource)
    if document is None:
        return
    if document.deleted_at is not None:
        return

    lease = await lifecycle.acquire_lease(db, document.id, purpose="delete")
    if lease is None:
        # A concurrent worker holds the document. Leave it quarantined; the
        # purge is retried rather than forced.
        raise IngestionError(
            "connector document is busy in another worker", code="document_busy"
        )
    try:
        await lifecycle.begin_delete(db, document.id)
        try:
            await knowledge_engine.delete(document.id)
        except KnowledgeEngineError:
            pass
        stored = Path(document.stored_path)
        if stored.exists():
            stored.unlink()
        await lifecycle.finish_delete(db, document.id)
    finally:
        await lifecycle.release_lease(db, document.id, lease)

    resource.document_id = None
    resource.content_hash = None
    resource.updated_at = utcnow()
    await db.flush()


__all__ = [
    "CONNECTOR_OWNER_PREFIX",
    "IngestionError",
    "connector_owner_key",
    "delete_resource_content",
    "list_resources",
    "load_resource",
    "mark_resource_deleted",
    "quarantine_resource",
    "record_permission_state",
    "restore_resource",
    "upsert_resource",
]
