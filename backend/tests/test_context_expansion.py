from app.services.context_expansion import build_neighbor_requests
from app.services.evidence_quality import CandidateGroup, RetrievedCandidate


def group(
    document_id: str,
    chunk_id: str,
    *,
    previous: str | None = None,
    next_: str | None = None,
    score: float = 0.9,
):
    metadata = {}
    if previous:
        metadata["previous_chunk_id"] = previous
    if next_:
        metadata["next_chunk_id"] = next_
    return CandidateGroup(
        representative=RetrievedCandidate(
            document_id=document_id,
            title=f"{document_id}.md",
            chunk_id=chunk_id,
            content=f"content-{chunk_id}",
            score=score,
            metadata=metadata,
        )
    )


def test_neighbor_requests_are_bounded_and_deterministic():
    groups = [
        group("doc-a", "c2", previous="c1", next_="c3"),
        group("doc-b", "b2", previous="b1", next_="b3", score=0.8),
    ]

    result = build_neighbor_requests(
        groups,
        primary_limit=1,
        max_neighbor_chunks=2,
    )

    assert [(item.chunk_id, item.direction) for item in result] == [
        ("c1", "previous"),
        ("c3", "next"),
    ]
    assert all(item.document_id == "doc-a" for item in result)


def test_neighbor_requests_skip_chunks_already_selected_as_primaries():
    groups = [
        group("doc-a", "c2", next_="c3"),
        group("doc-a", "c3", previous="c2", score=0.8),
    ]

    result = build_neighbor_requests(
        groups,
        primary_limit=2,
        max_neighbor_chunks=4,
    )

    assert result == []


def test_neighbor_requests_deduplicate_same_neighbor():
    groups = [
        group("doc-a", "c2", next_="c3"),
        group("doc-a", "c4", previous="c3", score=0.8),
    ]

    result = build_neighbor_requests(
        groups,
        primary_limit=2,
        max_neighbor_chunks=4,
    )

    assert len(result) == 1
    assert result[0].chunk_id == "c3"


def test_neighbor_requests_respect_global_budget():
    groups = [group("doc-a", "c2", previous="c1", next_="c3")]

    result = build_neighbor_requests(
        groups,
        primary_limit=1,
        max_neighbor_chunks=1,
    )

    assert len(result) == 1
    assert result[0].chunk_id == "c1"


def test_neighbor_requests_disabled_by_zero_limits():
    groups = [group("doc-a", "c2", previous="c1", next_="c3")]

    assert build_neighbor_requests(
        groups, primary_limit=0, max_neighbor_chunks=2
    ) == []
    assert build_neighbor_requests(
        groups, primary_limit=1, max_neighbor_chunks=0
    ) == []
