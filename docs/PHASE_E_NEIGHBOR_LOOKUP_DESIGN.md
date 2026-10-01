# Phase E Direct-Neighbour Lookup Design

Planning-only document. No production code is changed on this branch.

Reviewed application head: `b0d5e7b192a6292643cd9fe4e20de9153e38bc86`.

Verified external dependency source:
DB-GPT / dbgpt-ext `v0.8.2`.

## Decision

For Phase E, use **exact chunk-id lookup inside the existing per-document
Chroma collection**.

Do not use a second semantic query to approximate adjacency.
Do not infer neighbours from retrieval score, `peek()` order, UUID order or
global collection iteration.

Phase D already stores trustworthy `previous_chunk_id` and
`next_chunk_id` from the splitter output order. Phase E should dereference
those exact ids.

## Why this is the cleanest seam

Installed DB-GPT 0.8.2's `ChromaStore` exposes semantic search and delete
operations, but no public read-by-id method.

The underlying Chroma collection already supports exact retrieval by id, and
DB-GPT itself uses `self._collection.get()` in its `truncate()`
implementation.

Therefore the smallest compatibility shim is an OpenJM-owned exact-id reader
inside the Knowledge adapter.

This follows the same philosophy as OpenJM's existing Chroma collection
deletion fallback: keep version-specific storage behavior contained behind
`DBGPTKnowledgeEngine`, not in the orchestrator, tool registry or UI.

## Proposed internal API

Conceptual only:

```python
async def get_chunks_by_ids(
    self,
    document_id: str,
    chunk_ids: list[str],
) -> list[Chunk]:
    ...
```

Required behavior:

1. return an empty list for an empty input;
2. deduplicate requested ids while preserving request order;
3. open the **document-scoped** collection only;
4. call exact Chroma `Collection.get(ids=[...])`;
5. rebuild DB-GPT/OpenJM Chunk objects from returned id/content/metadata;
6. validate stored `metadata.document_id` against the authorized
   `document_id` where that metadata is present;
7. reject/skip an id not present in that collection;
8. preserve requested id order because Chroma's return order must not become
   an application invariant;
9. never expose the Chroma collection handle outside the Knowledge layer.

Legacy chunks lacking Phase D `document_id` metadata remain readable because
the collection itself is already document-scoped. New Phase D chunks receive
the additional metadata validation.

## Collection access

Two implementation options are acceptable.

### Option A — contained access through the DB-GPT store instance

```text
store = self._store(document_id)
store._collection.get(ids=[...])
```

Advantage:
- exact resolved collection from DB-GPT.

Cost:
- depends on DB-GPT's private attribute.

### Option B — OpenJM PersistentClient helper

Use the same Chroma public client approach already used by OpenJM's collection
deletion verification, then open the known document collection and call
`get(ids=[...])`.

Advantage:
- no dependency on DB-GPT's private `_collection` attribute.

Cost:
- collection naming must stay exactly aligned with DB-GPT, including any
  name normalization/hashing rules.

### Recommendation

For the currently pinned DB-GPT 0.8.2 integration, **Option A is the safer
correctness choice** because DB-GPT has already resolved the actual collection
name.

Contain the private access in one tiny helper with explicit tests and a comment
pinning the compatibility assumption. If/when DB-GPT adds a public exact-id
reader, replace the helper without affecting any OpenJM caller.

Do not replicate DB-GPT's collection-name hashing logic in a second code path.

## Proposed bounded expansion algorithm

Inputs:

- authorized primary Evidence/Chunks from existing semantic retrieval;
- document id;
- Phase D adjacency metadata.

Algorithm:

```text
for each primary chunk in score order:
    keep primary

    candidate ids =
        previous_chunk_id if present
        next_chunk_id if present

    exact-fetch candidates from SAME document collection

    validate same-document identity

    mark candidate as neighbour context

deduplicate all by (document_id, chunk_id)

budget:
    primary chunks win first
    neighbours admitted only while global budget remains

present:
    source-order group around each primary
    while preserving provenance role
```

No recursive chaining in the first release.

A neighbour's own `previous_chunk_id` / `next_chunk_id` must not trigger
another expansion step.

## Suggested policy object

Keep expansion decisions OpenJM-owned and deterministic.

Conceptual:

```text
KnowledgeContextPolicy
- enabled
- neighbor_radius = 1
- max_neighbor_chunks
- max_total_evidence
- max_context_chars/tokens
- eligible_content_types / policies (optional, benchmark-driven)
```

No model controls these values.

Start with:

- ±1 maximum;
- same document only;
- primary-first budget;
- global dedup.

Do not make expansion format-specific until benchmarks prove a need.

## Provenance

Primary evidence:

```json
{
  "retrieval_role": "primary",
  "semantic_score": 0.82
}
```

Neighbour evidence:

```json
{
  "retrieval_role": "neighbor",
  "expanded_from_chunk_id": "...",
  "direction": "previous"
}
```

or `"next"`.

Do not copy the primary semantic score onto neighbours.
Neighbour evidence is context, not independently semantically ranked evidence.

If a chunk is both a semantic primary and another primary's neighbour, keep it
as **primary** and deduplicate the neighbour copy.

If the same neighbour is reached from two primaries, keep one Evidence object
and optionally record multiple `expanded_from_chunk_id` values internally if
the public schema can do so without complexity. This is not required for the
first implementation.

## Ordering

Use stored `chunk_index` only for ordering chunks *within the same document*.

Do not compare chunk indices across documents.

For each source group, natural display/context order should be by
`chunk_index`; primary/neighbor role stays in provenance.

The final evidence budget may still rank source groups by the best primary
semantic score.

## Authorization tests

Phase E must include at least:

1. primary chunk from document A points to a malicious/forged id that exists
   only in document B's collection → document B chunk is never fetched;
2. requested neighbour id missing from document A → skipped safely;
3. Phase D metadata says wrong `document_id` → reject/skip;
4. legacy neighbour without document_id but physically in A's collection →
   allowed only when reached through A's server-authorized collection;
5. caller/model cannot submit arbitrary neighbour ids through any API/tool
   payload;
6. disabled/deleted/unready document cannot be expanded because it cannot
   become an authorized primary source.

## Hard adjacency benchmark fixture

The existing adjacent-context fixture is too easy because baseline semantic
retrieval already returns enough context.

Create a fixture designed to separate retrieval from context expansion:

```text
Chunk N:
  distinctive query anchor:
  "Project CEDAR escalation threshold"

Chunk N+1:
  deliberately low-overlap qualifier:
  "The controlling value is 47 minutes."

Chunk N+2:
  plausible but unrelated distractor:
  "Regional maintenance window is 90 minutes."
```

Query:

`What is the controlling value for the Project CEDAR escalation threshold?`

Acceptance design:

- baseline primary retrieval should reliably retrieve N but *not* N+1 within
  the configured top-k/threshold for the fixture;
- ±1 expansion should add N+1;
- N+2 should not be added;
- normalized Evidence must contain both the anchor and `47 minutes`;
- provenance must label N as primary and N+1 as next-neighbour.

The fixture must be generated so this behavior is deterministic before using
it as a promotion gate.

Add a boundary variant where the required qualifier is N-1.

## Duplicate evidence budget

Current retrieval can surface semantically duplicate chunks from separate
documents.

Do not merge separate source identities blindly.

Benchmark two layers independently:

### Within-document dedup
Always safe:
- `(document_id, chunk_id)` exact dedup.

### Cross-document exact-content dedup
Experimental only:
- normalize whitespace;
- exact content hash;
- preserve a list of source references rather than discarding provenance.

Promote cross-document dedup only if it measurably reduces context bloat and
does not hide meaningful source disagreement or version differences.

This can remain deferred if the model-gateway work resolves the immediate
Phoenix prompt issue another way.

## Evidence budget

Before production implementation, benchmark candidate budgets.

Track:

- number of primary chunks;
- number of candidate neighbours;
- number admitted;
- total evidence characters;
- approximate tokens;
- unrelated-context ratio;
- answer-context completeness.

Primary evidence must never be displaced by a neighbour solely because the
neighbour was fetched later.

A safe first policy is to reserve the current primary retrieval set, then spend
a separate small neighbour budget.

## Passage cap interaction

Exact neighbour lookup does not fix the `passage[:2000]` cap.

Keep passage-cap evaluation orthogonal:

- if a retrieved chunk itself is <= 2,000 chars, no special action;
- if a deterministic supported-format fixture produces a larger chunk and a
  needed tail fact is lost, implement the smallest bounded representation fix;
- do not increase context size merely because neighbours exist.

## Deletion behavior

Because every document uses a separate collection, deleting the document
collection should make both semantic retrieval and exact neighbour lookup
return nothing.

Add a true storage-level Phase E test:

1. ingest multi-chunk fixture;
2. exact-fetch a known neighbour by id;
3. delete document collection using production deletion path;
4. semantic retrieval returns no chunks;
5. exact neighbour lookup returns nothing / collection-not-found safely.

This complements the current metadata-shaped deletion test.

## Performance

Exact Chroma id reads do not require embedding computation.

Benchmark expansion latency separately from semantic retrieval.

The expected overhead should be small, but do not assume it; record:

- semantic retrieval ms;
- exact neighbour fetch ms;
- evidence assembly ms.

Batch all neighbour ids for one document in one `get(ids=[...])` call rather
than one call per chunk.

## Implementation boundary

Production Phase E should change only the Knowledge capability / tool path.

It must not require:

- changes to the chat router;
- changes to Structured SQL execution;
- changes to the frontend;
- a model to choose tools/neighbours;
- a new vector store;
- a new embedding model.

The eventual Hybrid orchestrator should remain unaware of this mechanism.

## Promotion gate

Promote bounded neighbour expansion only if the hard adjacency fixtures prove:

- context completeness improves;
- unrelated context remains bounded;
- no authorization leak;
- no duplicate explosion;
- latency remains reasonable;
- all Phase A–D facts remain green;
- formal runtime Gate C is also green before PR #3 merge.
