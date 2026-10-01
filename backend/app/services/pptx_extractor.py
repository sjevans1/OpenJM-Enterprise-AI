"""OpenJM-owned PPTX extractor that preserves slide order and slide identity.

Phase D requirement: reliable ``slide_number`` metadata per chunk, verified
end-to-end through the Evidence response (see docs/RAG_METADATA_EVALUATION.md).

This subclasses the installed ``PPTXKnowledge`` and overrides only
``_load`` so that:
  * slide boundaries are preserved exactly (one Document per slide, unchanged
    from the upstream loader — extraction and chunking are not regressed);
  * each slide Document carries ``slide_number`` (1-based, derived from the
    actual enumeration over ``pr.slides`` — never fabricated);
  * ``page`` (PDF-style) is not invented here.

The upstream loader iterates ``pr.slides`` with ``for slide in ...`` and emits
one Document per slide with ``metadata = {"source": self._path}``. We add
``slide_number`` by enumerating the same iteration. No text extraction logic is
copied beyond what is needed to attach the metadata, so table/shape extraction
behavior is identical to upstream.
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
from dbgpt_ext.rag.knowledge.pptx import PPTXKnowledge


class OpenJMPPTXKnowledge(PPTXKnowledge):
    """PPTX knowledge that attaches a verified 1-based ``slide_number``."""

    def _load(self) -> List[Document]:
        """Load pptx preserving slide order and slide identity per slide.

        Mirrors the upstream loader exactly except that each slide Document
        receives ``slide_number`` (1-based, enumerated over ``pr.slides``).
        """

        if self._loader is not None:
            documents = self._loader.load()
            return [Document.langchain2doc(lc_document) for lc_document in documents]

        from pptx import Presentation

        pr = Presentation(self._path)
        docs: List[Document] = []
        for index, slide in enumerate(pr.slides, start=1):
            content = ""
            for shape in slide.shapes:
                if hasattr(shape, "text") and shape.text:
                    content += shape.text
            metadata: Dict[str, Any] = {"source": self._path}
            if self._metadata:
                metadata.update(self._metadata)  # type: ignore[arg-type]
            metadata["slide_number"] = index
            docs.append(Document(content=content, metadata=metadata))
        return docs

    # Re-export the registration so the policy seam validation and factory
    # mapping stay aligned with the installed base class.
    @classmethod
    def document_type(cls) -> Any:
        return DocumentType.PPTX

    @classmethod
    def type(cls) -> KnowledgeType:
        return KnowledgeType.DOCUMENT

    @classmethod
    def support_chunk_strategy(cls) -> List[ChunkStrategy]:
        return [
            ChunkStrategy.CHUNK_BY_SIZE,
            ChunkStrategy.CHUNK_BY_PAGE,
            ChunkStrategy.CHUNK_BY_SEPARATOR,
        ]

    @classmethod
    def default_chunk_strategy(cls) -> ChunkStrategy:
        return ChunkStrategy.CHUNK_BY_SIZE
