"""BV5-C: the end-to-end Chat artifact journey.

These tests drive the *real* routers and services on a file-backed SQLite
database. The model is reduced to its only input surface — an answer string that
may contain one bounded directive block — so "hostile model output" is modelled
by feeding adversarial directives rather than by mocking a model.

The properties under test:

* a natural Chat request produces a real, downloadable artifact reference on the
  assistant message (metadata only — never raw source in the transcript);
* the model's suggested filename/path can never control the storage path;
* unsupported formats, non-tabular CSV and oversized output fail closed with a
  bounded note and no phantom artifact;
* artifact generation performs no network egress;
* owner/tenant isolation, linkage cannot be forged, deletion revokes download;
* tool/action retries do not duplicate artifacts (idempotency);
* evidence-backed output preserves citations and is never labelled approved.

The store is redirected to a per-test tmp directory so nothing touches the repo.
"""

from __future__ import annotations

import json
import re
import socket
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.context import set_principal
from app.core.identity import Principal
from app.core.permissions import permissions_for_role
from app.core.tenancy import LEGACY_PRINCIPAL_ID, LEGACY_TENANT_ID
from app.models import ChatArtifact, Conversation, Message
from app.schemas import Evidence
from app.services import artifact_journey as journey
from app.services import artifacts as artifacts_service


@pytest.fixture(autouse=True)
def _artifact_settings(tmp_path, monkeypatch):
    """Hermetic artifact storage: never touch the repo's data directory."""
    base = get_settings()
    fake = base.model_copy(update={"artifacts_dir": tmp_path / "artifacts"})
    monkeypatch.setattr(artifacts_service, "get_settings", lambda: fake)
    return fake


# ---------------------------------------------------------------------------
# Model + orchestrator stubs
# ---------------------------------------------------------------------------


class AnsweringGateway:
    def __init__(self, answer: str):
        self.answer = answer
        self.calls = 0
        self.messages = []

    async def chat(self, messages, **kwargs):
        self.calls += 1
        self.messages.append(messages)
        return self.answer


class StubOrchestrator:
    """General-mode plan with a system prompt, so the gateway is invoked."""

    def __init__(self, evidence=None):
        self._evidence = evidence or []

    async def plan(
        self,
        message,
        db,
        user_id,
        conversation_id=None,
        mode="chat",
        scope=None,
        request_id=None,
        budget=None,
        access=None,
    ):
        return SimpleNamespace(
            execution_class="general",
            system_prompt="You are OpenJM Enterprise AI.",
            evidence=list(self._evidence),
            direct_answer=None,
            requested_mode=mode,
            structured_result=None,
            trace_ids=[],
        )


def directive(fmt: str, content: str, title: str = "Weekly summary") -> str:
    """A realistic model answer that also proposes an artifact."""
    return (
        "Here is the file you asked for.\n"
        "```artifact\n"
        + json.dumps({"title": title, "format": fmt, "content": content})
        + "\n```\n"
        "Let me know if you need changes."
    )


def _filename_from_disposition(value: str) -> str:
    match = re.search(r'filename="([^"]+)"', value)
    assert match, value
    return match.group(1)


async def _count_artifacts(file_db) -> int:
    async with file_db() as db:
        return (
            await db.execute(select(func.count()).select_from(ChatArtifact))
        ).scalar_one()


# ---------------------------------------------------------------------------
# Happy path: a natural Chat request yields a real download
# ---------------------------------------------------------------------------


async def test_chat_request_yields_downloadable_artifact_without_source_in_transcript(
    client, file_db, monkeypatch
):
    monkeypatch.setattr("app.api.chat.orchestrator", StubOrchestrator())
    monkeypatch.setattr(
        "app.api.chat.model_gateway", AnsweringGateway(directive("markdown", "# Report\n\nBody."))
    )

    resp = await client.post("/api/chat", json={"message": "make me a markdown file"})
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # The directive block is stripped: no raw source in the transcript.
    assert "```artifact" not in body["answer"]
    assert "Here is the file you asked for." in body["answer"]

    artifacts = body["artifacts"]
    assert len(artifacts) == 1
    card = artifacts[0]
    assert card["filename"] == "weekly-summary.md"
    assert card["mime_type"] == "text/markdown"
    assert card["size_bytes"] == len("# Report\n\nBody.".encode("utf-8"))
    assert card["state"] == "active"
    assert card["conversation_id"] == body["conversation_id"]
    assert card["message_id"] == body["message_id"]
    # No content / key / base64 leaked into the response.
    assert "storage_key" not in card
    assert "content" not in card

    download = await client.get(f"/api/artifacts/{card['id']}/download")
    assert download.status_code == 200
    assert download.headers["content-disposition"].startswith("attachment;")
    assert download.content.decode("utf-8") == "# Report\n\nBody."


async def test_non_chat_modes_neither_prompt_for_nor_execute_artifact_directives(
    client, file_db, monkeypatch
):
    monkeypatch.setattr("app.api.chat.orchestrator", StubOrchestrator())
    gateway = AnsweringGateway(directive("markdown", "# Must stay inert"))
    monkeypatch.setattr("app.api.chat.model_gateway", gateway)

    resp = await client.post(
        "/api/chat",
        json={"message": "answer from governed knowledge", "mode": "knowledge"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["mode"] == "knowledge"
    assert body["artifacts"] == []
    assert await _count_artifacts(file_db) == 0
    # The non-Chat system prompt must not teach the model the artifact protocol.
    assert "downloadable file or artifact" not in gateway.messages[0][0]["content"]
    assert "artifact" not in gateway.messages[0][0]["content"].lower()
    # Defense in depth: even if the model emits the reserved fence anyway, the
    # server does not interpret/strip/execute it outside Chat mode.
    assert "```artifact" in body["answer"]
    assert "# Must stay inert" in body["answer"]


async def test_reopened_conversation_hydrates_the_artifact_card(client, monkeypatch):
    monkeypatch.setattr("app.api.chat.orchestrator", StubOrchestrator())
    monkeypatch.setattr(
        "app.api.chat.model_gateway", AnsweringGateway(directive("text", "plain body"))
    )
    created = (await client.post("/api/chat", json={"message": "a txt please"})).json()

    detail = (await client.get(f"/api/conversations/{created['conversation_id']}")).json()
    assistant = [m for m in detail["messages"] if m["role"] == "assistant"]
    assert len(assistant) == 1
    assert [a["id"] for a in assistant[0]["artifacts"]] == [created["artifacts"][0]["id"]]
    # The persisted content is the cleaned answer, not the directive.
    assert "```artifact" not in assistant[0]["content"]


# ---------------------------------------------------------------------------
# Format / content boundaries fail closed
# ---------------------------------------------------------------------------


async def test_csv_only_when_genuinely_tabular(client, file_db, monkeypatch):
    monkeypatch.setattr("app.api.chat.orchestrator", StubOrchestrator())
    monkeypatch.setattr(
        "app.api.chat.model_gateway", AnsweringGateway(directive("csv", "a,b\n1\n"))
    )
    resp = await client.post("/api/chat", json={"message": "export csv"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["artifacts"] == []
    assert await _count_artifacts(file_db) == 0
    # Bounded failure note; the turn itself is intact.
    assert "could not create the requested downloadable file" in body["answer"]
    assert "Here is the file you asked for." in body["answer"]

    # A genuine table succeeds.
    monkeypatch.setattr(
        "app.api.chat.model_gateway", AnsweringGateway(directive("csv", "a,b\n1,2\n"))
    )
    ok = await client.post("/api/chat", json={"message": "export csv"})
    assert ok.json()["artifacts"][0]["mime_type"] == "text/csv"


@pytest.mark.parametrize("fmt", ["exe", "xlsx", "pdf", "docx", "zip"])
async def test_unsupported_format_fails_closed(client, file_db, monkeypatch, fmt):
    monkeypatch.setattr("app.api.chat.orchestrator", StubOrchestrator())
    monkeypatch.setattr(
        "app.api.chat.model_gateway", AnsweringGateway(directive(fmt, "whatever"))
    )
    resp = await client.post("/api/chat", json={"message": "make it"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["artifacts"] == []
    assert await _count_artifacts(file_db) == 0


async def test_oversized_output_fails_closed(client, file_db, monkeypatch):
    base = get_settings()
    monkeypatch.setattr(
        artifacts_service,
        "get_settings",
        lambda: base.model_copy(
            update={"artifacts_dir": base.artifacts_dir, "max_artifact_bytes": 128}
        ),
    )
    monkeypatch.setattr("app.api.chat.orchestrator", StubOrchestrator())
    monkeypatch.setattr(
        "app.api.chat.model_gateway", AnsweringGateway(directive("text", "x" * 4096))
    )
    resp = await client.post("/api/chat", json={"message": "big file"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["artifacts"] == []
    assert await _count_artifacts(file_db) == 0
    assert "could not create the requested downloadable file" in resp.json()["answer"]


async def test_malformed_directive_does_not_corrupt_the_conversation(client, monkeypatch):
    monkeypatch.setattr("app.api.chat.orchestrator", StubOrchestrator())
    # Unterminated fence: present but unusable.
    monkeypatch.setattr(
        "app.api.chat.model_gateway",
        AnsweringGateway("Here you go.\n```artifact\n{not json"),
    )
    resp = await client.post("/api/chat", json={"message": "make it"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["artifacts"] == []

    detail = (await client.get(f"/api/conversations/{body['conversation_id']}")).json()
    roles = [m["role"] for m in detail["messages"]]
    assert roles == ["user", "assistant"]


# ---------------------------------------------------------------------------
# The model cannot choose the path; generation performs no network egress
# ---------------------------------------------------------------------------


async def test_model_suggested_filename_cannot_control_the_storage_path(
    client, file_db, tmp_path, monkeypatch
):
    hostile_title = "../../etc/passwd \\ ..\\windows\\system32"
    monkeypatch.setattr("app.api.chat.orchestrator", StubOrchestrator())
    monkeypatch.setattr(
        "app.api.chat.model_gateway",
        AnsweringGateway(directive("markdown", "body", title=hostile_title)),
    )
    body = (await client.post("/api/chat", json={"message": "make it"})).json()
    card = body["artifacts"][0]
    assert "/" not in card["filename"] and "\\" not in card["filename"]
    assert ".." not in card["filename"]

    async with file_db() as db:
        row = await db.get(ChatArtifact, card["id"])
        key = row.storage_key
    assert re.fullmatch(r"[0-9a-f]{32}", key)
    root = tmp_path / "artifacts"
    assert (root / key).exists()
    # No traversal target was ever created.
    assert not (tmp_path / "etc").exists()
    assert {p.name for p in root.iterdir()} == {key}


async def test_generation_never_performs_network_egress(client, monkeypatch):
    def _no_network(*_args, **_kwargs):  # pragma: no cover - only fires on failure
        raise AssertionError("artifact generation attempted a network connection")

    monkeypatch.setattr(socket, "socket", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)
    monkeypatch.setattr("app.api.chat.orchestrator", StubOrchestrator())
    monkeypatch.setattr(
        "app.api.chat.model_gateway",
        AnsweringGateway(
            directive("html", "<img src='http://169.254.169.254/x.png'> see http://evil.example")
        ),
    )
    body = (await client.post("/api/chat", json={"message": "fetch and make html"})).json()
    card = body["artifacts"][0]
    download = await client.get(f"/api/artifacts/{card['id']}/download")
    # The remote reference is stored verbatim as inert data; nothing was fetched.
    assert "http://evil.example" in download.content.decode("utf-8")
    assert download.headers["content-disposition"].startswith("attachment;")
    assert "Content-Security-Policy" in download.headers


# ---------------------------------------------------------------------------
# Rendering (PDF / DOCX) from a Chat artifact
# ---------------------------------------------------------------------------


async def test_chat_artifact_renders_to_pdf_and_docx(client, monkeypatch):
    monkeypatch.setattr("app.api.chat.orchestrator", StubOrchestrator())
    monkeypatch.setattr(
        "app.api.chat.model_gateway",
        AnsweringGateway(directive("html", "<h1>Hostile</h1><script>alert(1)</script>")),
    )
    card = (await client.post("/api/chat", json={"message": "make html"})).json()["artifacts"][0]

    pdf = await client.get(f"/api/artifacts/{card['id']}/render/pdf")
    assert pdf.status_code == 200, pdf.text
    assert pdf.content[:5] == b"%PDF-"
    docx = await client.get(f"/api/artifacts/{card['id']}/render/docx")
    assert docx.status_code == 200, docx.text
    assert docx.content[:2] == b"PK"


async def test_failed_render_leaves_the_single_card_and_no_phantom(
    client, file_db, monkeypatch
):
    from app.services import artifacts_render as render_service

    monkeypatch.setattr("app.api.chat.orchestrator", StubOrchestrator())
    monkeypatch.setattr(
        "app.api.chat.model_gateway", AnsweringGateway(directive("markdown", "body"))
    )
    created = (await client.post("/api/chat", json={"message": "make it"})).json()
    before = await _count_artifacts(file_db)

    def _boom(**_kwargs):
        raise render_service.ArtifactRenderError("render exploded")

    monkeypatch.setattr(render_service, "render_artifact", _boom)
    resp = await client.get(f"/api/artifacts/{created['artifacts'][0]['id']}/render/pdf")
    assert resp.status_code >= 400
    assert await _count_artifacts(file_db) == before

    # Reopening still shows exactly the one real card.
    detail = (await client.get(f"/api/conversations/{created['conversation_id']}")).json()
    assistant = [m for m in detail["messages"] if m["role"] == "assistant"][0]
    assert len(assistant["artifacts"]) == 1


# ---------------------------------------------------------------------------
# Isolation, linkage, deletion
# ---------------------------------------------------------------------------


async def test_cross_tenant_and_wrong_user_chat_artifact_denied(client, file_db):
    async def seed(**overrides):
        fields = {
            "tenant_id": LEGACY_TENANT_ID,
            "user_id": LEGACY_PRINCIPAL_ID,
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
            row = ChatArtifact(**fields)
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return row.id

    cross_tenant = await seed(tenant_id="tnt-other", user_id=LEGACY_PRINCIPAL_ID)
    wrong_user = await seed(tenant_id=LEGACY_TENANT_ID, user_id="someone-else")
    for artifact_id in (cross_tenant, wrong_user):
        assert (
            await client.get(f"/api/artifacts/{artifact_id}/download")
        ).status_code == 404
        assert (await client.get(f"/api/artifacts/{artifact_id}")).status_code == 404


async def test_linkage_cannot_be_forged(client, file_db):
    # A conversation owned by someone else.
    async with file_db() as db:
        foreign = Conversation(
            user_id="someone-else", tenant_id=LEGACY_TENANT_ID, title="Theirs"
        )
        db.add(foreign)
        await db.commit()
        await db.refresh(foreign)
        foreign_id = foreign.id

    resp = await client.post(
        "/api/artifacts",
        json={
            "title": "x",
            "format": "markdown",
            "content": "body",
            "conversation_id": foreign_id,
        },
    )
    assert resp.status_code == 404

    # Two owned conversations, but the message belongs to the other one.
    async with file_db() as db:
        owned_a = Conversation(user_id=LEGACY_PRINCIPAL_ID, tenant_id=LEGACY_TENANT_ID, title="A")
        owned_b = Conversation(user_id=LEGACY_PRINCIPAL_ID, tenant_id=LEGACY_TENANT_ID, title="B")
        db.add_all([owned_a, owned_b])
        await db.flush()
        message = Message(conversation_id=owned_b.id, role="user", content="hi")
        db.add(message)
        await db.commit()
        await db.refresh(owned_a)
        await db.refresh(message)
        a_id, message_id = owned_a.id, message.id

    resp = await client.post(
        "/api/artifacts",
        json={
            "title": "x",
            "format": "markdown",
            "content": "body",
            "conversation_id": a_id,
            "message_id": message_id,
        },
    )
    assert resp.status_code == 422


async def test_deletion_revokes_download_and_card(client, monkeypatch):
    monkeypatch.setattr("app.api.chat.orchestrator", StubOrchestrator())
    monkeypatch.setattr(
        "app.api.chat.model_gateway", AnsweringGateway(directive("markdown", "body"))
    )
    created = (await client.post("/api/chat", json={"message": "make it"})).json()
    artifact_id = created["artifacts"][0]["id"]

    deleted = await client.delete(f"/api/artifacts/{artifact_id}")
    assert deleted.status_code == 200
    assert deleted.json()["state"] == "deleted"

    assert (await client.get(f"/api/artifacts/{artifact_id}/download")).status_code == 404
    assert (await client.get(f"/api/artifacts/{artifact_id}/render/pdf")).status_code == 404

    detail = (await client.get(f"/api/conversations/{created['conversation_id']}")).json()
    assistant = [m for m in detail["messages"] if m["role"] == "assistant"][0]
    assert assistant["artifacts"] == []


# ---------------------------------------------------------------------------
# Provenance: evidence-backed but never approved/authoritative
# ---------------------------------------------------------------------------


async def test_evidence_backed_chat_artifact_keeps_citations_but_is_not_authoritative(
    client, monkeypatch
):
    evidence = [
        Evidence(
            source_type="document",
            source_id="doc-1",
            title="FY2025 Policy",
            passage="threshold USD 300",
        )
    ]
    monkeypatch.setattr("app.api.chat.orchestrator", StubOrchestrator(evidence))
    monkeypatch.setattr(
        "app.api.chat.model_gateway", AnsweringGateway(directive("markdown", "# Answer"))
    )
    body = (await client.post("/api/chat", json={"message": "summarise the policy"})).json()
    card = body["artifacts"][0]
    assert card["is_evidence_backed"] is True

    detail = (await client.get(f"/api/artifacts/{card['id']}")).json()
    assert detail["approved"] is False
    assert detail["authoritative"] is False
    provenance = detail["provenance"]
    assert provenance["approved"] is False
    assert provenance["authoritative"] is False
    assert provenance["kind"] == "chat_artifact"
    assert provenance["as_of"]
    assert provenance["citations"] == [
        {
            "source_type": "document",
            "source_id": "doc-1",
            "title": "FY2025 Policy",
            "evidence_id": None,
        }
    ]


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


async def test_persist_is_idempotent_for_the_same_content_and_linkage(file_db):
    principal = Principal(
        principal_id=LEGACY_PRINCIPAL_ID,
        tenant_id=LEGACY_TENANT_ID,
        subject="test",
        role="owner",
        membership_id="m1",
        auth_method="local-dev",
        permissions=permissions_for_role("owner"),
    )
    set_principal(principal)
    async with file_db() as db:
        first = await journey.persist_chat_artifact(
            db, principal, title="T", fmt="markdown", content="# hi"
        )
        await db.commit()
        first_id = first.id
    async with file_db() as db:
        again = await journey.persist_chat_artifact(
            db, principal, title="T", fmt="markdown", content="# hi"
        )
        await db.commit()
    assert again.id == first_id
    assert await _count_artifacts(file_db) == 1


async def test_action_retries_do_not_duplicate_artifacts(file_db, tmp_path, monkeypatch):
    from app.models import Tenant
    from app.services.actions import runtime

    from app.services.actions.builtin import registry

    tenant = LEGACY_TENANT_ID
    principal = Principal(
        principal_id="art-user",
        tenant_id=tenant,
        subject="test:art-user",
        role="owner",
        membership_id="m1",
        auth_method="local-dev",
        permissions=permissions_for_role("owner"),
    )
    set_principal(principal)
    async with file_db() as db:
        db.add(Tenant(id=tenant, slug="legacy", name="Legacy", status="active"))
        try:
            await db.commit()
        except Exception:
            await db.rollback()

    assert "artifact.create" in registry.names()

    async with file_db() as db:
        plan = await runtime.propose_plan(
            db,
            principal,
            goal="make a file",
            proposal=json.dumps(
                {
                    "steps": [
                        {
                            "tool": "artifact.create",
                            "arguments": {
                                "title": "T",
                                "format": "markdown",
                                "content": "# hi",
                            },
                        }
                    ]
                }
            ),
        )
        plan_id = plan.id
        await db.commit()

    async with file_db() as db:
        first = await runtime.execute_plan(db, principal, plan_id=plan_id)
        assert first["steps"][0]["status"] == "succeeded", first
        assert first["steps"][0]["result"]["ok"] is True
        assert first["steps"][0]["result"]["artifact_id"]

    # Replay the same plan: the recorded outcome is reused, not re-executed.
    async with file_db() as db:
        from app.models import ActionPlan

        plan = await db.get(ActionPlan, plan_id)
        plan.status = "proposed"
        await db.commit()
    async with file_db() as db:
        second = await runtime.execute_plan(db, principal, plan_id=plan_id)
    assert second["steps"][0]["reused"] is True
    assert await _count_artifacts(file_db) == 1


async def test_tool_runaway_path_argument_is_rejected(file_db):
    """artifact.create cannot be handed a filesystem path or URL argument."""
    from app.services.actions.registry import RegistryError

    from app.services.actions.builtin import registry

    tool = registry.get("artifact.create")
    with pytest.raises(RegistryError):
        tool.spec.validate_arguments(
            {"title": "T", "format": "markdown", "content": "x", "path": "/etc/passwd"}
        )
    with pytest.raises(RegistryError):
        tool.spec.validate_arguments({"format": "markdown", "content": "x"})  # no title
