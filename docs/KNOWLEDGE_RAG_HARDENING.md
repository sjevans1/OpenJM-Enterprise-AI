# Knowledge / RAG Hardening — Post Vertical Slice 2

## Objective

Harden the evidence quality returned by `knowledge.search` before Vertical Slice 3 treats HYBRID document + structured-data answers as mature.

This is not a replacement of DB-GPT, Chroma, the current embedding model, or the governed tool architecture. OpenJM continues to own policy, evidence contracts, permissions and orchestration while DB-GPT remains replaceable infrastructure behind the Knowledge capability.

## Verified starting point on latest `main`

The current implementation:

- uses `KnowledgeFactory.from_file_path(...)`;
- loads chunks through `EmbeddingAssembler`;
- explicitly forces `ChunkParameters(chunk_strategy="CHUNK_BY_SIZE")` for every supported document;
- uses `EmbeddingRetriever` with server-owned `rag_top_k` and score threshold;
- normalizes retrieval results into OpenJM `Evidence`;
- exposes Knowledge through the governed `knowledge.search` tool;
- resolves authorized documents server-side;
- preserves the Vertical Slice 1 upload / catalog / retrieval / citation / deletion contract.

Therefore the hardening target is evidence quality and document fidelity, not a parallel RAG stack.

## Architecture decision

Keep the working recursive size + overlap foundation as the fallback.

Improve the pipeline in this order:

```text
Extraction fidelity
        ↓
Structure preservation
        ↓
Document-aware chunk policy
        ↓
Rich chunk metadata
        ↓
Bounded context expansion
        ↓
Advanced semantic techniques only if benchmarks justify them
```

No semantic chunking, GraphRAG, alternative vector database, embedding-model replacement, autonomous agent runtime or major UI redesign is part of this work package unless deterministic benchmarks prove a need.

## Phase A — Baseline and observability

Before changing ingestion behavior:

1. Add deterministic fixtures under `backend/tests/fixtures/rag/`.
2. Include at minimum:
   - long narrative report;
   - Markdown heading-context fixture;
   - boundary-fact fixture;
   - DOCX financial table;
   - PDF financial table;
   - multi-page PDF;
   - slide-context PPTX.
3. Record current:
   - extracted/indexed content;
   - chunk count;
   - chunk metadata;
   - retrieval results per benchmark query;
   - answer correctness;
   - ingestion time;
   - retrieval latency.
4. Preserve these baseline results so improvements are measurable rather than inferred.

No production ingestion behavior changes during Phase A.

## Phase B — Extraction fidelity

Priority order:

### DOCX

Ensure paragraphs, headings and tables enter the Knowledge pipeline in logical order where practical.

A value that exists only in a Word table must be retrievable.

### PDF

Preserve existing DB-GPT extraction strengths while verifying:

- text fidelity;
- page metadata;
- detected table content;
- table boundaries / row meaning.

Do not replace the PDF parser wholesale unless benchmark evidence requires it.

### PPTX

Preserve slide boundaries and slide identifiers.

### Markdown / HTML

Preserve heading / section hierarchy where supported.

## Phase B closure status

Phase B is accepted.

Verified closure state:

- DOCX paragraphs/headings/tables are preserved in document order by the OpenJM-owned extractor.
- The DOCX table-only benchmark value `1,425` is now retrievable.
- All eight deterministic RAG fixtures plus Phoenix pass.
- Existing structured-data regressions remain green.
- Runtime structured acceptance now requires an isolated source catalog and cleans up its own source on normal failure paths.
- The production Knowledge stack remains DB-GPT + EmbeddingAssembler + Chroma + EmbeddingRetriever.
- The universal `CHUNK_BY_SIZE` override intentionally remains in place pending Phase C.

## Phase C — OpenJM-owned ingestion policy

Replace the universal OpenJM override of `CHUNK_BY_SIZE` with a document-aware policy layer.

Initial policy target:

```text
TXT      → recursive size + overlap
Markdown → header-aware where supported
DOCX     → structure-aware paragraphs/headings/tables
PDF      → page/content-aware with table protection
PPTX     → slide-aware
HTML     → heading/section-aware where supported
Fallback → recursive size + overlap
```

The policy belongs to OpenJM. DB-GPT strategies may be selected underneath it, but DB-GPT-specific decisions must not leak into the orchestrator or UI.

### Phase C promotion rule

Do not switch a document type to a different production chunk strategy merely because DB-GPT supports it.

For each document type:

1. introduce an OpenJM-owned policy decision;
2. benchmark the current `CHUNK_BY_SIZE` behavior against the candidate strategy using isolated indexes;
3. compare retrieval correctness, structural fidelity, chunk count, metadata, ingestion cost and retrieval latency;
4. promote the candidate only when it preserves all existing passes and provides a measurable fidelity/quality benefit;
5. otherwise retain recursive size + overlap as the production fallback.

The policy seam itself is required. A different strategy for every document type is not.

### Phase C closure status

Phase C is complete.

- The OpenJM-owned policy seam is `backend/app/services/ingestion_policy.py`:
  `OpenJMIngestionPolicy.for_document(...)` returns an `IngestionDecision`
  (extension, knowledge implementation, chunk strategy, chunk parameters,
  policy name, rationale). It is the only place in the codebase that knows
  DB-GPT strategy names; the orchestrator, API, tool registry and UI are
  unchanged and strategy-agnostic.
- `DBGPTKnowledgeEngine.ingest` now obtains chunking from the policy; the
  hard-coded universal `ChunkParameters(chunk_strategy="CHUNK_BY_SIZE")` is
  gone. `ingest_with_strategy` exists only for the isolated comparison
  harness.
- Installed-capability matrix, baseline-vs-candidate evidence, promoted and
  rejected strategies are recorded in `docs/RAG_CHUNK_POLICY_EVALUATION.md`
  and `backend/tests/fixtures/rag/chunk_policy_evaluation.json`.
- Promotions: Markdown -> `CHUNK_BY_MARKDOWN_HEADER` (all four markdown
  fixtures scored equal or higher, sections isolated, Header1-6 metadata
  added, no fact loss).
- Kept on the proven fallback: TXT, DOCX, PDF, PPTX, HTML and unknown types.
  DOCX retains the OpenJMDocxKnowledge extractor; `CHUNK_BY_PARAGRAPH` was
  rejected because it shatters tables into row-level chunks.
- All eight RAG fixtures plus Phoenix pass; the full backend test suite is
  green; structured foundation and structured chat acceptance pass.
- `baseline_benchmark_results.json` (the Phase B authoritative baseline) is
  untouched by Phase C artifacts.


### Phase C review checkpoint

Phase C policy implementation and deterministic benchmarks are complete. The only
production strategy change is promotion of Markdown to header-aware chunking;
other supported document types retain the proven size+overlap baseline.

Acceptance evidence at Phase C:
- 58/58 backend tests passed.
- Eight RAG fixture retrieval checks and Phoenix retrieval check passed.
- Structured foundation and structured chat acceptance passed.
- End-to-end Phoenix **answer generation** did not pass that run: the local
  Gemma endpoint returned malformed/special-token output. Treat this as an
  unresolved runtime acceptance gate rather than marking Gate C green based
  on retrieval-only evidence. Investigate the raw model response and request
  formatting in a separate bounded model-gateway reliability checkpoint.
  Do not mask the failure with unlimited retries or change chunk policy to
  compensate for malformed generation.

Known edge cases to capture in later quality checks:
- The upload API advertises `.htm` but installed DB-GPT's file factory only
  maps `.html`; provide a tested compatibility fix or stop advertising
  unsupported `.htm` before final hardening acceptance.
- Phase C Markdown fixtures show higher top retrieval scores and stronger
  heading separation, but scores alone are not proof of general precision.
  Add a long (>2,000-character) single-section Markdown fixture with a
  fact near its end to check whether the current 2,000-character evidence
  passage limit truncates needed context.

Phase D may add structural metadata and deterministic tests while the
model-gateway issue is investigated separately. PR #3 remains draft;
Gate J and Gates A–I, including successful runtime Gate C answer generation,
are required before merge.

### Phase D closure status

Phase D (structural metadata) is complete; details and measured before/after
state live in `docs/RAG_METADATA_EVALUATION.md`.

- Enrichment seam: `app/services/chunk_metadata.py::enrich_chunks` runs
  post-split, pre-persist inside `DBGPTKnowledgeEngine.ingest_with_strategy`.
  Chunk content, scores, chunk counts and retrieval behavior are unchanged;
  enrichment only writes chunk metadata.
- Implemented fields (server-derived, never fabricated): `document_id`,
  `chunk_id`, `chunk_index`, `source_type`, `source_name`, `content_type`,
  `ingestion_policy`, `heading_path` (Markdown), `page_number` (PDF),
  `slide_number` (PPTX via new `OpenJMPPTXKnowledge`),
  `previous_chunk_id`/`next_chunk_id` (split-order adjacency).
  `parent_id` intentionally not set (no real parent identity in installed
  loaders); documented as unsupported.
- Legacy loader keys (`Header1..6`, `page`, `type`, `title`) are preserved
  for backward compatibility; previously indexed documents lacking the new
  keys remain retrievable (metadata read via `.get()` everywhere).
- Security: internal filesystem paths are scrubbed from customer-facing
  Evidence (`sanitize_for_evidence`); server-owned keys overwrite any
  loader/content-supplied value (forgery test included); authorization
  remains server-side document resolution; deletion unchanged and verified.
- `.htm` compatibility: root-caused (DB-GPT factory matches `html` only) and
  fixed via `OpenJMHtmlKnowledge` in the OpenJM knowledge resolution seam;
  verified live end to end (upload → Evidence → correct answer).
- Long-section quality check: >2,000-char single-section Markdown fixture
  added (`long_section.md`, probe fact `BOUNDARY-FACT-PHASE-D-9931`); the
  fact survives into Evidence (no loss; the policy's 512/50 chunk bound
  keeps sections below the 2,000-char passage cap). Phase C's "4000/200
  fallback" note is corrected in the Phase D evaluation doc. Residual
  whole-document-chunk truncation risk documented with a bounded Phase E
  correction proposal (not implemented, out of Phase D scope).
- Validation at Phase D: 78/78 backend tests (20 new deterministic metadata
  tests); RAG benchmark A–H + Phoenix retrieval all PASS; structured
  foundation acceptance PASS; structured chat acceptance PASS; live PPTX
  slide-number Evidence and `.htm` Evidence verified.
- **Gate C answer generation remains red**: the formal Phoenix acceptance
  run again failed on malformed Gemma special-token output. Not masked by
  retries; Phase D validation is deterministic and independent of model
  generation. Gate C stays an unresolved runtime gate per the Phase C rule.

Phase E (bounded neighbour/parent expansion) is not started.

## Phase D — metadata enrichment

Indexed chunks should progressively carry enough structure for retrieval and citation:

- document_id;
- source name/type;
- page or slide where available;
- heading / section path where available;
- content_type;
- chunk_index;
- parent_id where applicable;
- previous_chunk_id;
- next_chunk_id;
- version / scope metadata where applicable.

Metadata must survive:

```text
index
  ↓
knowledge.search
  ↓
OpenJM Evidence
```

Existing Evidence/API fields remain backward compatible.

## Phase E — bounded context expansion

Only after chunk ordering / structural metadata is reliable:

1. retrieve semantic primary matches;
2. optionally include adjacent chunks;
3. optionally promote relevant parent/section context;
4. deduplicate;
5. enforce evidence/token limits;
6. preserve source identity and citations.

Neighbour expansion should be attempted before mandatory semantic chunking because it is more deterministic, cheaper and easier to debug.

## Relationship to Vertical Slice 3 — HYBRID

HYBRID is expected to compose:

```text
knowledge.search
       +
structured.query
       ↓
normalized evidence
       ↓
hybrid synthesis
```

The hardening work therefore stays entirely behind `knowledge.search`.

The orchestrator should not need to know whether Knowledge internally used page-aware parsing, header-aware chunking, tables, parent context or neighbours.

This phase should be completed before HYBRID is treated as production-mature. Slice 3 design work may proceed in parallel, but the first mature HYBRID acceptance should consume the hardened Knowledge capability.

## Gate J — Document Intelligence / RAG Fidelity

Hardening is complete only when deterministic acceptance proves:

1. Markdown, PDF, DOCX and PPTX fixtures ingest successfully.
2. DOCX table-only facts are retained and retrievable.
3. PDF table values remain retrievable with correct page/source context where available.
4. Heading-dependent questions retrieve the correct section context.
5. PPTX evidence identifies the correct slide where available.
6. Boundary/context questions receive required neighbour or parent context when the policy enables expansion.
7. Evidence retains document identity and structural provenance.
8. Unrelated context does not materially increase without benchmark justification.
9. Deletion removes all retrievable evidence.
10. No unauthorized or cross-document evidence leakage is introduced.
11. Existing Gates A–I remain green.

## Non-goals

Do not expand this work package into:

- HYBRID synthesis implementation;
- autonomous agent loops;
- multi-agent RAG;
- write actions;
- MCP orchestration;
- GraphRAG / full knowledge graph;
- mandatory semantic chunking;
- vector database migration;
- embedding-model replacement without benchmark evidence;
- code-aware chunking unless code ingestion becomes a product requirement.

## Working rule

> Improve the quality of the evidence returned by `knowledge.search` without changing the rest of OpenJM's architecture unnecessarily.

## Implementation order

```text
A. Deterministic fixtures + baseline
        ↓
B. Extraction fidelity
        ↓
C. Document-aware ingestion policy
        ↓
D. Structural metadata
        ↓
E. Bounded neighbour / parent expansion
        ↓
Gate J + Gates A–I regression
        ↓
Merge hardening
        ↓
Vertical Slice 3 HYBRID implementation / maturity
```
