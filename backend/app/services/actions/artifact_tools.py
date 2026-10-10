"""BV5-C: the governed ``artifact.create`` bounded action.

A Chat artifact creation is exposed as a tool in the VS6 registry so it inherits
the whole governed-action surface — registry bound, permission bound, tenant
scope, idempotency (a replayed step reuses the recorded outcome) and the audit
trail — rather than being a private side effect of some other layer.

Two properties keep this honest:

* **It is user-scoped, reversible and sandboxed.** The tool writes only an inert,
  download-only work product into the caller's own artifact store under an
  opaque, server-generated key; it performs no enterprise mutation, cannot reach
  another tenant's data and can be retired (deleted) like any other artifact.
  It therefore declares ``requires_approval=False`` — a second human approval
  for the caller's own downloadable copy would be ceremony without a control —
  while remaining a declared write activity rather than an undeclared one.
* **There is no path, shell or network argument.** The tool's only inputs are a
  title (advisory, normalised server-side into a filename), a format and inert
  content. It can never be handed a filesystem path or a URL to fetch.
"""

from __future__ import annotations

from app.core.context import current_principal
from app.core.identity import Permission
from app.services.actions.registry import (
    OperationClass,
    ParameterSpec,
    RiskLevel,
    ToolRegistry,
    ToolSpec,
)
from app.services.artifact_journey import failure_category, persist_chat_artifact
from app.services.artifacts import ArtifactError


async def _artifact_create(db, arguments: dict) -> dict:
    """Create a downloadable Chat artifact for the current principal.

    Fails closed with a bounded result: a refused format/name/size (or a
    cross-tenant linkage) yields ``{"ok": False, ...}`` and never a phantom
    artifact.
    """
    principal = current_principal()
    try:
        artifact = await persist_chat_artifact(
            db,
            principal,
            title=arguments["title"],
            fmt=arguments["format"],
            content=arguments["content"],
            conversation_id=arguments.get("conversation_id"),
            message_id=arguments.get("message_id"),
            evidence=(),
        )
    except ArtifactError as exc:
        return {
            "ok": False,
            "failure_category": failure_category(exc),
            "detail": "Artifact creation was refused by the artifact boundary.",
        }
    return {
        "ok": True,
        "artifact_id": artifact.id,
        "title": artifact.title,
        "filename": artifact.filename,
        "mime_type": artifact.mime_type,
        "artifact_format": artifact.artifact_format,
        "size_bytes": artifact.size_bytes,
        "state": artifact.state,
        "is_evidence_backed": artifact.is_evidence_backed,
    }


def register_artifact_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="artifact.create",
            description=(
                "Create a downloadable Chat artifact (HTML/Markdown/TXT/CSV) "
                "owned by the caller. User-scoped, reversible and confined to "
                "the controlled artifact store; creates no enterprise mutation."
            ),
            operation_class=OperationClass.WRITE,
            risk_level=RiskLevel.LOW,
            required_permissions=frozenset({Permission.CHAT_USE.value}),
            parameters=(
                ParameterSpec("title", str, max_length=200),
                ParameterSpec("format", str, max_length=16),
                ParameterSpec("content", str, max_length=4_000_000),
                ParameterSpec("conversation_id", str, required=False, max_length=36),
                ParameterSpec("message_id", str, required=False, max_length=36),
            ),
            tenant_scoped=True,
            requires_approval=False,
            timeout_seconds=20,
            reversible=True,
        ),
        _artifact_create,
    )
