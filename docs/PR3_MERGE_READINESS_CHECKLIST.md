# PR #3 Merge Readiness Checklist

This checklist is maintained on an isolated planning branch so it cannot
interfere with Hermes' active work on `hardening/knowledge-rag-fidelity`.

It is a review aid only. Do not merge PR #3 from this branch.

## Required before PR #3 can be considered merge-ready

### Phase A — deterministic RAG baseline
- [x] deterministic fixtures committed
- [x] baseline report committed
- [x] initial DOCX table-extraction failure documented

### Phase B — extraction fidelity
- [x] OpenJM-owned DOCX extractor
- [x] paragraph/heading/table order preserved
- [x] DOCX table-only `1,425` retrievable
- [x] all prior RAG fixtures remain green
- [x] structured acceptance source contamination diagnosed
- [x] structured acceptance scripts fail fast on dirty catalogs and clean up
      their own test sources

### Phase C — document-aware chunk policy
- [x] OpenJM-owned ingestion-policy seam
- [x] strategy compatibility matrix verified against installed DB-GPT
- [x] Markdown header-aware strategy promoted with benchmark evidence
- [x] DOCX/PDF/PPTX/TXT/HTML candidates rejected or retained on evidence
- [x] Phase B authoritative baseline left untouched
- [x] full deterministic regression green at Phase C

### Phase D — metadata / provenance
- [x] post-split, pre-persist metadata enrichment
- [x] document/chunk identity and ordering
- [x] Markdown heading path
- [x] PDF page number
- [x] PPTX slide number
- [x] previous/next chunk ids
- [x] path sanitization
- [x] `.htm` compatibility
- [x] DOCX table regression remains green
- [ ] customer-visible `source_name` verified to use the original upload
      name, never the UUID-prefixed storage filename
- [ ] long-section fixture text corrected to match the proven 512/50
      production behavior
- [ ] Phase D closure regressions rerun after the two items above

### Model gateway / runtime Gate C
- [ ] exact malformed-output trigger characterized
- [ ] failed generation does not leave conversation history in an invalid
      state or this behavior is otherwise safely modeled
- [ ] malformed special-token output is rejected, not returned to users
- [ ] bounded recovery policy implemented only if evidence justifies it
- [ ] malformed output is not persisted as a successful assistant message
- [ ] formal Phoenix end-to-end Gate C passes with `7-3-9-2-5`
- [ ] General memory still passes
- [ ] Structured `325` still passes
- [ ] no unlimited retry-until-green behavior

### Phase E — bounded context expansion
- [ ] direct, document-scoped neighbour lookup mechanism verified
- [ ] hard adjacency fixture demonstrates a real baseline gap
- [ ] bounded ±1 expansion benchmarked
- [ ] primary evidence preferred under evidence/token budget
- [ ] neighbour evidence deduplicated and provenance-labeled
- [ ] no cross-document expansion
- [ ] legacy chunks without adjacency metadata remain compatible
- [ ] context expansion promoted only if it produces measurable benefit
- [ ] HTML/passage-cap edge addressed only if a deterministic fixture proves
      actual customer-visible loss
- [ ] Phase E evaluation doc committed

## Final regression gates

Before PR #3 moves out of draft:

- [ ] backend pytest — all green
- [ ] deterministic RAG benchmark A–H — all green
- [ ] Phoenix retrieval — green
- [ ] formal Phoenix answer-generation Gate C — green
- [ ] structured foundation — green
- [ ] structured chat acceptance — green
- [ ] conversation-memory Gate A — green
- [ ] document catalog Gate B — green
- [ ] document deletion/no stale evidence — green
- [ ] source/path/storage-name redaction — green
- [ ] cross-document authorization isolation — green
- [ ] DOCX `1,425` — green
- [ ] PDF `18.5%` — green
- [ ] PDF `PHOENIX-8822` — green
- [ ] PPTX `3.2` + slide number — green
- [ ] Markdown heading-path behavior — green
- [ ] `.htm` compatibility — green
- [ ] frontend build unaffected / clean

## Known limitations that may remain acceptable if documented

These should not be silently converted into merge blockers unless a
deterministic product requirement proves otherwise:

- no synthetic `parent_id` until a real parent identity exists;
- no GraphRAG / knowledge graph;
- no semantic chunking requirement;
- no new vector database;
- no embedding-model replacement;
- no autonomous retrieval loop;
- PDF extraction artifacts inherited from the existing loader, provided
  required acceptance facts and provenance remain correct.

## PR hygiene before merge authorization

- [ ] active hardening branch synced with remote
- [ ] no timing-only benchmark noise
- [ ] no exploratory/untracked development files committed
- [ ] PR description updated with Phase A–E outcomes
- [ ] changed-file review completed
- [ ] no secrets / runtime data / uploaded documents committed
- [ ] PR marked ready only after every required gate above is green
- [ ] merge remains blocked until explicit user authorization
