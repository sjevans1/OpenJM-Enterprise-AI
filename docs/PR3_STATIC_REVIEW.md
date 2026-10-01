# PR #3 Static Review — Knowledge/RAG Hardening

Planning-only review. This file lives on `planning/phase-e-readiness` so it
cannot interfere with Hermes' active work on
`hardening/knowledge-rag-fidelity`.

Reviewed hardening head: `b0d5e7b192a6292643cd9fe4e20de9153e38bc86`.

Scope reviewed:

- all production changes in PR #3;
- RAG acceptance/benchmark scripts and new deterministic tests;
- Phase A–D design/evaluation documents;
- the unchanged chat/model-gateway paths that currently block formal Gate C;
- installed DB-GPT/dbgpt-ext 0.8.2 source where needed to validate integration
  assumptions.

## Summary

The architecture direction is sound:

- DB-GPT remains replaceable infrastructure behind OpenJM's Knowledge seam;
- DOCX extraction fidelity was fixed without replacing the RAG stack;
- document-aware chunk policy is OpenJM-owned and evidence-driven;
- structural metadata is added post-split/pre-persist;
- authorization remains server-side;
- customer-facing evidence is sanitized;
- Phase D now supplies the stable adjacency metadata required for a bounded
  Phase E.

The branch should remain draft because formal runtime Gate C is still red and
there are several concrete closure/test-harness issues.

## Findings

### BLOCKER — runtime model output can still fail formal Gate C

The current live failure is not a retrieval failure: Phoenix evidence is
retrieved correctly, but the model endpoint has returned malformed special
tokens instead of a usable answer.

Hermes is actively isolating this issue. Do not solve it by changing chunking,
retrieval thresholds or evidence facts.

Merge condition:

- malformed output must never be accepted as successful customer content;
- retry/recovery, if used, must be bounded;
- the formal Phoenix acceptance must actually return `7-3-9-2-5`.

### BLOCKER/HIGH — failed generation mutates conversation history

`backend/app/api/chat.py` persists the incoming user message and commits it
**before** the model request. If the gateway raises after that commit, the
conversation keeps an unanswered user turn.

Consequences:

- a subsequent retry is not a replay of the failed request;
- history may contain consecutive user turns;
- the prompt shape changes after every failed attempt;
- model-runtime diagnosis becomes harder;
- the user's persisted conversation can become semantically inconsistent.

This is independent of RAG quality and should be treated as a transaction /
message-state integrity defect during the model-gateway checkpoint.

Preferred invariant:

```text
request → plan → generation → success → persist user+assistant atomically
                         └→ failure → controlled failure, no normal orphan turn
```

If failed user requests must be retained for audit, they need an explicit
failure/pending state rather than silently appearing as ordinary history.

### HIGH — formal Knowledge acceptance is not isolated and leaks test artifacts on failure

`scripts/acceptance.py` uploads a document into the live Knowledge catalog but
only deletes it in the successful path after reaching the deletion section.
When Gate C generation fails earlier, the uploaded acceptance document remains.

This has already produced multiple near-duplicate Phoenix documents. Since
Knowledge retrieval searches every authorized indexed document, later
acceptance runs change the model prompt and no longer test the same condition.

Do **not** auto-delete arbitrary pre-existing documents.

Safer choices, in order:

1. run formal acceptance against an isolated test DB/vector store;
2. or fail fast when the Knowledge catalog is not in the expected isolated
   state;
3. always clean up the document created by the script in a `finally` block
   unless an explicit diagnostic keep flag is set.

The acceptance test should prove its own uploaded document supplied the
expected evidence and should leave no artifacts after ordinary failures.

### HIGH — Phase D source_name currently derives from the stored filename

The upload endpoint writes the file as
`{document_id}_{original_name}`, then passes the stored path to
`knowledge_engine.ingest`.

At reviewed head, `knowledge.py` calls:

`enrich_chunks(..., source_name=file_path.name, ...)`

so customer-visible `source_name` / sanitized `source` may expose the
UUID-prefixed storage filename rather than `Document.original_name`.

The authoritative original filename should be passed explicitly from the
upload record into ingestion. Do not reconstruct it by splitting the storage
filename.

This item is already assigned to Hermes in the Phase D closure checkpoint.

### MEDIUM — Knowledge retrieval has no cross-document duplicate-evidence control

`DBGPTKnowledgeEngine.retrieve` retrieves up to `rag_top_k` chunks from each
authorized document, concatenates the results, globally sorts by score and
then keeps the top `rag_top_k`.

That correctly preserves authorization, but when several indexed documents
contain the same or nearly identical content, the final evidence set can be
dominated by repetitions of the same fact.

This is not a reason to globally deduplicate documents blindly: two separate
documents may legitimately repeat language and represent distinct sources.

For Phase E / prompt-budget work, benchmark a conservative duplicate-evidence
rule such as exact normalized content hash **after retrieval**, preserving
source provenance separately. Promote only if it reduces prompt noise without
hiding meaningful source disagreement.

### MEDIUM — metadata isolation test name overstates what it proves

`test_evidence_is_scoped_per_document_id` verifies that enriched chunks for a
single fixture carry one document id. It does not execute retrieval across two
document collections or two users.

The authorization architecture is still sound because
`KnowledgeSearchTool` resolves ready documents by `user_id` server-side, but
the Phase D test does not independently prove the cross-document/cross-user
claim its name/comments imply.

Add one deterministic integration test that:

- creates two document records with different owners;
- mocks or isolates two document collections;
- executes `knowledge.search` as owner A;
- proves owner B's document id is never passed into the retrieval set and
  never appears in returned Evidence.

### MEDIUM — deletion-shaped metadata test is not a storage deletion test

`test_deletion_removes_all_chunks_from_storage` only checks that enriched
chunk indices are contiguous and share one document id. It does not persist a
collection, delete it, then verify retrieval is empty.

Actual deletion behavior is covered elsewhere by existing
`test_knowledge_delete.py` plus runtime acceptance, so this is a test naming /
coverage clarity issue rather than a production defect.

Rename or complement it with a true isolated Chroma ingest/delete/retrieve test
if Phase E adds direct chunk-id access.

### LOW — content_type uses a text fallback

`chunk_metadata._content_type_from` returns `"text"` when a loader provides
no explicit type. This is reasonable for the normalized textual RAG chunk, but
it is an OpenJM classification, not loader-observed structure.

Documentation should describe it as the normalized chunk representation rather
than implying every loader explicitly detected `text`.

### LOW — path sanitizer is intentionally heuristic

The current sanitizer handles the actual supported loader behavior because
upload paths are absolute. It also replaces other absolute-path-looking string
metadata values with the source name.

This is acceptable defense-in-depth for the current product, but a future raw
metadata/admin surface should use an allow-list of public metadata fields
rather than depending only on path-pattern replacement.

## External integration check: DB-GPT 0.8.2 ChromaStore

Review of the installed-version source confirms:

- semantic search is exposed through `similar_search*`;
- deletion by ids exists;
- the wrapper has no public read-by-chunk-id method;
- internally it uses a Chroma collection object;
- `truncate()` itself calls `self._collection.get()`.

Therefore Phase E direct neighbour retrieval should use a narrow OpenJM adapter
around Chroma's exact-id `Collection.get(ids=[...])`, contained inside the
Knowledge layer, instead of performing a second semantic search or relying on
`peek()` ordering.

See `docs/PHASE_E_NEIGHBOR_LOOKUP_DESIGN.md`.

## Files / areas with no new blocker found

The static review did not identify a new architectural blocker in:

- `OpenJMDocxKnowledge` table-preserving extraction;
- `OpenJMPPTXKnowledge` slide enumeration (it mirrors upstream extraction and
  adds a server-derived 1-based slide number);
- the evidence-based Phase C Markdown promotion;
- the decision to retain size+overlap for DOCX/PDF/PPTX/TXT/HTML;
- post-split/pre-persist chunk metadata enrichment;
- the `.htm` OpenJM compatibility seam;
- the structured acceptance isolation changes.

## Recommended closure order

1. Let Hermes finish model-runtime diagnosis.
2. Close source-name + stale fixture cleanup.
3. Fix failed-turn history integrity.
4. Isolate and self-clean the formal Knowledge acceptance harness.
5. Add the stronger authorization/deletion integration tests.
6. Re-run Phase D closure suite.
7. Only then start Phase E production implementation.
8. Keep PR #3 draft until formal Gate C and all final regression gates are
   genuinely green.
