# RAG Metadata Evaluation — Phase D

This document records the Phase D structural metadata work: what chunk
metadata actually looked like before Phase D, what it looks like after, how
each field is derived, which requested fields are unsupported and why, the
security changes, the deterministic tests, and the remaining limitations.

All "before" and "after" states below are measured, not assumed. Evidence
artifacts:

- Before-state probe: real loaders + policy splitters over the eight RAG
  fixtures (ChunkManager only; no embedding, no Chroma). Every loader
  metadata key per fixture type was dumped.
- After-state probe: full ingest → Chroma → retrieve → Evidence over the
  same fixtures plus the new long-section fixture, in an isolated vector
  store, through the production `DBGPTKnowledgeEngine`.

## Where chunk metadata enters the pipeline (installed-code facts)

Verified against installed `dbgpt` / `dbgpt-ext` 0.8.2:

1. Loaders emit one `Document` per structural unit with a small metadata
   dict (`source` = on-server file path; PDF adds `page`, `type`, `title`;
   Markdown header splitter adds `Header1..Header6`).
2. `ChunkManager.split` copies loader metadata verbatim onto every chunk
   (`Chunk.langchain2chunk`) and generates `chunk_id` (uuid4) per chunk.
3. `EmbeddingAssembler.load_from_knowledge` runs load + split; the chunks
   are available pre-persist via `assembler.get_chunks()`.
4. `ChromaStore.load_document_with_limit` persists chunk id + content +
   metadata; `_transform_chroma_metadata` keeps only str/int/float/bool
   values (lists/dicts are dropped).
5. `EmbeddingRetriever.aretrieve_with_scores` returns Chroma rows as
   `Chunk` objects; `DBGPTKnowledgeEngine.retrieve` maps them to OpenJM
   `Evidence` with the passage truncated at 2,000 characters.

The Phase D enrichment seam is step 3→4: `enrich_chunks()` runs after the
splitter has produced chunks (content and scores untouched) and before
persist, so enriched metadata lands in Chroma and flows through retrieval
into Evidence without any retrieval-time fabrication.

## Metadata before Phase D (measured)

| Fixture type | Loader chunk metadata keys |
|---|---|
| Markdown (.md) | Header1, Header2, source (full server path), title |
| TXT | source (full server path) |
| DOCX | source (full server path) |
| PDF | page (1-based int), type (text/excel), title, source (full server path) |
| PPTX | source (full server path) — no slide identity |
| HTML | source (full server path) |

Gaps: no document identity, no chunk ordering, no heading path, no slide
numbers, and the `source` value leaked the server filesystem path into
customer-facing Evidence.

## Metadata after Phase D (measured)

Every chunk now carries, at minimum:

| Field | Derivation | Source of truth |
|---|---|---|
| `document_id` | server upload identity | OpenJM `Document.id` (uuid) |
| `chunk_id` | per-chunk uuid recorded at index time | DB-GPT Chunk id |
| `chunk_index` | 0-based position in split order | splitter output order |
| `source_type` | constant `document` | server |
| `source_name` | server-known original file name | upload record |
| `content_type` | loader `type` where present, else `text` | loader metadata |
| `ingestion_policy` | policy name that produced the chunk | OpenJM policy seam |
| `previous_chunk_id` / `next_chunk_id` | adjacent chunk ids in split order | enrichment |

Type-specific, only where the structure is real (never fabricated):

| Field | Type | Derivation |
|---|---|---|
| `heading_path` | Markdown | ordered `Header1 > Header2 > ...` built from Header1..Header6; missing levels skipped |
| `page_number` | PDF | int-normalized 1-based loader `page` |
| `slide_number` | PPTX | 1-based slide identity from `OpenJMPPTXKnowledge` (enumerates `pr.slides`) |

PDF `type` (the "excel"/table indicator) is preserved as `content_type`
and the raw `type` key, so detected table chunks remain distinguishable.
No table-relationship metadata is invented.

The legacy loader keys (`Header1..6`, `page`, `type`, `title`) are kept
verbatim for backward compatibility: any consumer that read them before
Phase D still works, and previously indexed documents that lack the new
keys remain retrievable — Evidence construction reads metadata with
`.get()`, so absent keys never raise.

## Requested fields not implemented (and why)

| Field | Status | Reason |
|---|---|---|
| `parent_id` | not set | None of the installed loaders emit a real parent-document identity for a chunk. Fabricating one (e.g. "the document" or "the section") would be synthetic structure, which Phase D forbids. Revisit in Phase E only if parent-context expansion needs it, derived from real heading aggregation. |
| `slide_number` for non-PPTX | not set | slides only exist in PPTX |
| `page_number` for non-PDF | not set | no installed loader emits page identity for DOCX/TXT/HTML/Markdown |
| `heading_path` for non-Markdown | not set | installed loaders do not detect headings for HTML/TXT/PDF/DOCX; HTML heading awareness is a candidate for a future phase with benchmark evidence |

## DOCX (Phase B extractor preserved)

The OpenJMDocxKnowledge extractor is untouched. It emits one logical
document (paragraphs + tables in document order), so DOCX chunks receive
the universal fields only. Table content stays inside chunk content (the
1,425 value remains retrievable in one piece — verified again after Phase
D). No table fragmentation, no artificial per-row metadata.

## PPTX slide numbers (verified customer-visible)

`OpenJMPPTXKnowledge` mirrors the upstream loader exactly, plus
`slide_number` from the same slide enumeration. Verified end to end:
upload → index → chat question → the returned Evidence for the CAC fact
carries `slide_number: 3` and `chunk_index: 2` with no path disclosure.

## `.htm` compatibility fix (root cause and fix)

Root cause: DB-GPT `KnowledgeFactory` selects a knowledge class by
`document_type().value == extension`; installed `HTMLKnowledge` registers
`html` only. The upload API advertises `.htm`, so `.htm` ingestion raised
`Unsupported knowledge document type 'htm'`.

Fix: OpenJM's own knowledge resolution (`_knowledge_for` in
`app/services/knowledge.py`) maps `.htm` and `.html` to
`OpenJMHtmlKnowledge` (a subclass of the installed HTMLKnowledge) before
DB-GPT's factory is consulted. `.htm` now ingests and retrieves end to end
(verified live: upload → ready → Evidence with ECHO-4477 fact → correct
answer). The policy decision for `.htm` remains the safe size+overlap
fallback.

## Long-section quality check (>2,000 chars, fact at the end)

Fixture: `tests/fixtures/rag/long_section.md` — a single top-level section
(~2,600 chars) with the unique fact `BOUNDARY-FACT-PHASE-D-9931` near the
end.

Measured result: the section was split into 7 chunks of 325–511 chars;
the fact survived into the stored chunk and into the returned Evidence
passage. **No information loss occurred in this test.**

Why: the Markdown header strategy's internal oversized-section fallback
re-splits with the recursive splitter, and — a Phase C documentation
correction — it inherits the policy's 512/50 size/overlap rather than
the native 4000/200 defaults (the installed
`MarkdownHeaderTextSplitter.aggregate_lines_to_chunks` constructs its
fallback `RecursiveCharacterTextSplitter` with `self._chunk_size`, which
DB-GPT's `ChunkStrategy.match` had already set from `ChunkParameters`).
So single Markdown sections do not produce >2,000-char chunks under the
current policy, and the 2,000-char Evidence passage cap did not truncate
any needed context in this test.

Correction applied to the record (this file supersedes the Phase C note):
Phase C's claim that oversized sections fall back to "recursive 4000/200"
describes the splitter's constructor defaults, not the production path;
the production path bounds them at the policy's 512/50.

Residual risk (documented, not fixed — bounded correction proposed for
Phase E): a single unbroken paragraph longer than the Evidence passage
cap inside one chunk would still be truncated at passage formation.
With the current policy, chunk size (512+50) is well below the 2,000-char
cap, so the only remaining loss mode is dense non-Markdown documents whose
whole-document single chunk exceeds 2,000 chars (e.g. a large HTML page).
Bounded correction proposed for Phase E: enforce a deterministic
"passage ≤ chunk" invariant at Evidence formation (head + tail capture
instead of head-only truncation), or raise the cap to the policy chunk
bound; do not change retrieval thresholds to compensate.

## Security changes

- **No internal path disclosure.** Loaders store the on-server file path
  in `source` (PDF also stores a filename in `title`). Before Phase D this
  path reached customer-facing Evidence verbatim (measured: every type).
  `sanitize_for_evidence()` now replaces path-like values with the
  server-known source name before Evidence construction; verification
  across all fixture types shows zero path leaks in Evidence, while the
  raw value remains inside Chroma for server-side debugging.
- **Ownership/permissions never come from document text.** Authorization
  is unchanged: the document list is resolved server-side per user in
  `tools.py` / `orchestrator.py` before retrieval is attempted; retrieval
  iterates only caller-authorized `(document_id, title)` pairs. Metadata
  enrichment never writes any permission-like key.
- **Uploaded content cannot overwrite server-owned identities.**
  `enrich_chunks()` unconditionally overwrites `document_id`, `chunk_id`,
  `chunk_index`, `source_type`, `source_name`, `content_type`,
  `ingestion_policy`, adjacency ids and structural numbers, regardless of
  what the loader metadata contained. A crafted document carrying
  `document_id` in its text or metadata cannot influence the stored value
  (covered by test).
- **Isolation.** Evidence construction is per-authorized-document;
  chunk metadata is stored per-document collection. Cross-document
  leakage would require an authorized (id, title) pair; none exists.

## Deletion

Deletion is unchanged (per-document Chroma collection). Verified after
enrichment: collection removal succeeds and post-delete retrieval returns
nothing for the deleted document. `chunk_index` completeness (0..N-1, no
gaps) is asserted in tests so no enriched chunk can silently escape the
collection.

## Tests (all deterministic, no LLM)

`backend/tests/test_rag_metadata.py` (20 tests) covers:

- chunk ordering and document identity (document_id/chunk_index/chunk_id
  assignments, adjacency links, boundary chunks have no phantom neighbors);
- content is not altered by enrichment;
- Markdown heading_path construction (full path, skipped levels, absent
  when no headers) and fixture-level section nesting;
- PDF page_number normalization (1-based ints, ascending order);
- PPTX slide_number presence/ordering and the 3.2 fact with slide
  metadata;
- DOCX 1,425 table fact retained with structural metadata;
- legacy chunks without the new keys (enrichment + sanitize tolerate
  them);
- security: path scrubbing, server-owned key overwrite semantics,
  structural keys survive sanitization;
- `.htm` loads through the OpenJM alias (ECHO-4477) with neutral metadata;
  `.html` neutral metadata;
- deletion-shaped contract (single document scope, complete index range).

Pre-existing suites remain green: the policy tests, extractor tests,
deletion tests, tools/orchestrator/SQL suites all pass (78/78 total in
`backend/tests`, up from 58).

## Acceptance and benchmark results at Phase D

- Deterministic RAG benchmark (fixtures A–H + Phoenix retrieval): all PASS
  with Phase D enrichment active (same facts, same retrieval threshold).
- Backend pytest: 78/78 PASS.
- Structured foundation acceptance: PASS (7/7 checks).
- Structured chat acceptance: PASS (10 checks incl. grounded revenue
  answer 325.00, fail-closed behavior, SQL provenance, source deletion).
- Phoenix retrieval (Gate C retrieval-only leg): PASS — the expected
  `7-3-9-2-5` sequence is present in returned evidence passages.
- **End-to-end Phoenix answer generation (Gate C): FAIL — unchanged from
  Phase C.** The local Gemma endpoint again returned malformed
  special-token output (`<unused20><unused28>...`) instead of an answer.
  Per the Phase C checkpoint rule this remains an unresolved runtime
  model-gateway acceptance gate: it is NOT marked green, no retries were
  used to mask it, and no Phase D change compensates for it. Phase D's
  deterministic validation is independent of model-generation behavior.

## Remaining limitations

1. Gate C answer generation depends on the local model gateway, which
   intermittently emits malformed output. Tracked as a separate bounded
   model-gateway reliability checkpoint; out of Phase D scope by
   instruction.
2. `parent_id` remains unimplemented (no real parent identity exists in
   the installed loaders).
3. HTML heading/section structure is not detected by the installed
   loader; `.html`/`.htm` use neutral metadata.
4. PDF extraction-level artifacts (duplicated characters on overlapping
   text runs) are unchanged — extraction-level, not metadata-level.
5. `source` remains a path inside Chroma (server-side only). If a future
   admin UI surfaces raw Chroma rows, it must apply the same sanitizer.
6. The Evidence passage cap (2,000 chars) is unchanged; with current
   policy chunk bounds it cannot truncate Markdown/PDF/PPTX/DOCX/TXT
   fixture chunks, but a whole-document single-chunk document larger than
   the cap (e.g. a large HTML page) would still be head-truncated in the
   passage. Bounded correction proposed for Phase E (tail capture or cap
   aligned to chunk bound).

## Reproducing the probes

```bash
# before-state (loader metadata per fixture)
backend/.venv/bin/python tests/fixtures/rag/chunk_metadata_before_probe.py

# after-state (full pipeline, isolated store)
backend/.venv/bin/python tests/fixtures/rag/chunk_metadata_after_probe.py

# deterministic metadata tests
cd backend && .venv/bin/python -m pytest tests/test_rag_metadata.py -q
```
