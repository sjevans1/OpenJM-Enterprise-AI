"""QA2 Finding 4 diagnostics: a refused Chat artifact must leave a server trace.

Before this change a refused artifact produced only a generic user-facing note,
with no server-side record of why. A live QA failure could not then be diagnosed
from evidence. The bounded category is now logged (category plus linkage ids only
never the directive body, content or model text).
"""

from __future__ import annotations

import json
import logging

from app.core.identity import AuthMethod, Principal
from app.core.permissions import permissions_for_role
from app.services import artifact_journey as AJ

TENANT = "tnt-qa2-art"
MEMBER = "qa2-art-member"


def _directive(fmt: str, content: str) -> str:
    payload = {"title": "QA2 refusal probe", "format": fmt, "content": content}
    return "Here you go.\n\n```artifact\n" + json.dumps(payload) + "\n```\n"


async def test_refused_artifact_logs_a_bounded_category(caplog, file_db):
    # Oversized content is a deterministic, server-side refusal.
    oversized = "x" * (2_000_001)
    principal = Principal(
        principal_id=MEMBER,
        tenant_id=TENANT,
        subject=f"sub:{MEMBER}",
        role="viewer",
        membership_id="m-1",
        auth_method=AuthMethod.OIDC.value,
        permissions=permissions_for_role("viewer"),
    )
    async with file_db() as db:
        with caplog.at_level(logging.WARNING, logger="openjm.artifacts"):
            outcome = await AJ.maybe_create_artifact(
                db, principal, text=_directive("html", oversized),
                conversation_id=None, message_id=None,
            )

    assert outcome.artifact is None, "an oversized artifact must not be stored"
    assert outcome.failure, "a refusal must carry a bounded failure category"
    records = [r for r in caplog.records if r.name == "openjm.artifacts"]
    assert records, "a refusal must be recorded server-side"
    message = records[0].getMessage()
    assert "chat artifact refused" in message
    assert outcome.failure in message
    # The log must never carry the artifact body.
    assert oversized[:64] not in message


async def test_malformed_directive_logs_a_bounded_category(caplog, file_db):
    principal = Principal(
        principal_id=MEMBER,
        tenant_id=TENANT,
        subject=f"sub:{MEMBER}",
        role="viewer",
        membership_id="m-1",
        auth_method=AuthMethod.OIDC.value,
        permissions=permissions_for_role("viewer"),
    )
    async with file_db() as db:
        with caplog.at_level(logging.WARNING, logger="openjm.artifacts"):
            outcome = await AJ.maybe_create_artifact(
                db, principal, text="here\n\n```artifact\n{not valid json}\n```\n",
                conversation_id=None, message_id=None,
            )

    assert outcome.artifact is None
    assert outcome.failure == "malformed_directive"
    assert any("malformed_directive" in r.getMessage() for r in caplog.records)
