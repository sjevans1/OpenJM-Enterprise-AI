"""Deterministic Phase E neighbour-expansion policy helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.services.evidence_quality import CandidateGroup


@dataclass(frozen=True)
class NeighborRequest:
    document_id: str
    title: str
    chunk_id: str
    expanded_from_chunk_id: str
    direction: str


def build_neighbor_requests(
    groups: Iterable[CandidateGroup],
    *,
    primary_limit: int,
    max_neighbor_chunks: int,
) -> list[NeighborRequest]:
    """Select bounded same-document ±1 neighbour requests.

    This function does not access storage. It only trusts Phase D adjacency
    metadata already attached to the selected semantic primary chunks.

    Requests are deterministic:
    - inspect primaries in semantic-rank order;
    - previous before next for each primary;
    - never request an id already selected as a primary in the same document;
    - deduplicate repeated neighbour ids;
    - stop at the global neighbour budget.
    """

    if primary_limit <= 0 or max_neighbor_chunks <= 0:
        return []

    selected = list(groups)
    primary_keys = {
        (group.representative.document_id, group.representative.chunk_id)
        for group in selected
        if group.representative.chunk_id
    }

    requests: list[NeighborRequest] = []
    seen: set[tuple[str, str]] = set()

    for group in selected[:primary_limit]:
        primary = group.representative
        metadata = primary.metadata or {}
        for direction, key in (
            ("previous", "previous_chunk_id"),
            ("next", "next_chunk_id"),
        ):
            raw = metadata.get(key)
            neighbor_id = str(raw or "").strip()
            if not neighbor_id:
                continue

            identity = (primary.document_id, neighbor_id)
            if identity in primary_keys or identity in seen:
                continue

            seen.add(identity)
            requests.append(
                NeighborRequest(
                    document_id=primary.document_id,
                    title=primary.title,
                    chunk_id=neighbor_id,
                    expanded_from_chunk_id=primary.chunk_id,
                    direction=direction,
                )
            )
            if len(requests) >= max_neighbor_chunks:
                return requests

    return requests
