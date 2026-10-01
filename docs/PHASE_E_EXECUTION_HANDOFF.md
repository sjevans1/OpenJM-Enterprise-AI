# Phase E Execution Handoff — Evidence Quality and Bounded Context Expansion

Planning document only. Do not implement Phase E on this planning branch.

## Preconditions

Active hardening branch at the time of this handoff:

`hardening/knowledge-rag-fidelity`
HEAD `428209e918be840ccd02f772ea1a39ad5fb82a40`

Model-gateway checkpoint is complete:
- malformed special-token output is detected;
- one bounded deterministic retry is allowed;
- second malformed result fails closed;
- failed generation leaves no orphan user turn;
- formal Phoenix Gate C passed;
- 104/104 backend tests passed in the reported checkpoint.

There is a separate draft PR into the hardening branch:

**PR #4 — Harden Knowledge acceptance isolation and cleanup**

Head branch:
`fix/knowledge-acceptance-isolation`

Do not start Phase E production changes until PR #4 has been validated and
integrated into the hardening branch, or its equivalent changes are otherwise
present. PR #4 must not be merged into main directly.

PR #4 closes the test-harness contamination path that previously left duplicate
Phoenix documents behind after failed Gate C runs.

## Phase E objective

Improve the evidence sent to answer synthesis in two bounded ways:

1. **duplicate-evidence control** — prevent exact repeated passages from
   crowding out unique evidence or destabilizing the model prompt while
   preserving every source identity/provenance;
2. **bounded neighbour expansion** — when semantic retrieval finds a primary
   chunk but misses an immediately adjacent qualifier/context chunk, fetch
   trustworthy Phase D neighbours by exact chunk id from the same document.

Phase E remains entirely behind the Knowledge capability.

Do not implement Hybrid.
Do not add autonomous retrieval loops.
Do not change embeddings or vector database.
Do not introduce synthetic parent identities.
Do not change Phase B/C extraction/chunking policies merely to make Phase E
benchmarks pass.

## Why duplicate-evidence control is now part of Phase E

The completed model-gateway investigation established:

- single-document Phoenix evidence: valid model generation 9/9;
- four duplicate/overlapping Phoenix documents: malformed generation 6/6
  across the tested prompt shapes;
- history depth, prompt shape and sampling were not the dominant trigger.

The gateway now fails safely, but the Knowledge layer should still avoid
feeding unnecessary repeated evidence into synthesis.

This is an evidence-quality problem, not a reason to add more retries.

## Existing retrieval behavior to preserve

At the current hardening head:

`DBGPTKnowledgeEngine.retrieve(query, documents)`

does:

1. semantic top-k retrieval independently for every authorized document;
2. concatenate all candidate chunks;
3. sort candidates globally by score;
4. return the global `rag_top_k`.

This is authorization-safe, but duplicates from separate documents can occupy
multiple top-k slots and repeat the same passage in the Knowledge system prompt.

The current orchestrator still calls `knowledge_engine.retrieve` directly on
the production Knowledge route. `knowledge.search` uses the same engine.
Therefore Phase E retrieval quality should be implemented in the Knowledge
engine/tool seam so both paths receive the same behavior.

Do not duplicate evidence-quality logic inside the orchestrator.

# Work Package E0 — Exact duplicate-evidence control

## E0.1 Add deterministic candidate grouping before final top-k

After all authorized per-document semantic candidates are retrieved, but
**before global top-k truncation**, group exact repeated passage content.

Use a normalized-content fingerprint derived from the **full chunk content**,
not the customer-facing 2,000-character passage truncation.

Recommended normalization for the first implementation:

- Unicode/string content as emitted by the loader/chunker;
- normalize line endings;
- trim leading/trailing whitespace;
- collapse repeated internal whitespace only if benchmark evidence proves this
  does not merge materially different passages.

Start with conservative exact normalization. Do not use embedding similarity,
fuzzy matching, token-set overlap or LLM classification for deduplication.

## E0.2 Preserve source provenance

Never silently discard the fact that multiple authorized documents supported
the same passage.

For one exact-duplicate group:

- choose the highest-scoring candidate as the representative Evidence;
- retain its normal `source_id`, `title`, score and structural metadata;
- attach the other exact-equivalent source references in provenance or metadata,
  e.g. `equivalent_sources=[{source_id, title}, ...]`;
- do not expose internal storage paths;
- do not invent a blended semantic score.

The API may return one representative evidence item for an exact duplicate
group, provided every collapsed source identity remains inspectable in the
normalized Evidence provenance.

If preserving multiple source references cleanly requires a small optional
Evidence field, keep it backward-compatible.

## E0.3 Top-k fairness

Deduplicate before the final global `rag_top_k` cut.

Required benchmark:

- create at least three identical authorized document copies plus one unique
  relevant document;
- prove duplicates do not consume multiple final top-k slots;
- prove the unique relevant document remains eligible;
- prove all duplicate source identities remain preserved in provenance.

## E0.4 Do not over-deduplicate

Add a near-duplicate fixture where two passages differ by one material fact
(e.g. 18.5% vs 19.5%).

They MUST remain separate evidence.

Two separately versioned sources that happen to share most language must not be
collapsed unless their normalized full passage is exact under the approved
normalization.

# Work Package E1 — Exact neighbour lookup

## E1.1 Verify installed Chroma API locally

Before production code, inspect the installed DB-GPT 0.8.2 and Chroma versions.

DB-GPT 0.8.2's `ChromaStore` has no public read-by-id method. It does expose
the underlying collection internally and DB-GPT itself calls
`self._collection.get()` in `truncate()`.

Preferred Phase E seam:

```text
DBGPTKnowledgeEngine
    ↓
private OpenJM exact-id helper
    ↓
same DB-GPT-resolved document collection
    ↓
Chroma Collection.get(ids=[...])
```

Do not:
- perform a second semantic query to approximate neighbours;
- use `peek()` order;
- sort UUIDs;
- query a global collection;
- reproduce DB-GPT's collection-name hashing in a second code path if avoidable.

A small, tested compatibility helper over the resolved store's collection is
acceptable while DB-GPT remains pinned to 0.8.2.

## E1.2 Exact-id helper requirements

Conceptual method:

```python
async def get_chunks_by_ids(
    self,
    document_id: str,
    chunk_ids: list[str],
) -> list[Chunk]:
    ...
```

Requirements:

- empty input -> empty output;
- deduplicate requested ids while preserving requested order;
- fetch only from the document-scoped collection;
- reconstruct chunk content/id/metadata correctly;
- restore requested order explicitly; never depend on Chroma return order;
- for Phase D chunks, stored metadata.document_id must equal authorized
  document_id;
- missing ids are skipped safely;
- legacy chunks lacking document_id remain compatible because the collection
  itself is document-scoped;
- no collection handle leaks outside the Knowledge layer.

## E1.3 Bounded expansion policy

Do not start by expanding every one of five semantic primaries.

Benchmark these deterministic candidates:

- baseline: no expansion;
- candidate A: ±1 neighbours for top 1 semantic primary;
- candidate B: ±1 neighbours for top 2 semantic primaries;
- candidate C: ±1 with a hard global neighbour budget.

Promote the **smallest** policy that solves the hard context-gap fixtures.

No recursive chaining:
a neighbour's own adjacency metadata must not trigger more expansion.

No ±2 unless ±1 is proven insufficient.

## E1.4 Evidence roles

Primary semantic Evidence:

`provenance.retrieval_role = "primary"`

Neighbour Evidence:

- `retrieval_role = "neighbor"`
- `expanded_from_chunk_id`
- `direction = "previous" | "next"`

Do not copy the primary semantic score onto a neighbour.

If a chunk is both a semantic primary and another primary's neighbour, retain
one item and keep it as `primary`.

# Hard Phase E fixture

The existing `adjacent_context.md` already passes baseline semantic retrieval
and therefore cannot prove expansion value.

Create a deterministic fixture with **more chunks than production rag_top_k**.

Recommended Markdown shape:

1. several high-similarity distractor sections;
2. anchor section N containing a distinctive query phrase:
   `Project CEDAR escalation threshold`;
3. immediately following section N+1 containing the required qualifier with
   intentionally weak semantic overlap:
   `The controlling value is 47 minutes.`;
4. section N+2 contains a plausible distractor:
   `Regional maintenance window is 90 minutes.`;
5. enough additional query-similar sections exist that baseline production
   top-k retrieves N but excludes N+1.

Query:

`What is the controlling value for the Project CEDAR escalation threshold?`

Required comparison:

### Baseline

- semantic primary evidence includes the anchor;
- `47 minutes` is absent from the final production top-k evidence.

### Candidate

- exact neighbour expansion includes N+1;
- normalized Evidence now contains `47 minutes`;
- N+2 is not introduced solely through recursive expansion;
- provenance identifies N as primary and N+1 as next-neighbour.

Also create a previous-neighbour boundary variant.

Do not lower retrieval threshold or top-k merely to manufacture the baseline
failure.

# E2 — Combined evidence budget

Run dedup **before** final primary top-k and before neighbour expansion.

Then:

1. retain unique semantic primaries;
2. choose expansion candidates from the selected highest-ranked primaries;
3. exact-fetch neighbours;
4. deduplicate neighbour ids against primaries and each other;
5. enforce a global evidence/context budget;
6. preserve primary evidence first.

Measure:

- raw semantic candidate count;
- duplicate groups collapsed;
- unique primary count;
- candidate neighbour count;
- admitted neighbour count;
- final evidence count;
- context characters;
- approximate context tokens;
- unrelated-context count;
- semantic retrieval latency;
- exact neighbour lookup latency;
- assembly/dedup latency.

Do not rely on the model to manage the budget.

# Security tests

Add deterministic tests proving:

1. a neighbour id that exists only in document B can never be fetched through
   document A's collection;
2. metadata.document_id mismatch is rejected/skipped;
3. missing neighbour id fails safely;
4. legacy chunk without document_id works only through its authorized
   document-scoped collection;
5. caller/model payload cannot nominate arbitrary neighbour ids;
6. deleted/unready/unauthorized documents cannot become primaries or neighbours;
7. exact duplicate collapsing cannot combine evidence from an unauthorized
   document because unauthorized documents never enter the candidate set.

Strengthen the previously weak isolation test with an actual two-owner
`knowledge.search` integration test.

# Storage deletion test

Add a true storage-level test:

1. ingest a multi-chunk fixture;
2. retrieve a primary;
3. exact-fetch its neighbour;
4. delete the document through production deletion;
5. semantic retrieval returns nothing;
6. exact-id lookup returns nothing / handles missing collection safely.

# Passage-cap edge

Keep this orthogonal.

Only change the current 2,000-character Evidence cap if a deterministic
supported-format fixture proves a real customer-visible tail loss.

Do not increase the cap merely because Phase E adds neighbours.

# Regression requirements

After production integration, all must remain green:

- backend pytest;
- RAG fixtures A-H;
- Phoenix retrieval 7-3-9-2-5;
- formal Phoenix Gate C;
- DOCX 1,425;
- PDF 18.5%;
- PDF PHOENIX-8822;
- PPTX 3.2 + slide_number;
- Markdown heading_path;
- .htm ECHO-4477;
- original-name/path/storage-name redaction;
- Structured foundation;
- Structured chat acceptance;
- General conversation memory;
- model-gateway malformed-output tests;
- Knowledge acceptance isolation/cleanup from PR #4.

# Documentation

Create:

`docs/RAG_CONTEXT_EXPANSION_EVALUATION.md`

Include:

- exact duplicate grouping algorithm;
- provenance preservation behavior;
- duplicate benchmark before/after;
- direct-id Chroma seam and installed-version assumptions;
- hard adjacency benchmark;
- candidate A/B/C comparison;
- selected expansion policy or decision to retain no expansion;
- evidence/prompt budget comparison;
- latency comparison;
- authorization/deletion tests;
- model-runtime stability observation under duplicate fixtures;
- remaining limitations.

Update:

`docs/KNOWLEDGE_RAG_HARDENING.md`

Only mark Phase E complete if the promotion rules are actually satisfied.

# Promotion rules

Duplicate evidence control may be promoted if:

- exact duplicates no longer consume repeated top-k slots;
- all source identities remain preserved;
- materially different passages are not collapsed;
- deterministic regressions remain green;
- prompt/context size decreases on duplicate fixtures.

Neighbour expansion may be promoted if:

- the hard adjacency fixture proves a real baseline context gap;
- the smallest bounded candidate fixes it;
- unrelated context stays bounded;
- authorization and deletion tests pass;
- latency is reasonable;
- no existing RAG fact regresses.

It is valid for Phase E to promote duplicate control but reject neighbour
expansion if the neighbour benchmark shows no measurable benefit.

# Git workflow

Do not push directly to main.

Recommended after PR #4 is integrated:

- create `hardening/phase-e-context-expansion` from the latest
  `hardening/knowledge-rag-fidelity`;
- commit incrementally;
- open a draft PR targeting `hardening/knowledge-rag-fidelity`;
- do not merge it without explicit authorization.

Suggested commits:

1. `test: add Phase E duplicate and adjacency benchmarks`
2. `feat: add exact evidence deduplication`
3. `feat: add bounded same-document neighbor lookup`
4. `test: add Phase E authorization and deletion integration`
5. `docs: record Phase E evidence-quality evaluation`

# Final report

Return:

- base hardening SHA;
- Phase E final SHA;
- installed Chroma version/API finding;
- files changed;
- duplicate-evidence algorithm;
- duplicate benchmark result;
- source-provenance preservation result;
- near-duplicate non-collapse result;
- exact neighbour lookup seam;
- candidate A/B/C benchmark;
- selected neighbour policy;
- hard adjacency baseline/candidate result;
- cross-document/cross-user isolation result;
- storage deletion result;
- evidence counts/tokens before/after;
- latency before/after;
- pytest result;
- RAG A-H + Phoenix retrieval;
- formal Gate C result;
- General memory result;
- Structured 325 result;
- structured foundation/chat acceptance;
- model-gateway regressions;
- RAG_CONTEXT_EXPANSION_EVALUATION.md commit;
- git status;
- any unresolved limitation.

STOP after Phase E.

Do not implement Hybrid.
Do not merge into main.
