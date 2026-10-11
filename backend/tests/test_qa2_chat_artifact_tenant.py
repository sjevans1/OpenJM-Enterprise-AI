"""QA2 Finding 4: a Chat conversation must be bound to the acting tenant.

The chat route created `Conversation(user_id=...)` without `tenant_id`, so the row
fell back to the model default (the legacy tenant). The artifact-linkage check is
tenant-scoped, so it then found no conversation and refused every Chat artifact
with a generic "artifact_error" for any principal acting outside the legacy
tenant. The offline suite never saw it because test requests resolve the
development principal, which lives in the legacy tenant where the default
coincides with the correct value.
"""

from __future__ import annotations

import json

from httpx import AsyncClient
from sqlalchemy import select

from app.core.tenancy import LEGACY_PRINCIPAL_ID, LEGACY_PRINCIPAL_SUBJECT
from app.models import Conversation, PrincipalAccount, Tenant, TenantMembership

TENANT = "tnt-qa2-chat-art"
TENANT_HEADER = "X-OpenJM-Tenant"

DIRECTIVE_ANSWER = (
    "Here is your file.\n\n"
    "```artifact\n"
    + json.dumps(
        {
            "title": "QA2 chat artifact",
            "format": "html",
            "content": "<html><body>qa2</body></html>",
        }
    )
    + "\n```\n"
)


async def _seed_tenant(file_db) -> None:
    async with file_db() as db:
        db.add(Tenant(id=TENANT, slug="qa2-chat-art", name="QA2 Chat Art", status="active"))
        if await db.get(PrincipalAccount, LEGACY_PRINCIPAL_ID) is None:
            db.add(
                PrincipalAccount(
                    id=LEGACY_PRINCIPAL_ID,
                    subject=LEGACY_PRINCIPAL_SUBJECT,
                    issuer="openjm-local",
                    display_name="Local administrator",
                    status="active",
                )
            )
        db.add(
            TenantMembership(
                tenant_id=TENANT,
                principal_id=LEGACY_PRINCIPAL_ID,
                role="owner",
                status="active",
                version=1,
            )
        )
        await db.commit()


def _stub_gateway(monkeypatch):
    class _Gateway:
        async def chat(self, messages, **kwargs):
            return DIRECTIVE_ANSWER

    monkeypatch.setattr("app.api.chat.model_gateway", _Gateway())


async def test_chat_turn_binds_conversation_to_the_acting_tenant(
    client: AsyncClient, file_db, monkeypatch
):
    await _seed_tenant(file_db)
    _stub_gateway(monkeypatch)

    response = await client.post(
        "/api/chat",
        json={"message": "Please make me a downloadable file.", "mode": "chat"},
        headers={TENANT_HEADER: TENANT},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    async with file_db() as db:
        conversation = (
            await db.execute(
                select(Conversation).where(Conversation.user_id == f"{TENANT}:{LEGACY_PRINCIPAL_ID}")
            )
        ).scalars().first()
        assert conversation is not None, "the turn must create a conversation for the acting principal"
        assert conversation.tenant_id == TENANT, conversation.tenant_id

    # The tenant-scoped linkage check must therefore succeed and the artifact be created.
    assert body["artifacts"], f"artifact not created: {body.get('answer', '')[-200:]!r}"
    assert body["artifacts"][0]["mime_type"] == "text/html"
