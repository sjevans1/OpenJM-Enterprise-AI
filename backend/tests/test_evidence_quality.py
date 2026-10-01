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
    assert normalize_exact_content("  alpha\r\nbeta  ") == "alpha beta"
    assert normalize_exact_content("alpha\t  beta") == "alpha beta"


def test_exact_fingerprint_matches_equivalent_line_endings():
    assert exact_content_fingerprint("alpha\r\nbeta") == exact_content_fingerprint(
        "alpha\nbeta"
    )


def test_exact_duplicates_from_same_chunk_collapse_to_highest_score():
    groups = deduplicate_exact_candidates(
        [
            candidate("doc-a", "A.md", "a1", "same\r\npassage", 0.81),
            candidate("doc-a", "A.md", "a1", "same passage", 0.92),
        ]
    )

    assert len(groups) == 1
    group = groups[0]
    assert group.representative.document_id == "doc-a"
    assert group.representative.score == 0.92
    assert group.duplicate_count == 2


def test_identical_text_from_distinct_documents_is_not_collapsed():
    groups = deduplicate_exact_candidates(
        [
            candidate("doc-a", "A.md", "a1", "same passage", 0.92),
            candidate("doc-b", "B.md", "b1", "same passage", 0.91),
        ]
    )

    assert len(groups) == 2
    assert [group.representative.document_id for group in groups] == [
        "doc-a",
        "doc-b",
    ]


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
        candidate("doc-a", "A.md", "d1", "Phoenix code is 7-3-9-2-5.", 0.99),
        candidate("doc-a", "A.md", "d1", "Phoenix code is 7-3-9-2-5.", 0.98),
        candidate("doc-a", "A.md", "d1", " Phoenix  code is 7-3-9-2-5. ", 0.97),
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


def test_identical_text_in_distinct_chunks_of_one_document_is_not_collapsed():
    groups = deduplicate_exact_candidates(
        [
            candidate("doc-a", "A.md", "a1", "repeated heading", 0.8),
            candidate("doc-a", "A.md", "a2", "repeated heading", 0.7),
        ]
    )
    assert len(groups) == 2
