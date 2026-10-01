# RAG Chunk Policy Evaluation — Phase C

This document records the Phase C document-aware chunk policy evaluation:
what the installed DB-GPT 0.8.2 actually supports, how the proven baseline
compared against each candidate, which strategies were promoted, which were
rejected and why.

Evidence artifacts:

- `backend/tests/fixtures/rag/chunk_policy_evaluation.json` (harness output)
- `backend/tests/fixtures/rag/chunk_policy_comparison.py` (the harness)
- `backend/tests/fixtures/rag/baseline_benchmark_results.json` (Phase B
  authoritative baseline; untouched by Phase C)

## Installed strategy compatibility matrix

Read from the installed packages (`dbgpt` 0.8.2, `dbgpt-ext` 0.8.2), not
from docs.

| Extension | Knowledge implementation | Supported strategies | DB-GPT default |
|-----------|--------------------------|---------------------|----------------|
| .txt      | TXTKnowledge             | CHUNK_BY_SIZE, CHUNK_BY_SEPARATOR | CHUNK_BY_SIZE |
| .md       | MarkdownKnowledge         | CHUNK_BY_SIZE, CHUNK_BY_MARKDOWN_HEADER, CHUNK_BY_SEPARATOR | CHUNK_BY_MARKDOWN_HEADER |
| .docx     | DocxKnowledge             | CHUNK_BY_SIZE, CHUNK_BY_PARAGRAPH, CHUNK_BY_SEPARATOR | CHUNK_BY_SIZE |
| .pdf      | PDFKnowledge              | CHUNK_BY_SIZE, CHUNK_BY_PAGE, CHUNK_BY_SEPARATOR | CHUNK_BY_SIZE |
| .pptx     | PPTXKnowledge             | CHUNK_BY_SIZE, CHUNK_BY_PAGE, CHUNK_BY_SEPARATOR | CHUNK_BY_SIZE |
| .html     | HTMLKnowledge             | CHUNK_BY_SIZE, CHUNK_BY_SEPARATOR | CHUNK_BY_SIZE |
| .htm      | (none; factory maps html only) | raises on load | - |
| unknown   | (factory raises)          | -                   | - |

Installed defaults (`ChunkParameters`): `chunk_size=512`, `chunk_overlap=50`,
`separator="\n"`, `enable_merge=None`.

### Splitter behavior notes (installed source)

- `CHUNK_BY_SIZE` -> `RecursiveCharacterTextSplitter` (512/50). Loader
  metadata is copied verbatim onto every chunk. When a chunk carries
  metadata whose alphabetically-largest key value is a string, that value is
  prepended to the chunk content before splitting (visible in PDF chunks).
- `CHUNK_BY_MARKDOWN_HEADER` -> `MarkdownHeaderTextSplitter`. Splits on
  `#`..`######`; each chunk carries `Header1`..`Header6` metadata and content
  prefixed `"H1-H2": ...`. Oversized sections fall back to a recursive
  4000/200 split internally (native defaults), so no section is ever
  silently dropped.
- `CHUNK_BY_PAGE` -> `PageTextSplitter`. `split_text` is the identity
  function: it never splits; it preserves one chunk per loader Document.
  The PDF and PPTX loaders already emit one Document per page/slide with
  `page` metadata, so PAGE ≈ identity over pre-bounded documents.
- `CHUNK_BY_PARAGRAPH` -> `ParagraphTextSplitter`. Splits on `"\n"` with no
  size bound and no merge.
- `CHUNK_BY_SEPARATOR` -> `SeparatorTextSplitter`. Installed bug: the
  constructor pops `enable_merge` without a default, so building via
  `ChunkStrategy.match()` with default `ChunkParameters` raises
  `KeyError: 'enable_merge'`. The harness works around it by passing
  `enable_merge=False` explicitly. Also: the HTML and PDF loaders strip
  newlines during extraction, so `separator="\n"` has no split points on
  those types.

## Production policy after Phase C

| Type | Strategy | Policy name | Changed in Phase C |
|------|----------|--------------|--------------------|
| TXT  | CHUNK_BY_SIZE | fallback_recursive_size_overlap | no |
| Markdown | CHUNK_BY_MARKDOWN_HEADER | markdown_header_aware | **yes (promoted)** |
| DOCX | CHUNK_BY_SIZE | docx_structure_preserve | no |
| PDF  | CHUNK_BY_SIZE | fallback_recursive_size_overlap | no |
| PPTX | CHUNK_BY_SIZE | fallback_recursive_size_overlap | no |
| HTML/HTM | CHUNK_BY_SIZE | fallback_recursive_size_overlap | no |
| unknown | CHUNK_BY_SIZE | fallback_recursive_size_overlap | no |

DOCX keeps the OpenJMDocxKnowledge extractor (Phase B), which remains
non-negotiable; the strategy change for DOCX is none.

## Baseline vs candidate results

Full data in `chunk_policy_evaluation.json`. Retrieval scores are the
production-path top score (threshold 0.25). Ingestion figures include the
one-off embedding model load (~2.8s) per isolated run; the embedding model
itself is unchanged.

### Markdown — CANDIDATE PROMOTED

| Fixture | Metric | CHUNK_BY_SIZE | CHUNK_BY_MARKDOWN_HEADER |
|---------|--------|---------------|--------------------------|
| heading_context.md | top score | 0.657 | **0.881** |
| heading_context.md | chunks | 1 (all three projects mixed) | 5 (one per section) |
| heading_context.md | unrelated-section mixing | FAIL (Atlas+Phoenix+Orion in one chunk) | PASS (isolated) |
| adjacent_context.md | top score | 0.654 | **0.759** |
| long_report.md | top score | 0.544 | **0.592** |
| long_report.md | chunks | 4 | 5 (one per section) |
| phoenix_launch_protocol.md | top score | 0.849 | **0.857** |
| All MD fixtures | expected fact found | PASS | PASS |

Chunk-count rises are explained: header-aware chunking emits one chunk per
section instead of packing multiple sections into a 512-char window. All
expected facts remain retrievable; heading hierarchy is now in metadata
(Header1/2) and in the content prefix.

Representative chunks (heading_context.md):

- baseline CHUNK_BY_SIZE (single chunk, everything mixed):
  `# Project Atlas\n\n## Team Composition\nThe Atlas team has 12 engineers and 4 data scientists.\n\n## Technical Stack\n... # Project Phoenix ... # Project Orion ...`
- candidate MARKDOWN_HEADER (isolated):
  `"Project Atlas-Team Composition": The Atlas team has 12 engineers and 4 data scientists.`
  with metadata `{"Header1": "Project Atlas", "Header2": "Team Composition", ...}`

Promotion rule outcome: A (all benchmark facts pass), B (regressions green),
C (structural fidelity better: sections isolated, header metadata), D
(retrieval precision better on all four MD fixtures), E (chunk-count rise
explained), F (ingestion/retrieval latency unchanged), G (deletion behavior
untouched — collection-level, strategy-independent).

### DOCX — CANDIDATE REJECTED, BASELINE KEPT

| Metric | CHUNK_BY_SIZE (baseline) | CHUNK_BY_PARAGRAPH (candidate) |
|--------|-------------------------|-------------------------------|
| chunks | 1 | 10 (table shattered: header, separator, one row per row) |
| whole table in one chunk | PASS | FAIL |
| `1,425` retrievable | PASS | PASS (top score 0.625 vs 0.536) |
| row meaning | preserved | destroyed (rows divorced from header) |

CHUNK_BY_PARAGRAPH splits on `\n` with no size bound, so the markdown table
emitted by OpenJMDocxKnowledge is broken into one chunk per line: the header
row, the separator row, and each data row live in separate chunks. Row
meaning ("Analytics Services" = 1,425) only survives if header and value are
read together; the candidate destroys that relationship even though the raw
fact string remains retrievable. Rejected on structural fidelity (promotion
rule C). DOCX extraction itself (OpenJMDocxKnowledge) is untouched.

### PDF — CANDIDATE REJECTED (no advantage), BASELINE KEPT

| Fixture | Metric | CHUNK_BY_SIZE | CHUNK_BY_PAGE |
|---------|--------|---------------|---------------|
| financial_table.pdf | chunks | 1 | 1 |
| financial_table.pdf | top score | 0.444 | 0.444 (identical) |
| multi_page_report.pdf | chunks | 3 | 3 |
| multi_page_report.pdf | top score | 0.513 | 0.513 (identical) |
| `18.5%` fact | PASS | PASS |
| PHOENIX-8822 fact | PASS | PASS |
| page metadata present | PASS | PASS |

Byte-identical chunk sets: the PDF loader already emits one page-bounded
Document per page (with `page` metadata and `type: excel` for table pages),
and those documents are smaller than chunk_size, so the recursive splitter
never splits them. CHUNK_BY_PAGE is an identity function on top. No
measurable advantage; the proven baseline is retained. Note for a future
phase: on real-world dense pages (>512 chars), CHUNK_BY_PAGE would produce
whole-page chunks that the recursive splitter would otherwise bound —
revisit only with a benchmark fixture that has dense pages.

### PPTX — CANDIDATE REJECTED (no advantage), BASELINE KEPT

| Metric | CHUNK_BY_SIZE | CHUNK_BY_PAGE |
|--------|---------------|---------------|
| chunks | 4 | 4 |
| slide title+body together | PASS | PASS |
| `3.2` CAC fact | PASS | PASS |
| top score | 0.620 | 0.620 (identical) |

The PPTX loader already emits one Document per slide; both strategies
produce identical chunk sets. DB-GPT 0.8.2 has no real slide-aware strategy
beyond this (no slide-number metadata is emitted; that is Phase D work).
Baseline retained.

### TXT — CANDIDATE REJECTED, BASELINE KEPT

| Metric | CHUNK_BY_SIZE | CHUNK_BY_SEPARATOR* |
|--------|---------------|---------------------|
| chunks | 2 | 1 |
| BR-7749-Q3 fact | PASS | PASS |
| top score | 0.786 | 0.763 |

*only runs with an explicit `enable_merge=False` workaround (installed
KeyError bug). No advantage; TXT stays on the recursive size+overlap
baseline per plan.

### HTML — CANDIDATE REJECTED, BASELINE KEPT

The HTML loader strips newlines during extraction, so the SEPARATOR
strategy has no split points; both strategies produce one identical chunk
(top score 0.710 both). No section-aware strategy exists in the installed
HTMLKnowledge. Baseline retained. (ECHO-4477 fact PASS on both.)

## Strategy selected per document type

- TXT: CHUNK_BY_SIZE (fallback policy)
- Markdown: CHUNK_BY_MARKDOWN_HEADER (promoted)
- DOCX: CHUNK_BY_SIZE + OpenJMDocxKnowledge (Phase B extractor retained)
- PDF: CHUNK_BY_SIZE (fallback policy)
- PPTX: CHUNK_BY_SIZE (fallback policy)
- HTML/HTM: CHUNK_BY_SIZE (fallback policy)
- unknown: CHUNK_BY_SIZE (fallback policy)

## Strategies rejected and why

| Strategy | Type(s) | Why rejected |
|----------|---------|--------------|
| CHUNK_BY_PARAGRAPH | DOCX | shatters tables into row-level chunks; destroys row meaning |
| CHUNK_BY_PAGE | PDF, PPTX | byte-identical to baseline on fixtures; no measurable advantage |
| CHUNK_BY_SEPARATOR | TXT, HTML | installed enable_merge KeyError; no retrieval advantage; HTML loader strips newlines so no split points |

## Metadata differences observed

- Markdown header-aware chunks add `Header1`..`Header6` keys and a
  `"H1-H2": ` content prefix (both improve citation context).
- PDF chunks (both strategies) carry `page`, `type` (excel/text), `title`,
  `source`.
- PPTX chunks carry only `source` (no slide number) — unchanged, Phase D.
- DOCX chunks carry `source`; table content lives in content, not metadata.

## Latency summary

Retrieval latency is unchanged across strategies (14-25 ms; well under the
Phase B envelope). Ingestion latency is dominated by the embedding model
load and is unchanged (~2.8s per isolated cold run; warm ingestion
55-155 ms). No strategy introduced a latency regression.

## Deletion behavior

Deletion operates on the Chroma collection per document id and is
strategy-independent; no change. The existing deletion tests remain green.

## Remaining known gaps

1. No slide-number metadata for PPTX chunks (Phase D).
2. No heading/section metadata for non-Markdown types (Phase D).
3. PDF loader emits duplicated-character artifacts on overlapping text runs
   (e.g. `PPCrrooondjefuicdcteetndLcinReeOI`) — extraction-level, unchanged
   by Phase C, tracked as a known extraction gap.
4. Dense-page PDFs (>512 chars/page) were not exercised by fixtures;
   page-aware chunking may warrant a revisit with a dense-page fixture.
5. `.htm` uploads fall back safely but have no dedicated installed Knowledge
   class (factory maps `html` only) — ingestion of `.htm` via the API would
   fail in the factory; the upload API accepts `.htm`. Known edge; the
   policy falls back rather than crashing in the policy seam itself.

## Reproducing

```bash
cd backend
.venv/bin/python tests/fixtures/rag/chunk_policy_comparison.py
```

The harness never touches `baseline_benchmark_results.json`.
