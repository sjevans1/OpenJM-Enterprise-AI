# Acceptance Gates

A feature is not considered complete because code exists. It must pass the relevant gate.

> **VS8** adds operational acceptance (deployment profiles, production config,
> install, health/observability, backup/restore, retention, upgrade/rollback,
> hardening, load and release). See `docs/VS8_ACCEPTANCE.md` and the recorded
> evidence in `docs/VS8_ACCEPTANCE_REPORT.md`.

## Gate A — persistent conversation

1. Start a new conversation.
2. Ask: `Hello, my name is Sam.`
3. Ask: `What is my name?`
4. The answer must identify Sam from server-side conversation history.
5. Refresh the browser.
6. Open the same conversation.
7. Ask again: `What is my name?`
8. The answer must still identify Sam.

Failure at any step blocks further product phases.

## Gate B — knowledge inventory

1. Upload at least one supported document.
2. Wait for indexing to return success.
3. Ask: `What documents do you have loaded?`
4. The product must return the document catalog/metadata.
5. It must not answer that it "cannot access external documents."

## Gate C — document-grounded answer

1. Upload a document containing a unique test fact.
2. Ask a natural-language question that requires that fact.
3. The orchestrator must classify the request as KNOWLEDGE based on retrieved evidence.
4. The answer must include at least one evidence object.
5. The evidence must identify the source document and retrieved passage.
6. Remove the document from the index and repeat.
7. The assistant must no longer claim the removed document as evidence.

## Gate D — UI contract

The first production-facing shell must:

- present Chat as the primary workspace;
- include conversation history;
- include Knowledge as a business feature;
- show evidence/citations with answers;
- not expose raw MMR scores as a primary user workflow;
- not expose background Jobs as a primary navigation item;
- retain Data, Reports, Automations and Administration as the intended product information architecture.

## Gate E — no simulated success

Any endpoint that reports ingestion, retrieval, generation or execution as successful must have performed the real operation.

Stubs may exist only behind explicit test/development implementations and must be visibly named as such.


## Gate F — structured data source

1. Register the deterministic demo database.
2. Test the connection successfully.
3. Discover/refresh schema metadata.
4. Confirm the expected tables and columns are visible in Data.
5. Confirm read APIs never return the stored connection secret or decrypted URI.

## Gate G — enforced read-only SQL

1. A valid SELECT succeeds.
2. INSERT, UPDATE and DELETE are rejected before execution.
3. DDL and multi-statement SQL are rejected before execution.
4. Queries are bounded by a server-side maximum row count.
5. A requested/model LIMIT above the maximum is capped.
6. Execution timeout protection is enabled.

Any database mutation is a blocking failure.

## Gate H — structured grounded answer

1. Ask a natural-language question whose answer exists only in the acceptance database.
2. The orchestrator must return `structured`.
3. Generated SQL must use only authorized schema objects.
4. The SQL must pass server-side policy.
5. The query result must match the deterministic seeded value.
6. The assistant answer must state the correct value.
7. The response must include structured evidence containing the source identity and executed SQL.

## Gate I — source scope and hallucination resistance

1. Ask about a nonexistent or unauthorized table/column.
2. OpenJM must not execute invented SQL and must not fabricate a result.
3. Disable or remove the source.
4. OpenJM must no longer access it.
5. A structured-looking request must not fall through to GENERAL solely because every matching source is disabled; it must fail closed without database evidence or fabricated enterprise values.

Vertical Slice 2 must also keep Gates A–E green as regression coverage.

Runtime structured acceptance scripts require an isolated data-source catalog. They must fail fast when pre-existing sources are present rather than silently selecting, disabling, or deleting unrelated sources. Any acceptance source created by the script must be cleaned up on both success and failure unless an explicit keep-source diagnostic option is used.


## Slice 2 architecture seam checks

These checks support Gates F–I and must not replace them:

1. Registered tools are discoverable deterministically.
2. Unknown/unregistered tools cannot execute.
3. Required tool permissions are enforced by application code.
4. Approval-required tools cannot execute without explicit approval.
5. Structured execution produces normalized OpenJM Evidence.
6. Structured execution creates a persisted ExecutionTrace.
7. Trace records route/tool/source/policy/timing/result-bound/evidence identifiers without storing database credentials.
8. Knowledge tool resolves authorized documents server-side rather than trusting caller-supplied document IDs.
9. No autonomous agent loop or model-controlled permission/risk metadata is introduced.


## Gate J — document intelligence / RAG fidelity

1. Upload deterministic Markdown, PDF, DOCX and PPTX fixtures.
2. Verify expected document structure is represented in indexed metadata where available.
3. Verify a DOCX table-only fact is retained and retrievable.
4. Verify a PDF table value is retrievable with correct source/page context where available.
5. Verify a heading-dependent question retrieves evidence from the correct section.
6. Verify a slide-dependent question identifies the correct PPTX slide where available.
7. Verify a boundary/context question receives required neighbouring or parent context when expansion is enabled.
8. Verify evidence retains document identity, structural provenance and retrieval score.
9. Verify deletion removes all retrievable evidence.
10. Verify no cross-document or unauthorized evidence leakage.
11. Verify unrelated context does not materially increase without benchmark justification.
12. Re-run Gates A–I and keep them green.

Gate J is a quality gate. Adding a new chunking strategy without measurable improvement against deterministic fixtures does not satisfy it.


## Knowledge acceptance isolation

Document-backed runtime acceptance requires an isolated Knowledge catalog
before the test document is uploaded. If pre-existing documents are present,
`scripts/acceptance.py` fails fast and lists them; it never deletes or mutates
those documents to make the test pass.

When `--delete-after-test` is supplied, the script owns exactly one uploaded
test document. That document is deleted and verified on the success path, and
cleanup is also attempted from a `finally` block when a later Gate C/catalog/
model assertion fails. This prevents failed formal acceptance runs from
silently accumulating duplicate test documents and changing the prompt shape of
subsequent runs.

Omitting `--delete-after-test` remains an explicit diagnostic choice to retain
the test document. A later document-backed acceptance run will then fail the
isolation precondition until that retained document is removed.
