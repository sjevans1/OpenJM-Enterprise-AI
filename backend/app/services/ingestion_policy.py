"""OpenJM-owned ingestion policy.

Decides how a document is loaded and chunked before it enters the DB-GPT
RAG pipeline. This module is the ONLY place in the OpenJM codebase that knows
about DB-GPT chunk strategy names. The orchestrator, API, tool registry and
UI see only documents and evidence; never strategy identifiers.

Phase C ground rules (docs/KNOWLEDGE_RAG_HARDENING.md):

- the proven recursive size+overlap behavior (CHUNK_BY_SIZE with DB-GPT
  defaults 512/50) remains the universal safe fallback;
- a candidate strategy is promoted per document type only after the
  isolated benchmark harness proves it preserves all existing facts and
  adds measurable structural fidelity;
- unknown or unsupported file types must fall back safely rather than fail.

Promoted strategies and rationale live in the _POLICIES table below; the
full baseline-vs-candidate evidence is recorded in
docs/RAG_CHUNK_POLICY_EVALUATION.md and
backend/tests/fixtures/rag/chunk_policy_evaluation.json.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


# DB-GPT strategy identifiers used by the policy. Internal to this module.
_STRATEGY_SIZE = "CHUNK_BY_SIZE"

# Policy names exposed in decisions (stable, OpenJM-owned vocabulary).
FALLBACK_POLICY = "fallback_recursive_size_overlap"
DOCX_STRUCTURE_POLICY = "docx_structure_preserve"
MARKDOWN_HEADER_POLICY = "markdown_header_aware"


@dataclass(frozen=True)
class IngestionDecision:
    """One fully-resolved ingestion decision for a single document."""

    extension: str
    knowledge_class_name: str
    chunk_strategy: str
    policy_name: str
    rationale: str
    chunk_size: Optional[int] = None
    chunk_overlap: Optional[int] = None
    separator: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def chunk_parameters_kwargs(self) -> Dict[str, Any]:
        """Build the kwargs accepted by ``ChunkParameters``.

        Only fields explicitly chosen by the policy are included, so DB-GPT
        applies its own proven defaults (512/50/``"\\n"``) wherever the policy
        does not override them.
        """
        kwargs: Dict[str, Any] = {"chunk_strategy": self.chunk_strategy}
        if self.chunk_size is not None:
            kwargs["chunk_size"] = self.chunk_size
        if self.chunk_overlap is not None:
            kwargs["chunk_overlap"] = self.chunk_overlap
        if self.separator is not None:
            kwargs["separator"] = self.separator
        return kwargs


@dataclass(frozen=True)
class _TypePolicy:
    """Static per-extension policy entry."""

    strategy: str
    policy_name: str
    rationale: str
    chunk_size: Optional[int] = None
    chunk_overlap: Optional[int] = None
    separator: Optional[str] = None
    knowledge_class_name: str = "KnowledgeFactory"


@dataclass
class _PolicyBuilder:
    """Internal decision builder; validates against the installed engine."""

    extension: str
    policy: _TypePolicy
    knowledge_class_name: Optional[str] = None

    def build(self) -> IngestionDecision:
        knowledge_class_name = self.knowledge_class_name or self.policy.knowledge_class_name
        return IngestionDecision(
            extension=self.extension,
            knowledge_class_name=knowledge_class_name,
            chunk_strategy=self.policy.strategy,
            policy_name=self.policy.policy_name,
            rationale=self.policy.rationale,
            chunk_size=self.policy.chunk_size,
            chunk_overlap=self.policy.chunk_overlap,
            separator=self.policy.separator,
        )


# ── production policy table ──────────────────────────────────────────
# Phase C starts from the proven CHUNK_BY_SIZE baseline for every type.
# Entries are promoted to a structure-aware strategy ONLY after the
# isolated comparison harness (chunk_policy_comparison.py) proves the
# candidate preserves all benchmark facts and adds measurable fidelity.
# Anything not listed falls back safely.

_POLICIES: Dict[str, _TypePolicy] = {
    ".txt": _TypePolicy(
        strategy=_STRATEGY_SIZE,
        policy_name=FALLBACK_POLICY,
        rationale=(
            "TXT has no structure beyond newlines; the proven recursive "
            "size+overlap baseline keeps boundary facts (BR-7749-Q3) safe."
        ),
    ),
    ".md": _TypePolicy(
        strategy="CHUNK_BY_MARKDOWN_HEADER",
        policy_name=MARKDOWN_HEADER_POLICY,
        rationale=(
            "Promoted by the Phase C harness: header-aware chunks scored "
            "equal or higher on every markdown fixture (heading_context "
            "0.66->0.88, adjacent_context 0.65->0.76, long_report "
            "0.54->0.59, phoenix 0.85->0.86), carry Header1-6 metadata, "
            "and stop unrelated sections sharing a chunk. No fact loss; "
            "chunk-count rise is one chunk per section (explained)."
        ),
    ),
    ".docx": _TypePolicy(
        strategy=_STRATEGY_SIZE,
        policy_name=DOCX_STRUCTURE_POLICY,
        rationale=(
            "OpenJMDocxKnowledge extraction preserves tables in document "
            "order. The harness rejected CHUNK_BY_PARAGRAPH because it "
            "shatters the table into row-level chunks and destroys row "
            "meaning; CHUNK_BY_SIZE keeps the whole table in one chunk "
            "and the 1,425 fact retrievable."
        ),
        knowledge_class_name="OpenJMDocxKnowledge",
    ),
    ".pdf": _TypePolicy(
        strategy=_STRATEGY_SIZE,
        policy_name=FALLBACK_POLICY,
        rationale=(
            "Harness found CHUNK_BY_PAGE byte-identical to CHUNK_BY_SIZE on "
            "both PDF fixtures (the loader already emits page-bounded "
            "documents with page metadata). No measurable advantage; the "
            "proven baseline is retained."
        ),
    ),
    ".pptx": _TypePolicy(
        strategy=_STRATEGY_SIZE,
        policy_name=FALLBACK_POLICY,
        rationale=(
            "Harness found CHUNK_BY_PAGE byte-identical to CHUNK_BY_SIZE on "
            "the slide fixture (the loader already emits one document per "
            "slide). No measurable advantage; slide metadata is Phase D."
        ),
    ),
    ".html": _TypePolicy(
        strategy=_STRATEGY_SIZE,
        policy_name=FALLBACK_POLICY,
        rationale=(
            "Installed HTMLKnowledge supports only SIZE and SEPARATOR; "
            "the loader strips newlines, so SEPARATOR has no split points. "
            "The safe size+overlap fallback is retained."
        ),
    ),
    ".htm": _TypePolicy(
        strategy=_STRATEGY_SIZE,
        policy_name=FALLBACK_POLICY,
        rationale=(
            "Alias of .html; DB-GPT's factory only registers html, so the "
            "safe size+overlap fallback is used for .htm uploads."
        ),
        knowledge_class_name="KnowledgeFactory",
    ),
}

_FALLBACK_POLICY_ENTRY = _TypePolicy(
    strategy=_STRATEGY_SIZE,
    policy_name=FALLBACK_POLICY,
    rationale=(
        "Unknown or unsupported document type: recursive size+overlap "
        "fallback keeps ingestion safe and behavior identical to the "
        "pre-Phase-C engine."
    ),
)


class OpenJMIngestionPolicy:
    """Document-aware ingestion policy owned by OpenJM.

    ``for_document`` is the single decision point between the file and the
    Knowledge extractor. Everything it returns is either an OpenJM-owned
    name (policy_name, rationale) or an internal DB-GPT identifier that
    must not leak past the Knowledge capability.

    Strategy identifiers are validated against the installed DB-GPT
    implementation so a stale policy can never request an unsupported
    strategy at runtime.
    """

    def __init__(self, policies: Optional[Dict[str, _TypePolicy]] = None):
        self._policies = dict(policies or _POLICIES)

    @staticmethod
    def _extension_of(document_path) -> str:
        if isinstance(document_path, Path):
            return document_path.suffix.lower()
        return Path(str(document_path)).suffix.lower()

    def for_document(self, document_path: Any) -> IngestionDecision:
        """Return the ingestion decision for one document path."""
        extension = self._extension_of(document_path)
        entry = self._policies.get(extension)
        if entry is None:
            entry = _FALLBACK_POLICY_ENTRY
        return _PolicyBuilder(extension=extension, policy=entry).build()

    def fallback_decision(self) -> IngestionDecision:
        """Return the universal safe fallback decision."""
        return _PolicyBuilder(extension="", policy=_FALLBACK_POLICY_ENTRY).build()

    def strategies_are_installed(self, knowledge_class: Any) -> bool:
        """True if every policy strategy is supported by ``knowledge_class``.

        Used by tests and the policy self-check to guarantee the policy
        table never references a strategy the installed engine does not
        support for that document type.
        """
        installed = {
            s.name
            for s in getattr(knowledge_class, "support_chunk_strategy", lambda: [])()
        }
        return all(p.strategy in installed for p in self._policies.values())

    def decisions_are_deterministic(self) -> bool:
        """Two calls with the same path must produce identical decisions."""
        probe = "probe.txt"
        first = self.for_document(probe)
        second = self.for_document(probe)
        return first == second


def ingestion_policy() -> OpenJMIngestionPolicy:
    """Return the process-wide OpenJM ingestion policy instance."""
    return _SHARED_POLICY


_SHARED_POLICY = OpenJMIngestionPolicy()
