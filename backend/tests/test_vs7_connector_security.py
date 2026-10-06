"""VS7 connector security test matrix.

One test per security property: tenancy, lifecycle, sync, authorization and
isolation. Every test drives the real service modules against a real file-backed
SQLite database and a deterministic in-process connector, so an assertion is
always about stored state rather than about a mock call count.

The in-process connector is the only place that would perform network work; it
reads from a :class:`FakeProvider` whose state each test sets explicitly. No
test reaches the network and no test reaches an LLM.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.connectors import (
    AuthorizationBehavior,
    ConnectorCapability,
    ConnectorTypeSpec,
    DeclaredOperation,
    OperationClass,
    connector_registry,
)
from app.core.permissions import Permission
from app.models import (
    ConnectorCredential,
    ConnectorCursor,
    ConnectorInstance,
    Document,
    ExternalResource,
    Tenant,
    WorkspaceUserMapping,
)
from app.services import identity as identity_service
from app.services.connectors import authorization as authorization_service
from app.services.connectors import ingest as ingest_service
from app.services.connectors import service as connector_service
from app.services.connectors import sync as sync_service
from app.services.connectors.base import (
    Connector,
    ConnectorAuthUnavailable,
    ConnectorContext,
    ConnectorError,
    ExternalEvent,
    ExternalResourceRef,
    ResourceContent,
)
from app.services.credentials import credential_vault

TYPE_ID = "sectest"
VERSION = "1.0.0"
KEY = f"{TYPE_ID}@{VERSION}"

TENANT_A = "tnt-sec-a"
TENANT_B = "tnt-sec-b"
PRINCIPAL_A = "sec-pa"
PRINCIPAL_B = "sec-pb"
PRINCIPAL_C = "sec-pc"
PRINCIPAL_D = "sec-pd"


# ---------------------------------------------------------------------------
# Deterministic in-process connector
# ---------------------------------------------------------------------------


class FakeProvider:
    """Provider state a test sets directly. Nothing here touches the network."""

    def __init__(self) -> None:
        self.resources: dict[str, dict] = {}
        self.events: list[ExternalEvent] = []
        self.access: dict[tuple[str, str], bool] = {}
        self.access_error: ConnectorError | None = None
        self.check_calls: list[tuple[str, str]] = []

    def put(
        self,
        external_id: str,
        *,
        text: str,
        revision: str,
        title: str | None = None,
        resource_type: str = "page",
        parent_id: str | None = None,
    ) -> None:
        self.resources[external_id] = {
            "text": text,
            "revision": revision,
            "title": title or external_id,
            "resource_type": resource_type,
            "parent_id": parent_id,
        }

    def remove(self, external_id: str) -> None:
        self.resources.pop(external_id, None)

    def ordered_ids(self) -> list[str]:
        return sorted(self.resources)

    def ref(self, external_id: str) -> ExternalResourceRef:
        item = self.resources[external_id]
        return ExternalResourceRef(
            external_id=external_id,
            resource_type=item["resource_type"],
            external_revision=item["revision"],
            external_parent_id=item["parent_id"],
            title=item["title"],
        )

    def content(self, external_id: str) -> ResourceContent | None:
        item = self.resources.get(external_id)
        if item is None:
            return None
        return ResourceContent(
            external_id=external_id,
            revision=item["revision"],
            title=item["title"],
            text=item["text"],
        )

    def check(self, external_user_id: str, external_id: str) -> bool:
        self.check_calls.append((external_user_id, external_id))
        if self.access_error is not None:
            raise self.access_error
        return bool(self.access.get((external_user_id, external_id), False))

    def page(self, ids: list[str], limit: int, cursor: str | None):
        offset = int(cursor or 0)
        window = ids[offset : offset + limit]
        refs = [self.ref(external_id) for external_id in window]
        next_cursor = str(offset + limit) if offset + limit < len(ids) else None
        return refs, next_cursor


class FakeConnector(Connector):
    type_id = TYPE_ID
    version = VERSION

    def __init__(self, provider: FakeProvider) -> None:
        self.provider = provider

    async def test_connection(self, ctx: ConnectorContext) -> dict:
        return {"ok": True, "detail": "ok"}

    async def list_resources(
        self, ctx: ConnectorContext, *, limit: int, cursor: str | None = None
    ):
        return self.provider.page(self.provider.ordered_ids(), limit, cursor)

    async def fetch_resource(self, ctx: ConnectorContext, external_id: str):
        return self.provider.content(external_id)

    async def check_user_access(
        self, ctx: ConnectorContext, *, external_user_id: str, external_id: str
    ) -> bool:
        return self.provider.check(external_user_id, external_id)

    async def enumerate_events(
        self, ctx: ConnectorContext, *, limit: int, cursor: str | None = None
    ):
        return list(self.provider.events), None

    async def reconcile_scan(
        self, ctx: ConnectorContext, *, limit: int, cursor: str | None = None
    ):
        return self.provider.page(self.provider.ordered_ids(), limit, cursor)


def _spec() -> ConnectorTypeSpec:
    return ConnectorTypeSpec(
        type_id=TYPE_ID,
        version=VERSION,
        display_name="Security Matrix",
        description="In-process connector for the VS7 security matrix.",
        capabilities=frozenset(
            {
                ConnectorCapability.DOCUMENTS,
                ConnectorCapability.EVENTS,
                ConnectorCapability.PERMISSIONS,
            }
        ),
        operations=(
            DeclaredOperation(
                name="sectest.list_resources",
                capability=ConnectorCapability.DOCUMENTS,
                operation_class=OperationClass.READ,
                description="List resources.",
                required_permission=Permission.CONNECTOR_READ.value,
            ),
        ),
        authorization_behavior=AuthorizationBehavior.PROVIDER_CURRENT_STATE,
        requires_user_mapping=True,
        credential_kind="bearer_token",
        credential_fields=("token",),
        credential_rotation="replace",
        event_support=True,
        reconciliation_support=True,
        incremental_support=True,
        supports_test_connection=True,
        timeout_seconds=30,
        max_retries=3,
        initial_sync_limit=100,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def connector(monkeypatch, provider):
    # An ephemeral vault key so no key file is ever written.
    monkeypatch.setattr(credential_vault, "_key", Fernet.generate_key())
    implementation = FakeConnector(provider)
    if not connector_registry.has(TYPE_ID, VERSION):
        connector_registry.register(_spec())
    connector_service.register_implementation(implementation)
    try:
        yield implementation
    finally:
        connector_registry.unregister(KEY)
        connector_service._IMPLEMENTATIONS.pop(KEY, None)


@pytest.fixture
def knowledge(monkeypatch, tmp_path):
    """Replace the vector engine with a recording no-op.

    Connector ingestion drives the real document lifecycle; only the vector
    write is stubbed, so the tests still observe real lifecycle state without
    touching a model or the network.
    """
    import app.services.knowledge as knowledge_module

    ingested: list[tuple[str, str]] = []
    deleted: list[str] = []

    async def fake_ingest(document_id, file_path, source_name=None):
        ingested.append((document_id, Path(file_path).read_text(encoding="utf-8")))

    async def fake_delete(document_id):
        deleted.append(document_id)

    monkeypatch.setattr(knowledge_module.knowledge_engine, "ingest", fake_ingest)
    monkeypatch.setattr(knowledge_module.knowledge_engine, "delete", fake_delete)

    settings = knowledge_module.get_settings()
    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")
    (tmp_path / "uploads").mkdir(parents=True, exist_ok=True)
    return {"ingested": ingested, "deleted": deleted}


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


async def _seed(maker) -> None:
    async with maker() as db:
        db.add_all(
            [
                Tenant(id=TENANT_A, slug="sec-a", name="Security A", status="active"),
                Tenant(id=TENANT_B, slug="sec-b", name="Security B", status="active"),
            ]
        )
        await db.flush()
        await identity_service.get_or_create_principal(
            db, subject="sub-sec-a", principal_id=PRINCIPAL_A
        )
        await identity_service.get_or_create_principal(
            db, subject="sub-sec-b", principal_id=PRINCIPAL_B
        )
        await identity_service.get_or_create_principal(
            db, subject="sub-sec-c", principal_id=PRINCIPAL_C
        )
        await identity_service.get_or_create_principal(
            db, subject="sub-sec-d", principal_id=PRINCIPAL_D
        )
        await identity_service.add_membership(
            db, tenant_id=TENANT_A, principal_id=PRINCIPAL_A, role="editor"
        )
        await identity_service.add_membership(
            db, tenant_id=TENANT_B, principal_id=PRINCIPAL_B, role="owner"
        )
        await identity_service.add_membership(
            db, tenant_id=TENANT_A, principal_id=PRINCIPAL_C, role="viewer"
        )
        await identity_service.add_membership(
            db, tenant_id=TENANT_A, principal_id=PRINCIPAL_D, role="viewer"
        )
        await db.commit()


async def _make_instance(maker, tenant_id: str, *, name: str, enabled: bool = True) -> str:
    async with maker() as db:
        instance = ConnectorInstance(
            tenant_id=tenant_id,
            name=name,
            connector_type=TYPE_ID,
            connector_version=VERSION,
            display_name="Security Matrix",
            status="active" if enabled else "disabled",
            enabled=enabled,
        )
        db.add(instance)
        await db.flush()
        credential = ConnectorCredential(
            tenant_id=tenant_id,
            connector_instance_id=instance.id,
            label="primary",
            kind="bearer_token",
            secret_ciphertext=credential_vault.encrypt(json.dumps({"token": "fake"})),
            status="active",
            version=1,
        )
        db.add(credential)
        await db.flush()
        instance.credential_id = credential.id
        await db.commit()
        return instance.id


async def _instance(db, instance_id: str) -> ConnectorInstance:
    instance = await db.get(ConnectorInstance, instance_id)
    assert instance is not None
    return instance


async def _run_initial(db, instance, limit: int | None = None):
    return await sync_service.run_initial_sync(
        db, instance=instance, actor="tester", limit=limit
    )


async def _count(db, model) -> int:
    return (await db.execute(select(func.count()).select_from(model))).scalar_one()


async def _resource(db, instance_id: str, external_id: str) -> ExternalResource:
    result = await db.execute(
        select(ExternalResource).where(
            ExternalResource.connector_instance_id == instance_id,
            ExternalResource.external_id == external_id,
        )
    )
    resource = result.scalars().first()
    assert resource is not None
    return resource


async def _document(db, resource: ExternalResource) -> Document:
    document = await db.get(Document, resource.document_id)
    assert document is not None
    return document


async def _mapping(db, instance_id: str, principal_id: str, external_user_id: str):
    return await authorization_service.create_mapping(
        db,
        connector_instance_id=instance_id,
        tenant_id=TENANT_A,
        principal_id=principal_id,
        external_user_id=external_user_id,
        actor="tester",
    )


# ---------------------------------------------------------------------------
# CONNECTOR TENANCY
# ---------------------------------------------------------------------------


async def test_connector_metadata_is_tenant_scoped(file_db, connector):
    await _seed(file_db)
    instance_a = await _make_instance(file_db, TENANT_A, name="a-conn")

    async with file_db() as db:
        listed_a = await connector_service.list_instances(db, tenant_id=TENANT_A)
        listed_b = await connector_service.list_instances(db, tenant_id=TENANT_B)

    assert [i.id for i in listed_a] == [instance_a]
    assert listed_b == []


async def test_foreign_tenant_connector_id_is_refused_as_not_found(file_db, connector):
    await _seed(file_db)
    instance_a = await _make_instance(file_db, TENANT_A, name="a-conn")

    async with file_db() as db:
        with pytest.raises(ConnectorError) as foreign:
            await connector_service.resolve_instance(
                db, tenant_id=TENANT_B, connector_instance_id=instance_a
            )
        with pytest.raises(ConnectorError) as missing:
            await connector_service.resolve_instance(
                db, tenant_id=TENANT_B, connector_instance_id="does-not-exist"
            )

    # A real foreign id is indistinguishable from one that does not exist.
    assert foreign.value.code == "connector_not_found"
    assert missing.value.code == "connector_not_found"
    assert foreign.value.code == missing.value.code


async def test_foreign_tenant_external_resource_is_not_resolvable(
    file_db, connector, provider, knowledge
):
    await _seed(file_db)
    instance_a = await _make_instance(file_db, TENANT_A, name="a-conn")
    provider.put("r1", text="alpha", revision="1")

    async with file_db() as db:
        instance = await _instance(db, instance_a)
        await _run_initial(db, instance)

        own = await ingest_service.load_resource(
            db, connector_instance_id=instance_a, tenant_id=TENANT_A, external_id="r1"
        )
        foreign = await ingest_service.load_resource(
            db, connector_instance_id=instance_a, tenant_id=TENANT_B, external_id="r1"
        )

    assert own is not None
    assert foreign is None


async def test_connector_credential_row_cannot_cross_tenants(file_db, connector):
    await _seed(file_db)
    instance_a = await _make_instance(file_db, TENANT_A, name="a-conn")
    instance_b = await _make_instance(file_db, TENANT_B, name="b-conn")

    async with file_db() as db:
        a = await _instance(db, instance_a)
        b = await _instance(db, instance_b)
        credential_a = await connector_service.resolve_credential(db, a)
        assert credential_a.tenant_id == TENANT_A

        # Simulate a mis-pointed reference: tenant B's instance is made to point
        # at tenant A's credential row. Resolution must still refuse it.
        b.credential_id = credential_a.id
        await db.commit()

        with pytest.raises(ConnectorError) as exc:
            await connector_service.resolve_credential(db, b)

    assert exc.value.code == "credential_revoked"


# ---------------------------------------------------------------------------
# CONNECTOR LIFECYCLE
# ---------------------------------------------------------------------------


async def test_disabled_connector_refuses_operations(file_db, connector, provider, knowledge):
    await _seed(file_db)
    instance_id = await _make_instance(file_db, TENANT_A, name="off-conn", enabled=False)
    provider.put("r1", text="alpha", revision="1")

    async with file_db() as db:
        instance = await _instance(db, instance_id)
        with pytest.raises(ConnectorError) as build:
            await connector_service.build_context(db, instance)
        with pytest.raises(sync_service.SyncError) as sync:
            await sync_service.run_initial_sync(db, instance=instance, actor="tester")

    assert build.value.code == "connector_disabled"
    assert sync.value.code == "connector_disabled"


async def test_disconnected_connector_refuses_operations(file_db, connector, provider, knowledge):
    await _seed(file_db)
    instance_id = await _make_instance(file_db, TENANT_A, name="disc-conn")
    provider.put("r1", text="alpha", revision="1")

    async with file_db() as db:
        instance = await _instance(db, instance_id)
        await connector_service.disconnect(db, instance=instance, actor="tester")
        await db.commit()
        assert instance.status == "disconnected"
        assert instance.enabled is False

        with pytest.raises(ConnectorError) as build:
            await connector_service.build_context(db, instance)
        with pytest.raises(sync_service.SyncError) as sync:
            await sync_service.run_initial_sync(db, instance=instance, actor="tester")

    assert build.value.code == "connector_disabled"
    assert sync.value.code == "connector_disabled"


async def test_revoked_credential_refuses_operations(file_db, connector):
    await _seed(file_db)
    instance_id = await _make_instance(file_db, TENANT_A, name="rev-conn")

    async with file_db() as db:
        instance = await _instance(db, instance_id)
        revoked = await connector_service.revoke_credential(
            db, instance=instance, actor="tester"
        )
        await db.commit()
        assert revoked == 1

        with pytest.raises(ConnectorError) as exc:
            await connector_service.resolve_credential(db, instance)

    assert exc.value.code == "credential_revoked"


async def test_rotated_credential_becomes_current_without_recreating_instance(
    file_db, connector
):
    await _seed(file_db)
    instance_id = await _make_instance(file_db, TENANT_A, name="rot-conn")

    async with file_db() as db:
        instance = await _instance(db, instance_id)
        original_id = instance.credential_id
        original = await db.get(ConnectorCredential, original_id)

        rotated = await connector_service.rotate_credential(
            db, instance=instance, actor="tester", credential={"token": "rotated"}
        )
        await db.commit()

        # The connector instance identity is unchanged.
        assert instance.id == instance_id
        assert instance.credential_id == rotated.id
        assert instance.credential_id != original_id

        # The old row is retained as superseded, the new row is active.
        await db.refresh(original)
        assert original.status == "superseded"
        assert rotated.status == "active"
        assert rotated.version == 2

        current = await connector_service.resolve_credential(db, instance)
        assert current.id == rotated.id


async def test_superseded_credential_is_not_reused(file_db, connector):
    await _seed(file_db)
    instance_id = await _make_instance(file_db, TENANT_A, name="stale-conn")

    async with file_db() as db:
        instance = await _instance(db, instance_id)
        old_id = instance.credential_id
        await connector_service.rotate_credential(
            db, instance=instance, actor="tester", credential={"token": "rotated"}
        )
        await db.commit()

        # Point the instance back at the superseded row: it must not resolve.
        instance.credential_id = old_id
        await db.commit()

        with pytest.raises(ConnectorError) as exc:
            await connector_service.resolve_credential(db, instance)

    assert exc.value.code == "credential_revoked"


# ---------------------------------------------------------------------------
# SYNC
# ---------------------------------------------------------------------------


async def test_initial_sync_is_idempotent(file_db, connector, provider, knowledge):
    await _seed(file_db)
    instance_id = await _make_instance(file_db, TENANT_A, name="idem-conn")
    provider.put("r1", text="one", revision="1")
    provider.put("r2", text="two", revision="1")

    async with file_db() as db:
        instance = await _instance(db, instance_id)
        await _run_initial(db, instance)
        assert await _count(db, ExternalResource) == 2
        assert await _count(db, Document) == 2

        await _run_initial(db, instance)
        assert await _count(db, ExternalResource) == 2
        assert await _count(db, Document) == 2


async def test_duplicate_events_do_not_duplicate_resources(
    file_db, connector, provider, knowledge
):
    await _seed(file_db)
    instance_id = await _make_instance(file_db, TENANT_A, name="dup-conn")
    provider.put("r1", text="one", revision="1")

    async with file_db() as db:
        instance = await _instance(db, instance_id)
        await _run_initial(db, instance)

        # The same event id is delivered twice in one run.
        duplicate = ExternalEvent(
            event_id="evt-1", event_type="page.updated", external_id="r1", version="2"
        )
        provider.events = [duplicate, duplicate]
        await sync_service.run_incremental_sync(db, instance=instance, actor="tester")

        assert await _count(db, ExternalResource) == 1


async def test_duplicate_events_do_not_duplicate_embeddings(
    file_db, connector, provider, knowledge
):
    await _seed(file_db)
    instance_id = await _make_instance(file_db, TENANT_A, name="dup-doc-conn")
    provider.put("r1", text="one", revision="1")

    async with file_db() as db:
        instance = await _instance(db, instance_id)
        await _run_initial(db, instance)
        assert await _count(db, Document) == 1

        duplicate = ExternalEvent(
            event_id="evt-1", event_type="page.updated", external_id="r1", version="1"
        )
        provider.events = [duplicate, duplicate]
        await sync_service.run_incremental_sync(db, instance=instance, actor="tester")

        assert await _count(db, Document) == 1
        assert await _count(db, ExternalResource) == 1


async def test_older_revision_event_does_not_overwrite_newer_content(
    file_db, connector, provider, knowledge
):
    await _seed(file_db)
    instance_id = await _make_instance(file_db, TENANT_A, name="ooo-conn")
    provider.put("r1", text="newer content", revision="9")

    async with file_db() as db:
        instance = await _instance(db, instance_id)
        # A higher revision arrives first, then an out-of-order lower revision.
        provider.events = [
            ExternalEvent(
                event_id="evt-hi", event_type="page.updated", external_id="r1", version="9"
            ),
            ExternalEvent(
                event_id="evt-lo", event_type="page.updated", external_id="r1", version="3"
            ),
        ]
        await sync_service.run_incremental_sync(db, instance=instance, actor="tester")

        resource = await _resource(db, instance_id, "r1")
        document = await _document(db, resource)

    expected = hashlib.sha256(b"newer content").hexdigest()
    assert resource.content_hash == expected
    assert Path(document.stored_path).read_text(encoding="utf-8") == "newer content"


async def test_missed_event_is_repaired_by_reconciliation(
    file_db, connector, provider, knowledge
):
    await _seed(file_db)
    instance_id = await _make_instance(file_db, TENANT_A, name="miss-conn")
    provider.put("r1", text="one", revision="1")

    async with file_db() as db:
        instance = await _instance(db, instance_id)
        await _run_initial(db, instance)
        assert (await _resource(db, instance_id, "r1")).external_revision == "1"

        # Provider state changes with no event emitted.
        provider.put("r1", text="two", revision="2")
        await sync_service.run_reconciliation(db, instance=instance, actor="tester")

        resource = await _resource(db, instance_id, "r1")
        document = await _document(db, resource)

    assert resource.external_revision == "2"
    assert resource.content_hash == hashlib.sha256(b"two").hexdigest()
    assert Path(document.stored_path).read_text(encoding="utf-8") == "two"


async def test_resource_update_replaces_stale_content(
    file_db, connector, provider, knowledge
):
    await _seed(file_db)
    instance_id = await _make_instance(file_db, TENANT_A, name="upd-conn")
    provider.put("r1", text="old text", revision="1")

    async with file_db() as db:
        instance = await _instance(db, instance_id)
        await _run_initial(db, instance)

        provider.put("r1", text="new text", revision="2")
        provider.events = [
            ExternalEvent(
                event_id="evt-up", event_type="page.updated", external_id="r1", version="2"
            )
        ]
        await sync_service.run_incremental_sync(db, instance=instance, actor="tester")

        resource = await _resource(db, instance_id, "r1")
        document = await _document(db, resource)
        assert await _count(db, Document) == 1

    stored = Path(document.stored_path).read_text(encoding="utf-8")
    assert stored == "new text"
    assert "old text" not in stored


async def test_resource_delete_prevents_retrieval(file_db, connector, provider, knowledge):
    await _seed(file_db)
    instance_id = await _make_instance(file_db, TENANT_A, name="del-conn")
    provider.put("r1", text="alpha", revision="1")

    async with file_db() as db:
        instance = await _instance(db, instance_id)
        await _run_initial(db, instance)
        resource = await _resource(db, instance_id, "r1")
        document = await _document(db, resource)
        assert document.lifecycle_state == "ready"

        # Deletion arrives through the feed, then reconciliation runs.
        provider.remove("r1")
        provider.events = [
            ExternalEvent(
                event_id="evt-del", event_type="page.deleted", external_id="r1", version=None
            )
        ]
        await sync_service.run_incremental_sync(db, instance=instance, actor="tester")
        await sync_service.run_reconciliation(db, instance=instance, actor="tester")

        resource = await _resource(db, instance_id, "r1")
        await db.refresh(document)

    assert resource.lifecycle_state == "deleted"
    # The document is no longer in any retrievable state.
    assert document.lifecycle_state != "ready"
    assert document.indexed is False
    assert document.status == "error"


async def test_restart_resumes_without_duplication_or_skipping(
    file_db, connector, provider, knowledge
):
    await _seed(file_db)
    instance_id = await _make_instance(file_db, TENANT_A, name="restart-conn")
    provider.put("r1", text="one", revision="1")
    provider.put("r2", text="two", revision="1")
    provider.put("r3", text="three", revision="1")

    async with file_db() as db:
        instance = await _instance(db, instance_id)

        # A partial initial sync processes one bounded page only.
        await _run_initial(db, instance, limit=2)
        cursor = await sync_service.load_cursor(
            db, connector_instance_id=instance_id, stream=sync_service.STREAM_INITIAL
        )
        assert cursor is not None
        assert cursor.cursor == "2"
        assert await _count(db, ExternalResource) == 2

        # Resume: the remaining resource is picked up, nothing is duplicated.
        await _run_initial(db, instance, limit=100)
        resources = (
            await db.execute(
                select(ExternalResource.external_id).where(
                    ExternalResource.connector_instance_id == instance_id
                )
            )
        ).scalars().all()

    assert sorted(resources) == ["r1", "r2", "r3"]
    assert len(resources) == 3


# ---------------------------------------------------------------------------
# AUTHORIZATION
# ---------------------------------------------------------------------------


async def _authorized_setup(
    maker,
    provider,
    *,
    name: str,
    principal_id: str = PRINCIPAL_A,
    external_user_id: str = "u1",
    mapping: bool = True,
) -> str:
    await _seed(maker)
    instance_id = await _make_instance(maker, TENANT_A, name=name)
    provider.put("r1", text="alpha", revision="1")
    async with maker() as db:
        instance = await _instance(db, instance_id)
        await _run_initial(db, instance)
        if mapping:
            await _mapping(db, instance_id, principal_id, external_user_id)
            await db.commit()
    return instance_id


async def test_service_credential_alone_does_not_grant_evidence(
    file_db, connector, provider, knowledge
):
    instance_id = await _authorized_setup(
        file_db, provider, name="nomap-conn", mapping=False
    )

    async with file_db() as db:
        instance = await _instance(db, instance_id)
        # The connector is enabled and the credential resolves.
        assert instance.enabled is True
        assert (await connector_service.resolve_credential(db, instance)).status == "active"

        outcome = await authorization_service.require_current_authorization(
            db,
            tenant_id=TENANT_A,
            connector_instance_id=instance_id,
            external_id="r1",
            principal_id=PRINCIPAL_A,
        )

    assert outcome.allowed is False
    assert outcome.reason == "user_not_mapped"


async def test_missing_user_mapping_denies(file_db, connector, provider, knowledge):
    instance_id = await _authorized_setup(
        file_db, provider, name="nomap2-conn", mapping=False
    )

    async with file_db() as db:
        allowed, denials = await authorization_service.authorized_connector_document_ids(
            db, tenant_id=TENANT_A, principal_id=PRINCIPAL_A
        )

    assert allowed == set()
    assert denials["r1"] == "user_not_mapped"


async def test_ambiguous_mapping_is_impossible_to_create(file_db, connector, provider, knowledge):
    instance_id = await _authorized_setup(file_db, provider, name="amb-conn", mapping=False)

    async with file_db() as db:
        await _mapping(db, instance_id, PRINCIPAL_A, "u1")
        await db.commit()

    # A second active mapping for the same principal is rejected.
    async with file_db() as db:
        with pytest.raises(IntegrityError):
            await _mapping(db, instance_id, PRINCIPAL_A, "u2")
            await db.commit()

    # A second active mapping for the same external user id is rejected.
    async with file_db() as db:
        with pytest.raises(IntegrityError):
            await _mapping(db, instance_id, PRINCIPAL_C, "u1")
            await db.commit()

    async with file_db() as db:
        mappings = (
            await db.execute(
                select(WorkspaceUserMapping).where(
                    WorkspaceUserMapping.connector_instance_id == instance_id
                )
            )
        ).scalars().all()
        assert len(mappings) == 1


async def test_revoked_user_mapping_denies(file_db, connector, provider, knowledge):
    instance_id = await _authorized_setup(file_db, provider, name="revmap-conn")

    async with file_db() as db:
        mapping = await authorization_service.resolve_mapping(
            db,
            connector_instance_id=instance_id,
            tenant_id=TENANT_A,
            principal_id=PRINCIPAL_A,
        )
        assert mapping is not None
        await authorization_service.revoke_mapping(db, mapping=mapping, actor="tester")
        await db.commit()

        outcome = await authorization_service.require_current_authorization(
            db,
            tenant_id=TENANT_A,
            connector_instance_id=instance_id,
            external_id="r1",
            principal_id=PRINCIPAL_A,
        )

    assert outcome.allowed is False
    assert outcome.reason == "user_not_mapped"


async def test_revoked_source_acl_denies_and_quarantines(
    file_db, connector, provider, knowledge
):
    instance_id = await _authorized_setup(file_db, provider, name="acl-conn")
    provider.access[("u1", "r1")] = False

    async with file_db() as db:
        outcome = await authorization_service.require_current_authorization(
            db,
            tenant_id=TENANT_A,
            connector_instance_id=instance_id,
            external_id="r1",
            principal_id=PRINCIPAL_A,
        )
        await db.commit()
        resource = await _resource(db, instance_id, "r1")

    assert outcome.allowed is False
    assert outcome.reason == "access_revoked"
    assert outcome.quarantined is True
    assert resource.lifecycle_state == "quarantined"


async def test_provider_authorization_timeout_denies_and_quarantines(
    file_db, connector, provider, knowledge
):
    instance_id = await _authorized_setup(file_db, provider, name="timeout-conn")
    provider.access_error = ConnectorAuthUnavailable("timed out", code="provider_timeout")

    async with file_db() as db:
        outcome = await authorization_service.require_current_authorization(
            db,
            tenant_id=TENANT_A,
            connector_instance_id=instance_id,
            external_id="r1",
            principal_id=PRINCIPAL_A,
        )
        await db.commit()
        resource = await _resource(db, instance_id, "r1")

    assert outcome.allowed is False
    assert outcome.reason == "provider_timeout"
    assert outcome.quarantined is True
    assert resource.lifecycle_state == "quarantined"


async def test_provider_authorization_outage_denies_and_quarantines(
    file_db, connector, provider, knowledge
):
    instance_id = await _authorized_setup(file_db, provider, name="outage-conn")
    provider.access_error = ConnectorAuthUnavailable(
        "provider returned HTTP 503", code="provider_error"
    )

    async with file_db() as db:
        outcome = await authorization_service.require_current_authorization(
            db,
            tenant_id=TENANT_A,
            connector_instance_id=instance_id,
            external_id="r1",
            principal_id=PRINCIPAL_A,
        )
        await db.commit()
        resource = await _resource(db, instance_id, "r1")

    assert outcome.allowed is False
    assert outcome.reason == "provider_error"
    assert outcome.quarantined is True
    assert resource.lifecycle_state == "quarantined"


async def test_cached_content_never_bypasses_current_permission_checks(
    file_db, connector, provider, knowledge
):
    instance_id = await _authorized_setup(file_db, provider, name="cache-conn")
    provider.access[("u1", "r1")] = True

    async with file_db() as db:
        allowed_before, _ = await authorization_service.authorized_connector_document_ids(
            db, tenant_id=TENANT_A, principal_id=PRINCIPAL_A
        )
        resource = await _resource(db, instance_id, "r1")
        document_id = resource.document_id

    assert allowed_before == {document_id}

    # Access is revoked after the successful authorized read. The current-state
    # check now denies and quarantines the cached content.
    provider.access[("u1", "r1")] = False

    async with file_db() as db:
        outcome = await authorization_service.require_current_authorization(
            db,
            tenant_id=TENANT_A,
            connector_instance_id=instance_id,
            external_id="r1",
            principal_id=PRINCIPAL_A,
        )

    assert outcome.allowed is False
    assert outcome.reason == "access_revoked"

    # The document id is no longer returned by the retrieval gate.
    async with file_db() as db:
        allowed_after, _denials = await authorization_service.authorized_connector_document_ids(
            db, tenant_id=TENANT_A, principal_id=PRINCIPAL_A
        )

    assert document_id not in allowed_after
    assert allowed_after == set()


# ---------------------------------------------------------------------------
# ISOLATION
# ---------------------------------------------------------------------------


async def test_two_tenants_do_not_leak_connector_metadata(file_db, connector):
    await _seed(file_db)
    instance_a = await _make_instance(file_db, TENANT_A, name="iso-meta-a")
    instance_b = await _make_instance(file_db, TENANT_B, name="iso-meta-b")

    async with file_db() as db:
        listed_a = await connector_service.list_instances(db, tenant_id=TENANT_A)
        listed_b = await connector_service.list_instances(db, tenant_id=TENANT_B)

    assert [i.id for i in listed_a] == [instance_a]
    assert [i.id for i in listed_b] == [instance_b]
    assert instance_b not in [i.id for i in listed_a]
    assert instance_a not in [i.id for i in listed_b]


async def test_two_tenants_do_not_leak_resources(file_db, connector, provider, knowledge):
    await _seed(file_db)
    instance_a = await _make_instance(file_db, TENANT_A, name="iso-res-a")
    instance_b = await _make_instance(file_db, TENANT_B, name="iso-res-b")

    provider.put("r1", text="alpha", revision="1")
    async with file_db() as db:
        a = await _instance(db, instance_a)
        await _run_initial(db, a)

    provider.remove("r1")
    provider.put("r2", text="beta", revision="1")
    async with file_db() as db:
        b = await _instance(db, instance_b)
        await _run_initial(db, b)

    async with file_db() as db:
        resources_a = await ingest_service.list_resources(
            db, connector_instance_id=instance_a, tenant_id=TENANT_A
        )
        resources_b = await ingest_service.list_resources(
            db, connector_instance_id=instance_b, tenant_id=TENANT_B
        )
        cross_tenant = await ingest_service.load_resource(
            db, connector_instance_id=instance_b, tenant_id=TENANT_A, external_id="r2"
        )
        cross_instance = await ingest_service.load_resource(
            db, connector_instance_id=instance_a, tenant_id=TENANT_A, external_id="r2"
        )

    assert [r.external_id for r in resources_a] == ["r1"]
    assert [r.external_id for r in resources_b] == ["r2"]
    assert cross_tenant is None
    assert cross_instance is None


async def test_two_users_do_not_leak_user_specific_evidence(
    file_db, connector, provider, knowledge
):
    instance_id = await _authorized_setup(file_db, provider, name="iso-user-conn")
    async with file_db() as db:
        await _mapping(db, instance_id, PRINCIPAL_C, "uc")
        await _mapping(db, instance_id, PRINCIPAL_D, "ud")
        await db.commit()

    # Principal C is allowed by the provider; principal D is denied.
    provider.access[("uc", "r1")] = True
    provider.access[("ud", "r1")] = False

    async with file_db() as db:
        resource = await _resource(db, instance_id, "r1")
        document_id = resource.document_id
        allowed_c, _ = await authorization_service.authorized_connector_document_ids(
            db, tenant_id=TENANT_A, principal_id=PRINCIPAL_C
        )
        allowed_d, denials_d = await authorization_service.authorized_connector_document_ids(
            db, tenant_id=TENANT_A, principal_id=PRINCIPAL_D
        )

    assert allowed_c == {document_id}
    assert document_id not in allowed_d
    assert allowed_d == set()
    assert denials_d["r1"] == "access_revoked"

