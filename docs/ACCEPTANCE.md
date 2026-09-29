# Acceptance Gates

A feature is not considered complete because code exists. It must pass the relevant gate.

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
