# OpenJM Enterprise AI — Architecture v1

## Product contract

OpenJM is the product. DB-GPT, model runtimes, vector stores and database libraries are replaceable infrastructure behind OpenJM-owned interfaces.

The browser communicates only with the OpenJM API.

```text
┌────────────────────────────────────────────────────────────┐
│                    OpenJM Web Application                  │
│ Chat • Knowledge • Data • Reports • Automations • Admin   │
└──────────────────────────┬─────────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────────┐
│                     OpenJM Control Plane                   │
│ FastAPI • identity • policy • audit • conversations       │
└──────────────────────────┬─────────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────────┐
│                  OpenJM Orchestration Layer                │
│  GENERAL │ KNOWLEDGE │ STRUCTURED DATA │ HYBRID           │
└──────────────┬───────────────┬─────────────────────────────┘
               │               │
               ▼               ▼
┌────────────────────────┐   ┌───────────────────────────────┐
│ Knowledge Engine       │   │ Structured Data Gateway       │
│ DB-GPT RAG components  │   │ read-only connectors / SQL    │
│ parsing/retrieval      │   │ policy / semantic metadata    │
└────────────┬───────────┘   └──────────────┬────────────────┘
             │                              │
             ▼                              ▼
┌────────────────────────┐   ┌───────────────────────────────┐
│ Governed evidence      │   │ Customer data sources         │
│ docs/chunks/citations  │   │ DB / ERP / CRM / APIs         │
└────────────┬───────────┘   └───────────────────────────────┘
             │
             └──────────────────────┐
                                    ▼
                    ┌────────────────────────────┐
                    │ Model Abstraction          │
                    │ Hermes / Ollama / vLLM     │
                    │ OpenAI-compatible endpoint │
                    └────────────────────────────┘
```

## Vertical Slice 1

The first build implements only the path necessary to prove the foundation:

```text
User
  ↓
Chat
  ↓
Conversation Service ───────────────┐
  ↓                                 │ persistent server-side history
OpenJM Orchestrator                 │
  ├── GENERAL ──────────────────────┤
  └── KNOWLEDGE                     │
        ↓                           │
     DB-GPT RAG                     │
        ↓                           │
     Evidence + citations           │
        └──────────────┐            │
                       ↓            │
                  Model Gateway     │
                       ↓            │
               Persisted answer ◄───┘
```

### Deliberately not in Vertical Slice 1

- Natural-language SQL execution
- Hybrid document + database answers
- Report generation
- Agent actions
- Background-job UI
- Shared multi-tenancy
- Enterprise SSO

Those are added only after chat persistence and document-grounded Q&A pass acceptance.

## DB-GPT boundary

DB-GPT is not exposed as the OpenJM product UI.

For the first slice OpenJM uses DB-GPT's current RAG building blocks directly:

- `KnowledgeFactory` for document loading.
- `EmbeddingAssembler` for chunking/index persistence.
- `ChromaStore` as the initial local vector store.
- `EmbeddingRetriever` for retrieval.

The knowledge implementation is behind `KnowledgeEngine`, so it can later move to DB-GPT knowledge spaces, a dedicated DB-GPT service, pgvector, Milvus or another engine without changing the Chat contract.

## Conversation rules

1. The server owns conversation IDs.
2. Every user and assistant message is persisted.
3. The client never reconstructs model context from browser-only state.
4. Reloading the browser must reload the conversation from the server.
5. The model receives bounded server-side history.
6. Evidence used for an answer is returned with the answer.

## Knowledge rules

1. Upload is a product operation, not a debug-vector-search screen.
2. OpenJM stores document metadata separately from the vector index.
3. A deterministic document-catalog request lists authorized document metadata; it is not implemented as semantic retrieval.
4. RAG evidence is retrieved before model generation.
5. If no evidence clears the retrieval threshold, the request stays GENERAL.
6. Evidence metadata is attached to the assistant response.

## Vertical Slice 2 — governed structured data

Vertical Slice 2 adds the third execution class:

```text
User
  ↓
Chat
  ↓
OpenJM Orchestrator
  ↓
STRUCTURED
  ↓
Authorized source + schema context
  ↓
SQL planner
  ↓
SQLGlot policy / rewrite
  ↓
Bounded read-only execution
  ↓
Structured evidence
  ↓
Model synthesis
```

The Structured Data Gateway owns source registration, credential protection, schema discovery, query policy, bounded execution and provenance. The browser never talks directly to a database.

Initial adapters are SQLite and PostgreSQL. SQL must be validated independently of the model, restricted to one read-only statement, scoped to authorized schema objects, limited in row count and bounded by an execution timeout.

See `docs/VERTICAL_SLICE_2.md` for the complete scope and acceptance contract.

## Next architecture phases

After Vertical Slice 2 passes:

1. Four-way routing with HYBRID document + structured-data evidence.
2. Saved reports created from repeatable evidence plans.
3. Enterprise identity and permission-aware retrieval/data access.
4. Automations and bounded agent workflows.
