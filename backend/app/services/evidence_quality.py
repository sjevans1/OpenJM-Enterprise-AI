"""OpenJM Phase E evidence-quality helpers.

This module owns deterministic, model-independent evidence normalization that
runs after authorized semantic retrieval and before final Evidence objects are
sent to synthesis.

Phase E rule: exact duplicate passages may be collapsed for prompt quality, but
source provenance must never be lost and materially different passages must
remain distinct.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass(frozen=True)
class RetrievedCandidate:
    """Internal representation of one authorized retrieved chunk.

    Full chunk content is retained here for exact duplicate fingerprinting.
    Customer-facing passage truncation happens later.
    """

    document_id: str
    title: str
    chunk_id: str
    content: str
    score: float | None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EquivalentSource:
    document_id: str
    title: str
    chunk_id: str


@dataclass(frozen=True)
class CandidateGroup:
    representative: RetrievedCandidate
    equivalent_sources: tuple[EquivalentSource, ...] = ()

    @property
    def duplicate_count(self) -> int:
        return 1 + len(self.equivalent_sources)


def normalize_exact_content(content: str) -> str:
    """Normalize representation-only differences for exact dedup.

    We deliberately do NOT collapse arbitrary internal whitespace or use fuzzy
    similarity. Two passages that differ materially must remain distinct.
    """

    return content.replace("\\r\\n", "\\n").replace("\\r", "\\n").strip()


def exact_content_fingerprint(content: str) -> str:
    normalized = normalize_exact_content(content)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _score(candidate: RetrievedCandidate) -> float:
    return candidate.score if candidate.score is not None else 0.0


def deduplicate_exact_candidates(
    candidates: Iterable[RetrievedCandidate],
) -> list[CandidateGroup]:
    """Collapse exact duplicate full-chunk content while preserving provenance.

    The highest-scoring candidate in each exact-content group becomes the
    representative. Every other origin remains available as an
    EquivalentSource reference.

    Returned groups are sorted by representative semantic score descending so
    callers may safely apply the final global top-k AFTER deduplication.
    """

    grouped: dict[str, list[RetrievedCandidate]] = {}
    order: list[str] = []

    for candidate in candidates:
        key = exact_content_fingerprint(candidate.content)
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(candidate)

    result: list[CandidateGroup] = []
    for key in order:
        members = sorted(grouped[key], key=_score, reverse=True)
        representative = members[0]

        seen_refs: set[tuple[str, str, str]] = set()
        equivalents: list[EquivalentSource] = []
        for member in members[1:]:
            ref_key = (member.document_id, member.title, member.chunk_id)
            if ref_key in seen_refs:
                continue
            seen_refs.add(ref_key)
            equivalents.append(
                EquivalentSource(
                    document_id=member.document_id,
                    title=member.title,
                    chunk_id=member.chunk_id,
                )
            )

        result.append(
            CandidateGroup(
                representative=representative,
                equivalent_sources=tuple(equivalents),
            )
        )

    result.sort(key=lambda item: _score(item.representative), reverse=True)
    return result
