"""OpenJM HTML knowledge that also accepts the ``.htm`` extension.

Phase D compatibility fix: DB-GPT's ``KnowledgeFactory`` matches knowledge
classes by ``document_type().value == extension`` and ``HTMLKnowledge`` only
registers ``html``. As a result ``.htm`` uploads raised
"Unsupported knowledge document type 'htm'" even though the upload API
advertises ``.htm`` (see backend/app/api/knowledge.py ``SUPPORTED_EXTENSIONS``).

This subclass re-exports the identical loading behavior so ``.htm`` works
end-to-end. It is OpenJM-owned policy, resolved by extension in the Knowledge
engine (see ``app.services.knowledge._knowledge_for``), not by DB-GPT's
extension-driven factory.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from dbgpt.core import Document
from dbgpt.rag.knowledge.base import (
    ChunkStrategy,
    DocumentType,
    Knowledge,
    KnowledgeType,
)
from dbgpt_ext.rag.knowledge.html import HTMLKnowledge


class OpenJMHtmlKnowledge(HTMLKnowledge):
    """HTML knowledge; OpenJM alias used to ingest ``.htm`` files.

    The loader logic is the upstream HTMLKnowledge verbatim; only the type
    registration is surfaced so the policy seam and installed-strategy test
    remain aligned with the base class.
    """

    # Re-declare the registration surface so OpenJM's policy validation and
    # the installed-strategy test can reason about this class without reaching
    # into the DB-GPT factory (which only exposes "html").
    @classmethod
    def document_type(cls) -> Any:
        return DocumentType.HTML

    @classmethod
    def type(cls) -> KnowledgeType:
        return KnowledgeType.DOCUMENT

    @classmethod
    def support_chunk_strategy(cls) -> List[ChunkStrategy]:
        return [
            ChunkStrategy.CHUNK_BY_SIZE,
            ChunkStrategy.CHUNK_BY_SEPARATOR,
        ]

    @classmethod
    def default_chunk_strategy(cls) -> ChunkStrategy:
        return ChunkStrategy.CHUNK_BY_SIZE
