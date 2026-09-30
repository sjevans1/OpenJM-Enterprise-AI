"""OpenJM structural metadata enrichment for RAG chunks (Phase D).

This module enriches DB-GPT ``Chunk`` objects with server-owned,
source-derived structural metadata AFTER the splitter has produced the
chunks but BEFORE those chunks are persisted to Chroma.

Design rules (see docs/RAG_METADATA_EVALUATION.md for full rationale):

* Enrichment runs post-split, pre-persist. Chunk content is never touched,
  so retrieval scores, chunk counts and the Phase C content/prefix behavior
  are unchanged.
* Server-owned keys always overwrite any loader-supplied same-named key, so
  uploaded content can never overwrite document identity, chunk ordering or
  access metadata (see SECURITY below).
* Only scalar values are written because Chroma's
  ``_transform_chroma_metadata`` keeps str/int/float/bool only.
* ``heading_path`` is converted to an ordered string ("H1 > H2 > H3") so it
  survives Chroma storage.
* ``source`` is scrubbed of internal filesystem paths before reaching
  customer-facing Evidence (handled in ``sanitize_for_evidence``).
"""

from __future__ import annotations

import uuid
from typing import Any, Iterable, List, Optional

from dbgpt.core import Chunk

# Server-owned structural metadata keys (never fabricated from content).
# These are the canonical Phase D field names surfaced in customer Evidence.
STRUCTURAL_KEYS: List[str] = [
    "document_id",
    "chunk_id",
    "chunk_index",
    "source_type",
    "source_name",
    "page_number",
    "slide_number",
    "heading_path",
    "content_type",
    "parent_id",
    "previous_chunk_id",
    "next_chunk_id",
    "ingestion_policy",
]

# Keys whose values are known to be server-owned (must survive sanitize).
_SERVER_OWNED_KEYS: frozenset = frozenset(
    {
        "document_id",
        "chunk_id",
        "chunk_index",
        "source_type",
        "source_name",
        "page_number",
        "slide_number",
        "heading_path",
        "content_type",
        "parent_id",
        "previous_chunk_id",
        "next_chunk_id",
        "ingestion_policy",
    }
)

# Loader-provided keys that carry an internal filesystem path.
_PATH_LIKE_KEYS: frozenset = frozenset({"source"})


def _to_scalar(value: Any) -> Any:
    """Coerce a value to a Chroma-safe scalar, or return None."""

    if value is None:
        return None
    if isinstance(value, (bool, int, float, str)):
        return value
    # Lists/dicts from loaders (e.g. MarkdownHeaderTextSplitter values) are
    # joined into a deterministic string so they remain retrievable.
    if isinstance(value, (list, tuple)):
        try:
            return "|".join(str(v) for v in value)
        except Exception:  # noqa: BLE001
            return None
    if isinstance(value, dict):
        return None
    return str(value)


def _ordered_heading_path(metadata: dict) -> Optional[str]:
    """Build an ordered heading path "H1 > H2 > ..." from Header1..Header6.

    The DB-GPT MarkdownHeaderTextSplitter emits only the HeaderN keys that are
    actually present at a chunk (older headers are not carried forward), so
    the path naturally reflects the section nesting for that chunk. Missing
    levels are skipped rather than fabricated.
    """

    ordered: List[str] = []
    for level in range(1, 7):
        key = f"Header{level}"
        if key in metadata:
            value = _to_scalar(metadata[key])
            if value:
                ordered.append(str(value))
    if not ordered:
        return None
    return " > ".join(ordered)


def _content_type_from(metadata: dict) -> str:
    """Derive a content_type label from loader metadata, never fabricating."""

    raw_type = metadata.get("type")
    if isinstance(raw_type, str) and raw_type:
        return raw_type
    if isinstance(raw_type, (list, tuple)):
        joined = "|".join(str(v) for v in raw_type)
        return joined or "unknown"
    return "text"


def enrich_chunks(
    chunks: List[Chunk],
    *,
    document_id: str,
    source_name: str,
    policy_name: str,
) -> List[Chunk]:
    """Enrich an ordered list of chunks with OpenJM structural metadata.

    Args:
        chunks: Ordered chunk list (split order == chunk_index order).
        document_id: Server-owned document identity (UUID from upload).
        source_name: Server-known original file name.
        policy_name: OpenJM ingestion policy name that produced the chunks.

    Returns:
        The same chunk list, mutated in place. Each chunk.metadata gains the
        Phase D structural keys; loader metadata is otherwise preserved.
    """

    if not chunks:
        return chunks

    chunk_ids: List[str] = []
    for index, chunk in enumerate(chunks):
        # chunk.chunk_id already has a server uuid from Chunk construction;
        # normalize to a fresh uuid so every indexed chunk has a recorded id
        # that OpenJM owns (deterministic per index run, not content hash).
        if not getattr(chunk, "chunk_id", ""):
            chunk.chunk_id = str(uuid.uuid4())
        chunk_ids.append(chunk.chunk_id)

    for index, chunk in enumerate(chunks):
        metadata: dict = chunk.metadata or {}
        previous_chunk_id = chunk_ids[index - 1] if index > 0 else None
        next_chunk_id = chunk_ids[index + 1] if index < len(chunk_ids) - 1 else None

        # Server-owned structural fields always overwrite.
        metadata["document_id"] = document_id
        metadata["chunk_id"] = chunk.chunk_id
        metadata["chunk_index"] = index
        metadata["source_type"] = "document"
        metadata["source_name"] = source_name
        metadata["content_type"] = _content_type_from(metadata)
        metadata["ingestion_policy"] = policy_name

        # heading_path (Markdown); skip if not present / not derivable.
        heading_path = _ordered_heading_path(metadata)
        if heading_path is not None:
            metadata["heading_path"] = heading_path

        # page_number (PDF) — loader emits 1-based int "page".
        page = metadata.get("page")
        if isinstance(page, bool):
            page = None  # guard: bool is subclass of int
        if isinstance(page, str) and page.strip().lstrip("-").isdigit():
            metadata["page_number"] = int(page)
        elif isinstance(page, int):
            metadata["page_number"] = page

        # slide_number (PPTX) — written by OpenJMPPTXKnowledge into Document
        # metadata before chunking; read here and normalize to int.
        slide = metadata.get("slide_number")
        if isinstance(slide, str) and slide.strip().lstrip("-").isdigit():
            metadata["slide_number"] = int(slide)
        elif isinstance(slide, int):
            metadata["slide_number"] = slide

        # Adjacency (chunk-level, document-scoped). These reference OpenJM
        # chunk ids, not raw content, so they cannot be forged from content.
        if previous_chunk_id is not None:
            metadata["previous_chunk_id"] = previous_chunk_id
        if next_chunk_id is not None:
            metadata["next_chunk_id"] = next_chunk_id

        # parent_id is intentionally NOT set: none of the installed loaders
        # emit a real parent document identity, so fabricating one would
        # violate the "no fabricated metadata" rule. The key is omitted.

        # Force all values through the scalar coercion so Chroma storage is
        # stable for any loader-provided list/dict values we keep verbatim.
        for key in list(metadata.keys()):
            metadata[key] = _to_scalar(metadata[key])

        chunk.metadata = metadata

    return chunks


def sanitize_for_evidence(metadata: dict, source_name: str) -> dict:
    """Return metadata safe for customer-facing Evidence.

    * Drops/replaces internal filesystem paths in ``source`` (loaders store
      ``self._path`` there, which is an on-server path). The value is
      replaced with the server-known source_name so citation context is
      preserved without leaking the host filesystem.
    * Leaves all server-owned structural keys untouched.
    * Leaves legacy metadata keys untouched (backward compatibility).
    """

    if not metadata:
        return {}
    cleaned = dict(metadata)
    source = cleaned.get("source")
    if isinstance(source, str) and _looks_like_path(source):
        cleaned["source"] = source_name
    # Defense in depth: any value that still embeds the server upload dir or
    # vector path must not leave the host.
    for key, value in list(cleaned.items()):
        if isinstance(value, str) and _looks_like_path(value):
            cleaned[key] = source_name
    return cleaned


def _looks_like_path(value: str) -> bool:
    """Heuristic: true if the string looks like a host filesystem path.

    Catches Unix absolute (/...), Windows absolute/UNC (C:\\..., \\...),
    and server-internal directories. Never matches a plain file name.
    """

    if not value:
        return False
    # Unix absolute or relative deep path
    if value.startswith("/"):
        return True
    # Windows drive letter e.g. C:\... or C:/...
    if len(value) >= 2 and value[1] == ":" and value[0].isalpha():
        return True
    # UNC or backslash path
    if value.startswith("\\"):
        return True
    return False
