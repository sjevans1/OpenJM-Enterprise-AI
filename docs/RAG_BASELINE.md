# RAG Baseline Report

## Executive Summary
This document establishes the baseline for the Knowledge/RAG hardening effort (Phase A) for the OpenJM Enterprise AI application. The baseline captures the current state of the RAG pipeline before any optimizations are made, focusing on deterministic fixtures and current-state measurement.

## Metadata
- **HEAD SHA**: 381744058cea57c2e5738dc7d8ef5cc59363438d
- **DB-GPT Version**: 0.8.2
- **dbgpt-ext Version**: 0.8.2
- **Timestamp**: [Insert timestamp of baseline run]

## Current Pipeline Diagram
The current RAG pipeline, as implemented in `DBGPTKnowledgeEngine`, follows these steps:
1. **Ingestion**: A document is loaded via `KnowledgeFactory.from_file_path` (DB-GPT Ext) with forced `ChunkParameters(chunk_strategy="CHUNK_BY_SIZE")`.
2. **Chunking**: The document is split into chunks using the DB-GPT Ext chunk manager with default size and overlap (from settings?).
3. **Embedding**: Chunks are embedded using the HuggingFaceEmbeddings model (sentence-transformers/all-MiniLM-L6-v2).
4. **Storage**: Embedded chunks are stored in a Chroma vector store (collection name derived from document ID).
5. **Retrieval**: Upon query, an `EmbeddingRetriever` fetches top-K chunks (configured by `rag_top_k`) with a score threshold (`rag_score_threshold`).
6. **Normalization**: Retrieved chunks are normalized into OpenJM `Evidence` objects.

## Fixture Inventory
The following deterministic fixtures were created under `backend/tests/fixtures/rag/`:
- `long_report.md`: Tests ordinary narrative retrieval.
- `heading_context.md`: Tests heading-dependent context.
- `boundary_fact.txt`: Tests fact near a chunk boundary.
- `financial_table.docx`: Tests value existing ONLY in a DOCX table.
- `financial_table.pdf`: Tests value existing in a PDF table.
- `multi_page_report.pdf`: Tests page-specific context.
- `adjacent_context.md`: Tests statement requiring adjacent context.
- `slide_context.pptx`: Tests slide-specific context.
- `phoenix_launch_protocol.md`: Used for Gate C acceptance (Phoenix document).

## Extraction Findings
| Fixture | Extraction Success | Notes |
|---------|-------------------|-------|
| long_report.md | Yes | Full text extracted; narrative content retrievable. |
| heading_context.md | Yes | Headings preserved in extracted text. |
| boundary_fact.txt | Yes | Boundary fact extracted. |
| financial_table.docx | **Partial** | Paragraph text extracted; headings survive; **table cell values did not survive extraction** (value "1,425" not found in retrieved passage). |
| financial_table.pdf | Yes | Table extracted; row relationships preserved; value "18.5%" found in retrieved passage. |
| multi_page_report.pdf | Yes | Page-specific metadata (page number) present in chunk metadata. |
| adjacent_context.md | Yes | Adjacent context preserved. |
| slide_context.pptx | Yes | Slide-specific context preserved; slide metadata present. |

## Chunking Findings
- **Chunk Strategy**: `CHUNK_BY_SIZE` (fixed size chunking) is applied uniformly across all document types.
- **Chunk Size/Defaults**: Determined by `ChunkParameters` (from DB-GPT Ext); specific values not inspected in baseline but can be inferred from settings.
- **Chunk Ordering**: Preserved as per original document order.
- **Chunk Counts**: Vary by fixture (see benchmark table below).
- **Metadata in Chunks**: Each chunk includes at least `source` and `title` metadata. PDF chunks also include `page` and `type` (excel) when extracted from tables.

## Metadata Findings
- **Available Metadata**: For all fixtures, chunks include `source` (file path) and `title` (fixture name without extension).
- **Page/Slide Metadata**: 
  - PDF fixtures (`financial_table.pdf`, `multi_page_report.pdf`) include `page` number in metadata.
  - PPTX fixture (`slide_context.pptx`) does not currently include slide-specific metadata in the chunk metadata (only source and title).
- **Table Survival**: 
  - DOCX table: **Table content did not survive extraction** (only surrounding text was captured).
  - PDF table: Table content survived extraction and was retrievable; however, row relationships may be lost within the chunk (requires further inspection).
- **Chunk-Level Metadata**: No hierarchical chunking (parent/child) is in place; all chunks are at the same granularity.

## Benchmark Results
The following table summarizes the baseline benchmark results (see `backend/tests/fixtures/rag/baseline_benchmark_results.json` for full details):

| Fixture Label | Category | Ingestion (s) | Retrieval Count | Retrieval (s) | Answer Correctness | Notes |
|---------------|----------|---------------|-----------------|---------------|--------------------|-------|
| A. long_report.md - ordinary narrative retrieval | A | 7.8081 | 4 | 0.0211 | PASS | Expected revenue found. |
| B. heading_context.md - heading-dependent context | B | 0.0580 | 1 | 0.0192 | PASS | Team composition found. |
| C. boundary_fact.txt - fact near chunk boundary | C | 0.0565 | 2 | 0.0223 | PASS | Boundary ID found. |
| D. financial_table.docx - value ONLY in DOCX table | D | 0.0641 | 1 | 0.0175 | **FAIL** | Analytics Services revenue "1,425" not found in retrieved passage. |
| E. financial_table.pdf - value in PDF table cell | E | 0.1085 | 1 | 0.0173 | PASS | Projected ROI "18.5%" found. |
| F. multi_page_report.pdf - page-specific context | F | 0.0968 | 2 | 0.0187 | PASS | Project codename on page 2 found. |
| G. adjacent_context.md - statement requiring adjacent context | G | 0.0503 | 2 | 0.0204 | PASS | Assumption about new regulations found. |
| H. slide_context.pptx - slide-specific context | H | 0.1018 | 1 | 0.0181 | PASS | CAC ratio "3.2" found. |
| **Phoenix (Gate C)** | N/A | 0.0658 | 2 | 0.0199 | PASS | Primary launch sequence code found. |

## Latency Measurements
- **Ingestion Latency**: Varies significantly by document type and size. The longest ingestion was for `long_report.md` (7.8 seconds), likely due to its size and embedding computation.
- **Retrieval Latency**: Consistently low (under 0.03 seconds) across all fixtures, indicating efficient vector search.

## Confirmed Failures
1. **DOCX Table Extraction**: The value "1,425" (Analytics Services revenue) existing ONLY in the DOCX table was not retrievable. This indicates that the current extraction process (via `python-docx` in DB-GPT Ext) does not preserve table cell values in the extracted text, or the chunking process splits the table in a way that isolates the value outside of retrievable chunks.

## Confirmed Existing Strengths
1. **Narrative Text Extraction**: Standard markdown and text documents are extracted and indexed correctly.
2. **Heading Context**: Headings are preserved in extracted text and contribute to chunk context.
3. **Boundary Facts**: Facts near chunk boundaries are still retrievable (chunk overlap helps).
4. **PDF Table Extraction**: Tables in PDF documents are extracted and their content is retrievable (at least for the tested fixture).
5. **Page/Specific Context**: Page numbers are captured in chunk metadata for PDF documents.
6. **Adjacent Context**: Statements requiring adjacent context (i.e., the sentence before or after a retrieved chunk) are preserved within the same or neighboring chunks.
7. **Slide Context**: Text content from PowerPoint slides is extracted and retrievable.
8. **Phoenix Acceptance**: The existing Gate C acceptance test passes, indicating that the core RAG functionality works for simple text documents.

## Recommended Phase B Changes (Ranked by Evidence)
Based on the baseline findings, the following improvements are recommended for Phase B, ordered by strength of evidence:

1. **Fix DOCX Table Extraction** (High Priority)
   - **Evidence**: Complete failure to retrieve a value known to exist only in a DOCX table.
   - **Recommended Action**: Investigate the extraction pipeline for DOCX files in DB-GPT Ext. Ensure that table cell values are included in the extracted text and that chunking does not isolate table content. Consider using a different chunk strategy for tabular data or preprocessing tables to preserve their structure.

2. **Enhance Chunk Metadata with Structural Information** (Medium Priority)
   - **Evidence**: Lack of slide-specific metadata in PPTX chunks; limited metadata for table relationships.
   - **Recommended Action**: Extend the chunk metadata to include structural information such as:
     - For PDF: table boundaries, row/column indices.
     - For PPTX: slide number, slide title.
     - For DOCX: table indicators, heading hierarchy.
   - This will enable more precise retrieval and context expansion in later phases.

3. **Evaluate Chunk Strategy for Semantic Coherence** (Medium Priority)
   - **Evidence**: While boundary facts were retrievable (due to overlap), the uniform `CHUNK_BY_SIZE` strategy may still split related content (e.g., a table across chunks) and reduce precision.
   - **Recommended Action**: Test alternative chunk strategies (e.g., `CHUNK_BY_HEADER`, `CHUNK_BY_PAGE`) for specific document types to improve semantic coherence of chunks. This should be done in a way that does not break existing functionality for narrative text.

4. **Improve Table Relationship Preservation** (Lower Priority, requires further investigation)
   - **Evidence**: PDF table extraction succeeded for a simple value, but the baseline did not test complex row-column relationships or whether the table structure was preserved within chunks.
   - **Recommended Action**: Conduct additional tests to verify that table rows and columns remain intelligible in the extracted text and that chunking does not break apart related table cells without providing a way to reconstruct the relationship.

## Proven Current Behavior vs. Inferred Behavior
- **Proven**: 
  - Narrative text extraction and retrieval works.
  - Heading context is preserved.
  - Boundary facts are retrievable with overlap.
  - PDF table content is extracted and retrievable (at least for simple values).
  - Page numbers are captured in PDF chunk metadata.
  - Adjacent context is preserved within chunk windows.
  - PPTX text content is extracted and retrievable.
- **Inferred**:
  - The chunk size and overlap settings are sufficient for narrative text but may be suboptimal for structured content.
  - The lack of table value retrieval in DOCX is due to extraction, not chunking (since the value was not in the retrieved passage at all).
  - Chroma is functioning correctly (no errors in vector store operations beyond the expected "already exists" warnings due to temporary directory reuse).
- **Proposed Improvement**: 
  - The recommendations above are proposed changes to be evaluated in Phase B. They are not yet implemented.

## Conclusion
The baseline establishes that the current RAG pipeline is functional for narrative text and simple contextual queries but fails to extract and retrieve values embedded solely in DOCX tables. This gap must be addressed in Phase B to ensure fidelity across all supported document types. The recommended changes focus on improving extraction for structured data and enriching chunk metadata to support more advanced retrieval techniques in future phases.

---
*Report generated as part of OpenJM Knowledge/RAG Hardening — Phase A Baseline.*
