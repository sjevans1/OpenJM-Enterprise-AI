"""BV5-A: Chat artifact foundation — security and behaviour.

A Chat artifact is a downloadable work product (HTML / Markdown / TXT / CSV)
created from General Chat. These tests exercise the real routers and services on
a file-backed SQLite database. The security core is: cross-tenant and wrong-user
downloads denied, path traversal rejected, unsupported MIME rejected, oversized
artifacts rejected, deleted/revoked artifacts unavailable, and hostile content
that can never escape the storage or render boundary.
"""

from __future__ import annotations

import re

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect

from app.core.config import get_settings
from app.core.tenancy import LEGACY_TENANT_ID
from app.models import ChatArtifact
from app.services import artifacts as artifacts_service
from app.services.artifacts import ArtifactError, ArtifactNameError, ArtifactStorage


@pytest.fixture(autouse=True)
def _artifact_settings(tmp_path, monkeypatch):
    """Hermetic artifact storage: never touch the repo's data directory."""
    base = get_settings()
    fake = base.model_copy(update={"artifacts_dir": tmp_path / "artifacts"})
    monkeypatch.setattr(artifacts_service, "get_settings", lambda: fake)
    return fake


def _tiny_settings(tmp_path, **updates):
    base = get_settings()
    return base.model_copy(
        update={"artifacts_dir": tmp_path / "artifacts", **updates}
    )


def _payload(**overrides):
    body = {
        "title": "Weekly summary",
        "format": "markdown",
        "content": "# Weekly summary\n\nAll good.\n",
    }
    body.update(overrides)
    return body


async def _seed(file_db, **overrides):
    """Persist an artifact row directly, bypassing the API."""
    fields = {
        "tenant_id": LEGACY_TENANT_ID,
        "user_id": "local-admin",
        "title": "Seeded",
        "filename": "seeded.md",
        "mime_type": "text/markdown",
        "artifact_format": "markdown",
        "size_bytes": 3,
        "storage_key": artifacts_service.new_storage_key(),
        "sha256": "x" * 64,
        "state": "active",
        "is_evidence_backed": False,
        "provenance_json": None,
    }
    fields.update(overrides)
    async with file_db() as db:
        artifact = ChatArtifact(**fields)
        db.add(artifact)
        await db.commit()
        await db.refresh(artifact)
        return artifact.id, artifact.storage_key


# ---------------------------------------------------------------------------
# Happy path + render boundary
# ---------------------------------------------------------------------------


async def test_create_and_download_html_is_attachment_never_inline(client, file_db):
    hostile = "<h1>Report</h1><script>alert('x')</script>"
    resp = await client.post(
        "/api/artifacts", json=_payload(format="html", content=hostile)
    )
    assert resp.status_code == 200, resp.text
    created = resp.json()
    assert created["artifact_format"] == "html"
    assert created["mime_type"] == "text/html"
    assert created["approved"] is False
    assert created["authoritative"] is False

    download = await client.get(f"/api/artifacts/{created['id']}/download")
    assert download.status_code == 200
    disposition = download.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert "inline" not in disposition.lower()
    # HTML is served with a restrictive CSP in addition to being an attachment.
    assert "Content-Security-Policy" in download.headers
    assert download.headers["x-content-type-options"] == "nosniff"
    # Content is served verbatim as data; it is never rendered or executed here.
    assert download.content.decode("utf-8") == hostile


async def test_markdown_text_and_csv_roundtrip(client):
    for fmt, content, mime in (
        ("markdown", "*hi*", "text/markdown"),
        ("text", "plain", "text/plain"),
        ("csv", "a,b\n1,2\n", "text/csv"),
    ):
        resp = await client.post(
            "/api/artifacts", json=_payload(format=fmt, content=content)
        )
        assert resp.status_code == 200, (fmt, resp.text)
        body = resp.json()
        assert body["mime_type"] == mime
        assert body["size_bytes"] == len(content.encode("utf-8"))
        download = await client.get(f"/api/artifacts/{body['id']}/download")
        assert download.status_code == 200
        assert download.content.decode("utf-8") == content


async def test_csv_must_be_tabular(client):
    # A ragged (non-rectangular) body is not a table.
    resp = await client.post(
        "/api/artifacts", json=_payload(format="csv", content="a,b\n1\n")
    )
    assert resp.status_code == 422, resp.text
    # An empty CSV is refused too.
    resp = await client.post(
        "/api/artifacts", json=_payload(format="csv", content="\n\n")
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Path traversal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["../x", "../../etc/passwd", "/etc/passwd", "..\\x", "C:\\x", "sub/dir.txt", ".", ".."],
)
def test_stored_filename_traversal_rejected(name):
    with pytest.raises(ArtifactNameError):
        artifacts_service.validate_stored_filename(name)


def test_storage_key_must_be_opaque():
    with pytest.raises(ArtifactNameError):
        artifacts_service.validate_storage_key("../etc/passwd")
    with pytest.raises(ArtifactNameError):
        artifacts_service.validate_storage_key("a/b")


def test_storage_read_cannot_escape_root(tmp_path):
    root = tmp_path / "artifacts"
    root.mkdir()
    # A file just outside the store that a traversal key would resolve to.
    (tmp_path / "secret.txt").write_text("TOPSECRET")
    store = ArtifactStorage(root)
    with pytest.raises(ArtifactError):
        store.read("../secret.txt")
    with pytest.raises(ArtifactError):
        store.read("../../secret.txt")
    with pytest.raises(ArtifactError):
        store.read("not-a-hex-key")


async def test_hostile_title_stays_contained(client, file_db, tmp_path):
    title = "../../etc/passwd \\ ..\\windows\\system32"
    resp = await client.post("/api/artifacts", json=_payload(title=title))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "/" not in body["filename"] and "\\" not in body["filename"]
    assert ".." not in body["filename"]

    # The row's storage key is opaque and its bytes live directly under the store.
    async with file_db() as db:
        row = await db.get(ChatArtifact, body["id"])
        key = row.storage_key
    assert re.fullmatch(r"[0-9a-f]{32}", key)
    assert (tmp_path / "artifacts" / key).exists()
    # No traversal target was created.
    assert not (tmp_path / "etc").exists()


# ---------------------------------------------------------------------------
# MIME allow-list and size bound
# ---------------------------------------------------------------------------


async def test_unsupported_format_rejected(client):
    resp = await client.post(
        "/api/artifacts",
        json={"title": "x", "format": "exe", "content": "MZ"},
    )
    assert resp.status_code == 422


async def test_mime_removed_from_allow_list_is_rejected(tmp_path, monkeypatch, client):
    monkeypatch.setattr(
        artifacts_service,
        "get_settings",
        lambda: _tiny_settings(tmp_path, artifact_allowed_mime_types="text/plain,text/csv"),
    )
    resp = await client.post(
        "/api/artifacts", json=_payload(format="html", content="<p>hi</p>")
    )
    assert resp.status_code == 422, resp.text
    # A still-allowed format is unaffected.
    ok = await client.post("/api/artifacts", json=_payload(format="text", content="hi"))
    assert ok.status_code == 200, ok.text


async def test_oversized_artifact_rejected(tmp_path, monkeypatch, client):
    monkeypatch.setattr(
        artifacts_service,
        "get_settings",
        lambda: _tiny_settings(tmp_path, max_artifact_bytes=10),
    )
    resp = await client.post(
        "/api/artifacts", json=_payload(format="text", content="x" * 64)
    )
    assert resp.status_code == 413, resp.text
    # A body within the bound still succeeds.
    ok = await client.post("/api/artifacts", json=_payload(format="text", content="ok"))
    assert ok.status_code == 200, ok.text


# ---------------------------------------------------------------------------
# Tenancy / ownership
# ---------------------------------------------------------------------------


async def test_cross_tenant_download_denied_isolating_tenant_rule(client, file_db):
    # Same owner key, DIFFERENT tenant: only the tenant predicate can catch this.
    other_id, _ = await _seed(
        file_db, tenant_id="tnt-other", user_id="local-admin"
    )
    resp = await client.get(f"/api/artifacts/{other_id}/download")
    assert resp.status_code == 404
    assert (await client.get(f"/api/artifacts/{other_id}")).status_code == 404
    listing = await client.get("/api/artifacts")
    assert listing.status_code == 200
    assert all(item["id"] != other_id for item in listing.json())


async def test_wrong_user_download_denied_isolating_ownership_rule(client, file_db):
    # Same tenant, DIFFERENT owner: the tenant predicate passes, ownership fails.
    other_id, _ = await _seed(file_db, tenant_id=LEGACY_TENANT_ID, user_id="someone-else")
    resp = await client.get(f"/api/artifacts/{other_id}/download")
    assert resp.status_code == 404
    assert (await client.get(f"/api/artifacts/{other_id}")).status_code == 404


# ---------------------------------------------------------------------------
# Deletion / retention state
# ---------------------------------------------------------------------------


async def test_deleted_artifact_unavailable(client):
    created = (await client.post("/api/artifacts", json=_payload())).json()
    deleted = await client.delete(f"/api/artifacts/{created['id']}")
    assert deleted.status_code == 200
    assert deleted.json()["state"] == "deleted"

    assert (await client.get(f"/api/artifacts/{created['id']}/download")).status_code == 404
    assert (await client.get(f"/api/artifacts/{created['id']}")).status_code == 404
    listing = await client.get("/api/artifacts")
    assert all(item["id"] != created["id"] for item in listing.json())
    # A second delete of a retired artifact is not a success either.
    assert (await client.delete(f"/api/artifacts/{created['id']}")).status_code == 404


async def test_revoked_artifact_unavailable(client, file_db):
    key = artifacts_service.new_storage_key()
    artifacts_service.storage().write(key, b"# gone")
    rev_id, _ = await _seed(file_db, state="revoked", storage_key=key)
    assert (await client.get(f"/api/artifacts/{rev_id}/download")).status_code == 404
    assert (await client.get(f"/api/artifacts/{rev_id}")).status_code == 404


# ---------------------------------------------------------------------------
# Not a governed report; evidence provenance is metadata only
# ---------------------------------------------------------------------------


async def test_ordinary_artifact_is_not_approved_or_authoritative(client):
    created = (await client.post("/api/artifacts", json=_payload())).json()
    assert created["is_evidence_backed"] is False
    assert created["approved"] is False
    assert created["authoritative"] is False
    provenance = created["provenance"]
    assert provenance["approved"] is False
    assert provenance["authoritative"] is False
    assert provenance["kind"] == "chat_artifact"
    assert "not" in provenance["label"].lower()


async def test_evidence_backed_artifact_preserves_citations_but_is_not_authoritative(client):
    evidence = [
        {
            "source_type": "document",
            "source_id": "doc-1",
            "title": "Policy",
            "passage": "FY2025 threshold USD 300",
        }
    ]
    created = (
        await client.post("/api/artifacts", json=_payload(evidence=evidence))
    ).json()
    assert created["is_evidence_backed"] is True
    assert created["approved"] is False and created["authoritative"] is False
    provenance = created["provenance"]
    assert provenance["approved"] is False
    assert provenance["authoritative"] is False
    assert provenance["as_of"]
    assert provenance["citations"] == [
        {
            "source_type": "document",
            "source_id": "doc-1",
            "title": "Policy",
            "evidence_id": None,
        }
    ]


async def test_linkage_to_foreign_conversation_denied(client):
    resp = await client.post(
        "/api/artifacts", json=_payload(conversation_id="does-not-exist")
    )
    assert resp.status_code == 404


async def test_linkage_is_recorded_for_owned_conversation(client, file_db):
    from app.models import Conversation

    async with file_db() as db:
        conv = Conversation(user_id="local-admin", tenant_id=LEGACY_TENANT_ID, title="C")
        db.add(conv)
        await db.commit()
        await db.refresh(conv)
        conv_id = conv.id

    created = (
        await client.post("/api/artifacts", json=_payload(conversation_id=conv_id))
    ).json()
    assert created["conversation_id"] == conv_id


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def test_migration_0018_adds_chat_artifacts(tmp_path):
    from alembic.script import ScriptDirectory

    from app.migrations_runner import (
        _alembic_config,
        adopt_and_upgrade,
        current_revision,
        sync_url_for,
    )

    url = f"sqlite+aiosqlite:///{tmp_path / 'bv5a_migration.db'}"
    adopt_and_upgrade(url)
    head = current_revision(url)
    assert head is not None

    # Head-agnostic: a later stacked package moves the chain forward, so assert
    # 0018 is IN the applied down_revision chain rather than pinning the head.
    script = ScriptDirectory.from_config(_alembic_config(sync_url_for(url)))
    chain: set[str] = set()
    cursor: str | None = script.get_current_head()
    while cursor:
        chain.add(cursor)
        revision = script.get_revision(cursor)
        cursor = revision.down_revision if revision else None
    assert "0018_chat_artifacts" in chain
    assert len("0018_chat_artifacts") <= 32

    engine = create_engine(sync_url_for(url), future=True)
    try:
        assert "chat_artifacts" in set(inspect(engine).get_table_names())
        config = _alembic_config(sync_url_for(url))
        command.downgrade(config, "0017_inf1b_admission")
        assert current_revision(url) == "0017_inf1b_admission"
        assert "chat_artifacts" not in set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
