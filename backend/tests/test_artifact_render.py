"""BV5-B: controlled PDF/DOCX rendering of Chat artifacts.

These tests drive the real router and services on a file-backed SQLite database.
The rendered output is a real file: a PDF opens via ``pypdf`` and a DOCX opens
via ``python-docx``. The properties under test are:

* a valid render carries real magic bytes (``%PDF`` / ``PK`` zip);
* an evidence-backed artifact's citations and as-of provenance are rendered into
  the document;
* hostile content (script/HTML, control characters, template tokens) is inert
  text that cannot escape the render or storage boundary;
* an external URL / ``<img src=...>`` reference is never fetched;
* download authorization is the same owner+tenant gate as BV5-A;
* a render failure leaves NO phantom artifact: no DB row and no storage write,
  and the API returns an error instead of a false success;
* the rendered bytes are size-checked against ``max_artifact_bytes``.
"""

from __future__ import annotations

import io
import socket

import pytest
from docx import Document
from pypdf import PdfReader
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.tenancy import LEGACY_TENANT_ID
from app.models import ChatArtifact
from app.services import artifacts as artifacts_service
from app.services import artifacts_render as render_service


@pytest.fixture(autouse=True)
def _artifact_settings(tmp_path, monkeypatch):
    """Hermetic artifact storage: never touch the repo's data directory."""
    base = get_settings()
    fake = base.model_copy(update={"artifacts_dir": tmp_path / "artifacts"})
    monkeypatch.setattr(artifacts_service, "get_settings", lambda: fake)
    return fake


def _tiny_settings(tmp_path, **updates):
    base = get_settings()
    return base.model_copy(update={"artifacts_dir": tmp_path / "artifacts", **updates})


def _payload(**overrides):
    body = {
        "title": "Weekly summary",
        "format": "markdown",
        "content": "# Weekly summary\n\nAll good.\n",
    }
    body.update(overrides)
    return body


async def _seed(file_db, **overrides):
    """Persist a source artifact row directly, bypassing the API."""
    key = artifacts_service.new_storage_key()
    fields = {
        "tenant_id": LEGACY_TENANT_ID,
        "user_id": "local-admin",
        "title": "Seeded",
        "filename": "seeded.md",
        "mime_type": "text/markdown",
        "artifact_format": "markdown",
        "size_bytes": 3,
        "storage_key": key,
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
        return artifact.id, key


async def _count(file_db) -> int:
    async with file_db() as db:
        return (await db.execute(select(func.count()).select_from(ChatArtifact))).scalar_one()


def _files(tmp_path):
    root = tmp_path / "artifacts"
    return sorted(p.name for p in root.glob("*")) if root.exists() else []


def _docx_text(data: bytes) -> str:
    doc = Document(io.BytesIO(data))
    parts = [p.text for p in doc.paragraphs]
    return "\n".join(parts)


def _pdf_text(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


# ---------------------------------------------------------------------------
# Valid render: real magic bytes + opens with the real parser
# ---------------------------------------------------------------------------


async def test_render_pdf_is_a_real_pdf_that_opens(client):
    created = (await client.post("/api/artifacts", json=_payload())).json()
    resp = await client.get(f"/api/artifacts/{created['id']}/render/pdf")
    assert resp.status_code == 200, resp.text
    assert resp.content[:5] == b"%PDF-"
    assert resp.headers["content-type"] == "application/pdf"
    disposition = resp.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert disposition.endswith('.pdf"')
    assert resp.headers["x-content-type-options"] == "nosniff"
    reader = PdfReader(io.BytesIO(resp.content))
    assert len(reader.pages) >= 1
    assert "Weekly summary" in _pdf_text(resp.content)


async def test_render_docx_is_a_real_docx_that_opens(client):
    created = (await client.post("/api/artifacts", json=_payload())).json()
    resp = await client.get(f"/api/artifacts/{created['id']}/render/docx")
    assert resp.status_code == 200, resp.text
    assert resp.content[:2] == b"PK"
    assert (
        resp.headers["content-type"]
        == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    disposition = resp.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert disposition.endswith('.docx"')
    text = _docx_text(resp.content)
    assert "Weekly summary" in text
    assert "All good." in text


# ---------------------------------------------------------------------------
# Evidence / citation + as-of provenance retention
# ---------------------------------------------------------------------------


async def test_render_renders_citations_and_as_of(client):
    evidence = [
        {
            "source_type": "document",
            "source_id": "doc-1",
            "title": "FY2025 Policy",
            "passage": "threshold USD 300",
        }
    ]
    created = (
        await client.post("/api/artifacts", json=_payload(evidence=evidence))
    ).json()
    assert created["is_evidence_backed"] is True
    as_of = created["provenance"]["as_of"]

    docx = (await client.get(f"/api/artifacts/{created['id']}/render/docx")).content
    text = _docx_text(docx)
    assert "FY2025 Policy" in text
    assert "doc-1" in text
    assert as_of in text
    # Still explicitly NOT a governed / approved report.
    assert "not a governed report" in text.lower()

    pdf_text = _pdf_text(
        (await client.get(f"/api/artifacts/{created['id']}/render/pdf")).content
    )
    assert "FY2025 Policy" in pdf_text
    assert "doc-1" in pdf_text


async def test_ordinary_artifact_render_is_not_approved(client):
    created = (await client.post("/api/artifacts", json=_payload())).json()
    text = _docx_text(
        (await client.get(f"/api/artifacts/{created['id']}/render/docx")).content
    )
    assert "not a governed report" in text.lower()


# ---------------------------------------------------------------------------
# Target vocabulary + storage boundary
# ---------------------------------------------------------------------------


async def test_only_pdf_and_docx_are_render_targets(client):
    created = (await client.post("/api/artifacts", json=_payload())).json()
    for bad in ("html", "exe", "txt"):
        resp = await client.get(f"/api/artifacts/{created['id']}/render/{bad}")
        assert resp.status_code == 422, (bad, resp.text)


async def test_pdf_and_docx_are_in_the_format_vocabulary():
    assert artifacts_service.FORMAT_MIME["pdf"] == "application/pdf"
    assert (
        artifacts_service.FORMAT_MIME["docx"]
        == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert artifacts_service.FORMAT_EXTENSION["pdf"] == "pdf"
    assert artifacts_service.FORMAT_EXTENSION["docx"] == "docx"
    assert artifacts_service.safe_filename("Some / hostile \\ title", "pdf").endswith(".pdf")
    assert artifacts_service.safe_filename("Some / hostile \\ title", "docx").endswith(".docx")


async def test_pdf_docx_cannot_be_stored_as_artifact_rows(client):
    """A render target is never a persistable artifact_format (schema boundary)."""
    for fmt in ("pdf", "docx"):
        resp = await client.post(
            "/api/artifacts", json={"title": "x", "format": fmt, "content": "hi"}
        )
        assert resp.status_code == 422, (fmt, resp.text)


# ---------------------------------------------------------------------------
# Hostile content is inert
# ---------------------------------------------------------------------------


async def test_hostile_content_is_inert_in_docx(client):
    hostile = (
        "<script>alert('x')</script>\n"
        "<img src='http://169.254.169.254/latest/meta-data/'>\n"
        "{{7*7}} ${1+1} <%= 2+2 %>\n"
        "__import__('os').system('echo pwned')\n"
        "bad\x00control\x07chars\n"
    )
    created = (
        await client.post("/api/artifacts", json=_payload(format="html", content=hostile))
    ).json()
    resp = await client.get(f"/api/artifacts/{created['id']}/render/docx")
    assert resp.status_code == 200, resp.text
    text = _docx_text(resp.content)
    # Script/HTML arrives as literal, inert text.
    assert "<script>alert('x')</script>" in text
    assert "http://169.254.169.254/latest/meta-data/" in text
    # Template tokens are NOT evaluated.
    assert "{{7*7}}" in text and "${1+1}" in text and "<%= 2+2 %>" in text
    assert "49" not in text.split("As of")[0]
    assert "__import__('os').system('echo pwned')" in text
    # Control characters cannot survive into the document.
    assert "\x00" not in text and "\x07" not in text


async def test_hostile_content_is_inert_in_pdf(client):
    hostile = "<script>alert('x')</script> {{7*7}} \x00 \x07"
    created = (
        await client.post("/api/artifacts", json=_payload(format="html", content=hostile))
    ).json()
    resp = await client.get(f"/api/artifacts/{created['id']}/render/pdf")
    assert resp.status_code == 200, resp.text
    assert resp.content[:5] == b"%PDF-"
    text = _pdf_text(resp.content)
    assert "alert('x')" in text
    assert "{{7*7}}" in text


async def test_render_never_fetches_a_remote_asset(client, monkeypatch):
    """An external URL in content is rendered as text; no network egress occurs."""

    def _no_network(*_args, **_kwargs):  # pragma: no cover - only fires on failure
        raise AssertionError("render attempted a network connection")

    monkeypatch.setattr(socket, "create_connection", _no_network)
    monkeypatch.setattr(socket, "socket", _no_network)
    created = (
        await client.post(
            "/api/artifacts",
            json=_payload(
                content="See http://example.com/evil.png and <img src='http://example.com/x.png'>"
            ),
        )
    ).json()
    for target in ("pdf", "docx"):
        resp = await client.get(f"/api/artifacts/{created['id']}/render/{target}")
        assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# Render failure: no phantom artifact, no stray file
# ---------------------------------------------------------------------------


async def test_render_failure_leaves_no_row_and_no_file(client, file_db, tmp_path, monkeypatch):
    created = (await client.post("/api/artifacts", json=_payload())).json()
    before_rows = await _count(file_db)
    before_files = _files(tmp_path)
    assert before_files  # the source artifact really is on disk

    def _boom(**_kwargs):
        raise render_service.ArtifactRenderError("render exploded")

    monkeypatch.setattr(render_service, "render_artifact", _boom)
    resp = await client.get(f"/api/artifacts/{created['id']}/render/pdf")
    assert resp.status_code >= 400
    assert resp.status_code != 200

    assert await _count(file_db) == before_rows
    assert _files(tmp_path) == before_files


async def test_oversized_render_is_rejected_and_leaves_nothing(client, file_db, tmp_path, monkeypatch):
    monkeypatch.setattr(
        artifacts_service,
        "get_settings",
        lambda: _tiny_settings(tmp_path, max_artifact_bytes=200),
    )
    created = (await client.post("/api/artifacts", json=_payload(content="hi"))).json()
    before_rows = await _count(file_db)
    before_files = _files(tmp_path)

    resp = await client.get(f"/api/artifacts/{created['id']}/render/pdf")
    assert resp.status_code == 413, resp.text
    assert await _count(file_db) == before_rows
    assert _files(tmp_path) == before_files


# ---------------------------------------------------------------------------
# Authorization: same owner+tenant gate as the download path
# ---------------------------------------------------------------------------


async def test_render_cross_tenant_denied(client, file_db):
    other_id, _ = await _seed(file_db, tenant_id="tnt-other", user_id="local-admin")
    resp = await client.get(f"/api/artifacts/{other_id}/render/pdf")
    assert resp.status_code == 404


async def test_render_wrong_user_denied(client, file_db):
    other_id, _ = await _seed(file_db, tenant_id=LEGACY_TENANT_ID, user_id="someone-else")
    resp = await client.get(f"/api/artifacts/{other_id}/render/docx")
    assert resp.status_code == 404


async def test_render_deleted_artifact_unavailable(client):
    created = (await client.post("/api/artifacts", json=_payload())).json()
    await client.delete(f"/api/artifacts/{created['id']}")
    resp = await client.get(f"/api/artifacts/{created['id']}/render/pdf")
    assert resp.status_code == 404


async def test_render_unknown_artifact_404(client, file_db):
    resp = await client.get("/api/artifacts/does-not-exist/render/pdf")
    assert resp.status_code == 404
