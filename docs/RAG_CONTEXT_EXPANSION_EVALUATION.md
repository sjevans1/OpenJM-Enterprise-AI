# RAG Context Expansion Evaluation - Phase E

## Scope

Phase E hardens the governed `knowledge.search` path without changing DB-GPT,
Chroma, embeddings, the Evidence schema, or orchestration architecture.

Implemented stages:

1. E0: exact normalized duplicate control before the final semantic evidence cut.
2. E1: bounded immediate-neighbor expansion through exact Chroma chunk lookup.

No parent expansion, recursive windows, semantic neighbor search, reranking,
Hybrid, or agent behavior was added.

## E0: exact duplicate control

Implementation:

- `backend/app/services/evidence_quality.py`
- `backend/app/services/knowledge.py`

Normalization collapses whitespace runs, including CRLF/LF representation
differences, and trims passage edges. It does not case-fold, rewrite
punctuation, or use fuzzy similarity.

The dedupe identity is conservative:

```text
(document_id, chunk_id, normalized-content fingerprint)
```

This removes repeated representations of the same server-owned chunk while
keeping identical text from different documents, and identical text from
different chunks in one document, as separate evidence. The highest-scoring
copy is retained, its metadata and score survive, and `duplicate_count`
records how many representations were grouped.

Checkpoint commit:

```text
8e5304098453555557d7f41e0c532b2b406bfdc8
```

## E1: bounded immediate neighbors

Implementation:

- `backend/app/services/context_expansion.py`
- `backend/app/services/knowledge.py`
- `backend/app/services/tools.py`
- `backend/app/services/orchestrator.py`
- `backend/app/core/config.py`

The production orchestrator executes the governed `knowledge.search` tool.
That tool resolves ready, indexed documents for the current user and enables
the bounded policy.

Default policy:

```text
semantic primary limit: OPENJM_RAG_TOP_K = 5
expanded primary limit: OPENJM_RAG_NEIGHBOR_PRIMARY_LIMIT = 2
neighbor limit: OPENJM_RAG_NEIGHBOR_MAX_CHUNKS = 2
final evidence limit: 5 primaries + 2 neighbors = 7
passage limit: 2,000 characters per Evidence item
final character ceiling: 14,000 characters
```

For each eligible primary, requests are considered in deterministic order:
previous, then next. The policy never recurses and never requests a selected
primary as a neighbor.

Neighbor retrieval uses Chroma `Collection.get(ids=[...])`. It does not run a
second vector or semantic search. A neighbor is accepted only when all of the
following are true:

- the exact chunk ID exists in the primary document's collection;
- stored `document_id` exactly matches the authorized document;
- stored `chunk_id` exactly matches the requested ID;
- both primary and neighbor have integer `chunk_index` values;
- the indexes differ by exactly one in the requested direction;
- the neighbor's reciprocal pointer refers back to the primary.

Missing or inconsistent identity and sequence metadata is rejected. Final
neighbor identity dedupe is scoped by `(document_id, chunk_id)`. Evidence
ordering is previous, primary, next. Primary scores remain semantic scores;
neighbor scores remain `None` because no semantic neighbor search occurred.

The tool applies an authorized-source postcondition before returning Evidence.
Primary provenance reports `retrieval_mode=semantic`; neighbor provenance
reports `retrieval_mode=exact_chunk_id`.

Checkpoint commit:

```text
45977b2defaaa0491d8a637431b4c1c908cee6ab
```

## Deterministic adjacent-context proof

Fixtures:

- `adjacent_context_hard.md`: 10 indexed chunks, answer in the next chunk.
- `adjacent_context_previous.md`: 9 indexed chunks, answer in the previous chunk.

Command:

```bash
cd backend
.venv/bin/python tests/fixtures/rag/phase_e_benchmark.py
```

Measured result:

| Case | Semantic top-5 | Qualifier before expansion | Expanded evidence | Exact neighbor | Grounded result |
|---|---:|---:|---:|---|---|
| CEDAR next | 5 | absent | 7 | next, exact chunk ID | 47 minutes |
| MAPLE previous | 5 | absent | 7 | previous, exact chunk ID | 31 minutes |

For both cases:

- candidate neighbors: 2;
- accepted neighbors: 2;
- primary semantic hits: 5;
- removed by deduplication: 0;
- final evidence count: 7;
- final character count: 1,165 for CEDAR and 1,024 for MAPLE;
- evidence and character limits enforced;
- every neighbor was exactly one chunk from its originating primary;
- no non-adjacent chunk was expanded;
- acceptance result: PASS.

The harness asserts these conditions and exits nonzero on failure.

## Validation

Executed results:

```text
Focused Phase E and governance tests: 30/30 PASS
Full backend pytest suite: 136/136 PASS
RAG benchmark A-H: 8/8 PASS
Phoenix retrieval benchmark: PASS
Phase E real embedding/Chroma acceptance: PASS
Formal runtime Gates A-C with upload, catalog, grounded answer, deletion,
and negative retrieval: PASS
Structured foundation Gates F-G: PASS
Structured chat Gates H-I: PASS
Frontend TypeScript/Vite build for Gate D: PASS
```

Formal Phoenix runtime evidence:

```text
grounded answer: The PRIMARY launch sequence code for Project Phoenix is 7-3-9-2-5 [1].
grounded evidence count: 5
deleted document absent from catalog: PASS
deleted document no longer used as evidence: PASS
```

## Gate E - no simulated success

Gate E is cross-cutting rather than a separate mock-based unit gate. It was
executed against a fresh backend process and the real configured local model.

Command:

```bash
backend/.venv/bin/python scripts/acceptance.py \
  --base-url http://127.0.0.1:8001 \
  --document backend/tests/fixtures/rag/phoenix_launch_protocol.md \
  --question "What is the PRIMARY launch sequence code for Project Phoenix?" \
  --expect "7-3-9-2-5" \
  --delete-after-test
```

Measured result: 12/12 checks PASS.

The run performed, rather than simulated:

- multipart upload through the production API;
- DB-GPT extraction and chunking;
- embedding generation and Chroma persistence;
- catalog readback showing the indexed document;
- semantic retrieval through governed `knowledge.search`;
- evidence delivery to the configured Gemma model;
- generated answer `7-3-9-2-5 [1]` with five Evidence items;
- API deletion, catalog removal and vector cleanup;
- negative retrieval after deletion, with no deleted-document evidence.

The real structured execution path was also exercised:

```text
structured foundation: 7/7 PASS
structured chat acceptance: 10/10 PASS
```

Those runs registered an actual SQLite source, tested its connection,
discovered its schema, executed governed read-only SQL, returned SQL-backed
Evidence, verified disabled-source failure and deleted the acceptance source.
No production endpoint was credited from a stubbed success response.

## Remaining limits

- Expansion requires Phase D server-owned chunk identity and ordering metadata.
  Legacy rows without those fields fail closed and must be reindexed.
- The evidence budget is count and character bounded. No tokenizer-specific
  token counter was introduced.
- Parent expansion remains disabled.
- Cross-process vector-operation locking is not part of Phase E. Existing
  upload/delete lifecycle behavior remains covered by the repository's
  deletion tests and runtime negative-retrieval acceptance.
