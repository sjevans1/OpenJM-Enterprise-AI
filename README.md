# OpenJM Enterprise AI

OpenJM Enterprise AI is a self-hostable enterprise AI platform for governed chat, persistent conversations, knowledge/RAG, structured data access, hybrid analysis, reporting, and future agent workflows.

## Build rules

This repository is being rebuilt cleanly from the previous Hermes MVP. The prior repository is reference material only; it is not an architectural dependency.

Core principles:

- OpenJM owns the product, UX, policy, audit, and orchestration contracts.
- DB-GPT is used selectively behind OpenJM-owned interfaces for RAG, data/SQL, workflows, and agent/data capabilities.
- The browser never talks directly to model providers, databases, or DB-GPT.
- Conversation state is server-side and persistent.
- Knowledge retrieval is permission-aware before evidence reaches the model.
- Structured-data access is read-only, source-scoped, validated, bounded, and audited.
- General, Knowledge, Structured Data, and Hybrid are first-class execution paths.
- No simulated success in production paths.
- Jobs/background execution are implementation details, not a primary end-user workflow.
- Every capability must have an automated acceptance test before it is treated as complete.

## Development workflow

`main` is the protected baseline. Feature work is done on branches and merged through pull requests after acceptance tests pass.

The first implementation branch is dedicated to the initial vertical slice:

**User -> Chat -> Conversation Service -> OpenJM Orchestrator -> General/Knowledge -> Evidence -> Model -> Cited Answer**

The first slice is not complete until conversation persistence and document-grounded Q&A work end-to-end.
