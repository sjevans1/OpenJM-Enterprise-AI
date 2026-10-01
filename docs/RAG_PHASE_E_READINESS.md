# Phase E Readiness — Bounded Context Expansion Design

Status: planning only. This branch remains isolated from production changes.

Latest hardening state reviewed: `428209e918be840ccd02f772ea1a39ad5fb82a40`.
The model-gateway reliability checkpoint is complete and formal Gate C passed.

## Purpose

Prepare the Phase E implementation without touching production behavior on this planning branch. Formal Gate C is now green; Phase E should address the remaining evidence-quality issue proven during the gateway investigation: semantically duplicate evidence can destabilize the local model.

Phase E should improve contextual completeness behind `knowledge.search`
using metadata created in Phase D, while remaining deterministic, bounded,
permission-safe and backward-compatible.

## Current primitives available from Phase D

Each newly indexed chunk can carry:

- `document_id`
- `chunk_id`
- `chunk_index`
- `previous_chunk_id`
- `next_chunk_id`
- `heading_path` where real
- `page_number` / `slide_number` where real
- `content_type`
- `ingestion_policy`

These fields make bounded neighbour expansion possible without reconstructing
order from retrieval rank or Chroma iteration order.

`parent_id` is intentionally absent because no trustworthy parent identity is
currently emitted. Therefore Phase E should start with neighbour expansion;
parent/section expansion should remain optional and evidence-driven.

## Proposed Phase E architecture

```text
semantic query
   ↓
primary retrieval (existing EmbeddingRetriever)
   ↓
authorized primary chunks
   ↓
bounded neighbour resolver
   ├─ same document only
   ├─ metadata-identified previous/next only
   ├─ no caller-supplied document/chunk authority
   └─ hard evidence/token budget
   ↓
deduplicate / preserve order
   ↓
normalize Evidence
   ├─ primary evidence
   └─ context evidence
   ↓
existing knowledge answer synthesis
```

No autonomous retrieval loop. No model-selected neighbour count. No
cross-document expansion.

## Security / authorization invariant

A neighbour must be eligible only when all of the following are true:

1. the primary chunk came from a document already authorized server-side;
2. the neighbour is fetched from the same document-scoped vector collection;
3. stored `document_id` equals the authorized document id when that metadata
   exists;
4. its chunk id equals an index-derived adjacency id from a primary/eligible
   chunk;
5. it is included only within the server-owned expansion budget.

Uploaded content, model output and caller input must not be able to nominate
arbitrary neighbour ids or document ids.

Legacy chunks without Phase D adjacency metadata must continue to retrieve
normally; they simply receive no neighbour expansion.

## Retrieval roles

Use the existing Evidence schema and optional provenance/metadata rather than a
new public response type.

Suggested internal/public fields:

- `provenance.retrieval_role = "primary" | "neighbor"`
- `provenance.expanded_from_chunk_id` for neighbour evidence
- existing `metadata.chunk_id`, `chunk_index`, source/page/slide/heading
  fields remain authoritative

Primary scores remain the semantic retrieval scores. Do not invent a semantic
score for neighbour chunks. A neighbour may have `score=None` or an explicitly
separate context score only if the implementation has a real source for it.

## Bounded expansion policy

Start with the smallest useful deterministic policy:

- retain the current semantic `rag_top_k` primary retrieval;
- consider at most one previous and one next chunk per primary;
- deduplicate by `(document_id, chunk_id)`;
- never expand across document boundaries;
- apply a global post-expansion evidence-count and character/token budget;
- prefer primary evidence over neighbour evidence when the budget is full;
- preserve natural source order for contextual chunks around each primary.

The actual budget should be benchmark-derived, not arbitrary. Initial harness
candidates can compare:

- no expansion
- ±1 neighbour
- ±1 only for multi-chunk documents / selected structure types

Do not implement ±2, recursive chaining or model-controlled expansion unless
the ±1 benchmark proves insufficient.

## Important implementation question: neighbour lookup seam

The current retrieval API is optimized for semantic search, not direct
chunk-id lookup. Before coding Phase E, inspect the installed DB-GPT 0.8.2
Chroma adapter for a supported direct-id or metadata-filter lookup.

Preferred order:

1. use a supported DB-GPT vector-store lookup if available;
2. otherwise add a narrow OpenJM-owned retrieval adapter over Chroma's public
   client API, scoped to the existing document collection;
3. do not perform a second semantic query to approximate adjacency;
4. do not reconstruct neighbours from `peek()` order.

Any direct Chroma use should stay inside the Knowledge adapter just like the
existing collection-deletion fallback.

## Duplicate-evidence finding from the gateway investigation

The model-gateway checkpoint established a strong Phase E requirement:
four duplicate/overlapping Phoenix documents produced malformed model output
6/6 across tested prompt shapes, while single-document evidence was valid 9/9.

Gateway validation now contains this safely, but Phase E should reduce prompt
redundancy at the evidence layer rather than relying on retries.

Evaluate **post-retrieval exact/near-exact duplicate evidence control**
separately from neighbour expansion:

- always deduplicate exact repeats within the same document;
- benchmark exact normalized-content dedup across documents while preserving
  every source reference/provenance;
- do not collapse sources that merely disagree or represent distinct versions;
- measure prompt-size reduction and answer stability;
- keep raw authorized source identities available even when repeated passages
  are collapsed for synthesis.

Do not make cross-document semantic dedup an unconditional production rule
without deterministic evidence.

## Required deterministic fixtures

The existing `adjacent_context.md` currently passes without expansion, so it
is not sufficient to prove Phase E value.

Add a hard adjacency fixture with at least:

- chunk N contains the distinctive query anchor;
- chunk N+1 contains a qualifier/value required to answer correctly;
- the neighbour chunk is intentionally less semantically similar so primary
  retrieval alone does not reliably include it;
- answer requires both pieces;
- unrelated chunk N+2 exists to verify bounded expansion.

Add a second fixture where the primary is the last chunk and only the previous
neighbour is valid.

For Markdown, preserve header-aware chunking. For DOCX/PDF/PPTX, do not alter
existing Phase B/C extraction/chunk policies merely to create an expansion win.

## Acceptance comparisons

For each Phase E fixture compare:

### Baseline
semantic primary retrieval only

### Candidate
semantic primary retrieval + bounded neighbour expansion

Measure:

- whether required answer context is present in normalized Evidence;
- evidence count;
- duplicated evidence count;
- unrelated-context count;
- prompt-context character/token size;
- retrieval + expansion latency;
- citation/provenance correctness;
- authorization/document isolation;
- deletion behavior.

Promotion requires a measurable context-completeness benefit without materially
increasing unrelated context.

## Existing 2,000-character passage cap

Phase D showed current 512/50-bounded Markdown chunks do not hit the cap, but
large whole-document HTML chunks remain a possible edge.

Treat this separately from neighbour expansion.

A bounded fix may be evaluated in Phase E only if a deterministic fixture proves
loss in customer-visible Evidence. Candidate remedies:

- align passage cap with the largest permitted production chunk bound; or
- bounded head+tail representation that preserves provenance and clearly
  indicates truncation.

Do not increase the cap blindly and do not compensate by lowering retrieval
thresholds.

## Parent / section expansion

Do not introduce synthetic `parent_id`.

Markdown heading paths may later support a real section-context concept, but
only after defining a deterministic section identity from actual chunk
structure and benchmarking it.

Phase E can be accepted with neighbour expansion alone if it solves the proven
context-gap fixture. Parent expansion is optional, not a checkbox.

## Interaction with Hybrid

Phase E must remain completely behind `knowledge.search`.

The eventual Hybrid path should continue to see only normalized Knowledge
Evidence plus Structured Evidence. It should not know whether Knowledge used
semantic retrieval alone or semantic+neighbour expansion.

## Regression gates before merge

Phase E implementation must preserve:

- DOCX table-only `1,425`
- PDF `18.5%`
- PDF `PHOENIX-8822`
- PPTX `3.2` with correct slide metadata
- Phoenix retrieval `7-3-9-2-5`
- Markdown heading-path behavior
- `.htm` compatibility
- no filesystem/storage-name disclosure
- deletion/no stale evidence
- authorization isolation
- structured-data Gates F–I
- formal runtime Gate C, which is now green and must remain so

PR #3 should not be considered merge-ready until runtime Gate C is genuinely
green.

## Suggested Phase E implementation sequence

1. Build direct neighbour lookup prototype against isolated fixture collections.
2. Add synthetic/unit tests for adjacency, boundaries, duplicates and legacy
   chunks.
3. Add hard adjacency benchmark fixture.
4. Compare no-expansion vs ±1 expansion.
5. Promote only if benchmark demonstrates value.
6. Integrate inside `knowledge.search` / Knowledge engine behind an
   OpenJM-owned deterministic expansion policy.
7. Add provenance roles and budget accounting.
8. Run full deterministic and runtime regression suite.
9. Document results in `docs/RAG_CONTEXT_EXPANSION_EVALUATION.md`.

## Explicit non-goals

- no autonomous retrieval loop
- no query rewriting
- no reranker unless separately benchmarked later
- no GraphRAG
- no cross-document neighbours
- no model-controlled expansion
- no database writes/actions
- no Hybrid implementation
- no Phase E code on this planning branch while Hermes owns the active
  hardening work

This document is a readiness plan only and can be cherry-picked later if useful.
