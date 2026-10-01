from app.services.evidence_quality import (
    RetrievedCandidate,
    deduplicate_exact_candidates,
    exact_content_fingerprint,
    normalize_exact_content,
)


def candidate(
    document_id: str,
    title: str,
    chunk_id: str,
    content: str,
    score: float,
):
    return RetrievedCandidate(
        document_id=document_id,
        title=title,
        chunk_id=chunk_id,
        content=content,
        score=score,
        metadata={"document_id": document_id, "chunk_id": chunk_id},
    )


def test_exact_normalization_only_changes_line_endings_and_edges():
    assert normalize_exact_content("  alpha\\r\\nbeta  ") == "alpha\\nbeta"
    assert normalize_exact_content("alpha  beta") == "alpha  beta"


def test_exact_fingerprint_matches_equivalent_line_endings():
    assert exact_content_fingerprint("alpha\\r\\nbeta") == exact_content_fingerprint(
        "alpha\\nbeta"
    )


def test_exact_duplicates_collapse_to_highest_score_and_preserve_origins():
    groups = deduplicate_exact_candidates(
        [
            candidate("doc-a", "A.md", "a1", "same passage", 0.81),
            candidate("doc-b", "B.md", "b1", "same passage", 0.92),
            candidate("doc-c", "C.md", "c1", "same passage", 0.75),
        ]
    )

    assert len(groups) == 1
    group = groups[0]
    assert group.representative.document_id == "doc-b"
    assert group.representative.score == 0.92
    assert group.duplicate_count == 3
    assert {
        (item.document_id, item.title, item.chunk_id)
        for item in group.equivalent_sources
    } == {
        ("doc-a", "A.md", "a1"),
        ("doc-c", "C.md", "c1"),
    }


def test_near_duplicates_with_material_fact_difference_do_not_collapse():
    groups = deduplicate_exact_candidates(
        [
            candidate(
                "doc-a",
                "A.md",
                "a1",
                "Projected ROI is 18.5%.",
                0.90,
            ),
            candidate(
                "doc-b",
                "B.md",
                "b1",
                "Projected ROI is 19.5%.",
                0.89,
            ),
        ]
    )

    assert len(groups) == 2
    assert {g.representative.content for g in groups} == {
        "Projected ROI is 18.5%.",
        "Projected ROI is 19.5%.",
    }


def test_dedup_before_top_k_preserves_unique_relevant_passage():
    raw = [
        candidate("dup-1", "dup1.md", "d1", "Phoenix code is 7-3-9-2-5.", 0.99),
        candidate("dup-2", "dup2.md", "d2", "Phoenix code is 7-3-9-2-5.", 0.98),
        candidate("dup-3", "dup3.md", "d3", "Phoenix code is 7-3-9-2-5.", 0.97),
        candidate(
            "unique",
            "management.md",
            "u1",
            "Management attributed the variance to inventory constraints.",
            0.96,
        ),
    ]

    groups = deduplicate_exact_candidates(raw)
    top_two = groups[:2]

    assert len(top_two) == 2
    assert top_two[0].duplicate_count == 3
    assert top_two[1].representative.document_id == "unique"


def test_distinct_chunks_from_same_document_remain_distinct():
    groups = deduplicate_exact_candidates(
        [
            candidate("doc-a", "A.md", "a1", "first chunk", 0.8),
            candidate("doc-a", "A.md", "a2", "second chunk", 0.7),
        ]
    )
    assert len(groups) == 2
