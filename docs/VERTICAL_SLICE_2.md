# Vertical Slice 2 — Governed Structured Data

## Objective

Add the third OpenJM execution path:

```text
User
  ↓
OpenJM Chat
  ↓
Conversation Service
  ↓
OpenJM Orchestrator
  ↓
STRUCTURED DATA
  ↓
Authorized schema context
  ↓
SQL planning
  ↓
SQLGlot policy / rewrite
  ↓
Read-only execution
  ↓
Structured evidence
  ↓
Model synthesis
  ↓
Persisted answer
```

The slice is complete only when a user can connect an authorized relational data source, ask a natural-language business question, receive a correct answer grounded in live query results, inspect the evidence/query, and prove that write operations are blocked.

Hybrid document + database answers are explicitly deferred to Vertical Slice 3.

## Product contract

OpenJM owns:

- source registration and authorization;
- credential protection;
- schema discovery and source scope;
- routing and planning contracts;
- SQL policy and safety enforcement;
- query execution limits;
- audit/evidence metadata;
- user-facing Data and Chat experiences.

SQLAlchemy, SQLGlot, database drivers, DB-GPT capabilities and model runtimes remain replaceable infrastructure behind OpenJM-owned interfaces.

The browser never connects directly to a customer database.

## Supported sources for this slice

Initial runtime support:

1. SQLite — local/demo acceptance and lightweight installations.
2. PostgreSQL — production-oriented relational source path.

Additional database engines are later adapters, not reasons to weaken the source interface.

## Data source model

A registered source should contain non-secret metadata such as:

- source ID;
- display name;
- engine/dialect;
- connection status;
- enabled/disabled state;
- authorized schemas/tables;
- last schema refresh;
- created/updated timestamps.

Connection credentials/URIs must never be returned by read APIs or exposed in chat evidence.

For this slice, credentials should be encrypted at rest by the backend using a server-owned application secret. The API may accept credentials during source creation/update, but subsequent GET responses return only safe metadata.

## Data page

Enable the existing **Data** navigation item as a real workflow.

Minimum product operations:

- list configured sources;
- add a source;
- test connection;
- inspect discovered schemas/tables/columns;
- refresh schema metadata;
- enable/disable a source;
- delete a source.

Do not expose raw framework/debug screens.

## Structured execution pipeline

### 1. Source discovery

The orchestrator receives only sources authorized for the current OpenJM user/workspace.

### 2. Schema context

The structured gateway supplies bounded schema metadata to the planner:

- source name;
- schema/table names;
- columns and types;
- primary keys;
- foreign-key relationships where available.

The model must not invent tables or columns outside this context.

### 3. SQL planning

The planner produces a machine-readable plan containing at minimum:

- selected source ID;
- SQL;
- short planning rationale.

The source ID is validated independently of the model.

### 4. SQL policy

Every generated query must pass a server-side SQLGlot policy layer before execution.

Required rules:

- exactly one SQL statement;
- SELECT/CTE query only;
- reject INSERT, UPDATE, DELETE, MERGE, CREATE, ALTER, DROP, TRUNCATE, GRANT, REVOKE, COPY, CALL, DO and equivalent mutations;
- reject multi-statement payloads;
- reject access to tables/schemas outside the source authorization scope;
- reject unknown tables/columns where validation can determine them;
- deny dangerous or intentionally blocking functions where applicable;
- enforce a server-side maximum row count;
- add a LIMIT when one is absent;
- cap a user/model LIMIT above the configured maximum;
- enforce an execution timeout;
- preserve the source dialect during parse/rewrite.

A database account should also be read-only where the engine supports it. SQL policy is defense in depth, not a replacement for least-privilege credentials.

### 5. Execution

Execute only the validated/re-written SQL against the selected source.

Return a bounded structured result:

- columns;
- rows;
- row count;
- truncated flag;
- elapsed time;
- executed SQL;
- source ID/name.

### 6. Evidence

Structured query results become OpenJM evidence before answer synthesis.

Suggested evidence contract:

```text
source_type: structured_query
source_id: <data-source-id>
title: <source display name>
passage: <bounded human-readable result>
metadata:
  sql: <executed validated SQL>
  columns: [...]
  row_count: N
  truncated: true|false
  elapsed_ms: N
```

The model answers from that evidence and must not claim database facts not supported by the returned result.

## Routing

After this slice the orchestrator supports:

- GENERAL
- KNOWLEDGE
- STRUCTURED

HYBRID remains disabled until Vertical Slice 3.

A structured request should route only when an authorized source/schema can plausibly support the question. Failure to produce a safe, valid query must result in an explicit safe failure/clarification, not silent fallback to fabricated data.

## Audit expectations

For every structured execution, preserve enough information to reconstruct:

- user/workspace;
- conversation/message;
- source ID;
- planned SQL;
- executed SQL;
- policy decision;
- execution time;
- row count;
- success/failure;
- timestamp.

Do not log database passwords, bearer keys or decrypted connection URIs.

A dedicated enterprise audit subsystem can come later, but this slice should establish the execution record contract.

## Acceptance database

Create a deterministic demo relational database for automated and manual testing.

It should include a small business-style schema such as:

- customers;
- products;
- orders;
- order_items.

Seed it with fixed values that support unambiguous acceptance questions, for example:

- total revenue for a named customer;
- highest-selling product;
- order count for a fixed period.

The expected answers must be computable directly from the seeded rows.

## Acceptance gates

### Gate F — source registration and schema discovery

1. Register the demo database through the OpenJM source API/UI.
2. Test connection successfully.
3. Refresh/discover schema.
4. Data page shows expected tables and columns.
5. GET/list APIs do not return the stored connection secret/URI.

### Gate G — enforced read-only execution

1. A valid SELECT succeeds.
2. INSERT is rejected before execution.
3. UPDATE is rejected before execution.
4. DELETE is rejected before execution.
5. DDL is rejected before execution.
6. Multi-statement SQL is rejected.
7. A query without LIMIT is bounded by the server.
8. A LIMIT above the configured maximum is capped.
9. Timeout protection is configured and tested where practical.

Any mutation reaching the target database is a blocking failure.

### Gate H — structured natural-language answer

1. Ask a question whose answer exists only in the demo database.
2. Orchestrator returns execution_class = `structured`.
3. Generated SQL references only authorized schema objects.
4. SQL passes the policy layer.
5. Query result matches the seeded expected value.
6. Assistant answer contains the correct value.
7. Response includes structured evidence with source identity and executed SQL.

### Gate I — source scope / hallucination resistance

1. Ask about a table/column not present in the authorized schema.
2. OpenJM must not execute invented SQL against an unknown object and must not fabricate an answer.
3. Ask for data from a disabled or unregistered source.
4. OpenJM must not access it.

### Regression gate

All Vertical Slice 1 acceptance must remain green:

- persistent conversation memory;
- document inventory;
- grounded knowledge answer;
- deletion/no stale evidence.

## UI requirements

### Chat

- support a `structured` execution badge;
- display structured evidence without presenting raw internal debug output;
- allow the user to expand evidence and inspect the executed SQL/result provenance.

### Data

- becomes enabled in primary navigation;
- presents business-friendly data source cards/table;
- supports connect/test/refresh/delete;
- shows schema inventory in a readable way;
- never shows saved passwords/credentials after submission.

## Deliberately deferred

Not part of Vertical Slice 2:

- Hybrid document + SQL synthesis;
- cross-database joins;
- writes/transactions/actions;
- stored procedures;
- arbitrary code execution;
- report scheduling;
- agents;
- multi-user enterprise RBAC/SSO;
- broad connector marketplace;
- semantic metrics layer beyond the schema metadata needed for safe SQL generation.

## Implementation sequence

1. Create source models/migrations and encrypted credential handling.
2. Build OpenJM structured-data adapter interfaces.
3. Add SQLite and PostgreSQL source connectors.
4. Add schema introspection.
5. Add SQLGlot validation/rewrite policy.
6. Add bounded read-only executor.
7. Add structured planner using the existing model gateway.
8. Extend orchestrator and schemas with `structured`.
9. Persist structured evidence/audit metadata.
10. Enable Data UI.
11. Render structured evidence in Chat.
12. Add deterministic demo database.
13. Add unit tests for SQL policy/source scope.
14. Add runtime acceptance for Gates F–I.
15. Re-run all Vertical Slice 1 regression checks.

## Definition of done

Vertical Slice 2 is complete only when all of the following are true:

- a real database source can be configured through OpenJM;
- OpenJM discovers its schema;
- natural-language questions produce safe read-only SQL;
- server-side policy rejects mutations regardless of model output;
- results are bounded and evidenced;
- Chat answers are grounded in the actual query result;
- source credentials remain secret;
- Data is a real product workflow;
- regression tests for Vertical Slice 1 remain green;
- automated acceptance proves the structured path end to end.


## Foundation implementation status

The first backend foundation is now implemented on the Vertical Slice 2 branch:

- `DataSource` persistence model;
- Fernet-encrypted connection credentials with a server-owned key;
- SQLite/PostgreSQL connection normalization;
- connection testing;
- schema/table/column/PK/FK discovery;
- safe source APIs that never return saved connection credentials;
- SQLGlot single-statement/read-only/source-scope policy;
- server-side row limits;
- dangerous-function deny list;
- bounded structured query executor;
- database-session read-only mode (SQLite `PRAGMA query_only`, PostgreSQL `SET TRANSACTION READ ONLY`);
- deterministic SQLite acceptance database;
- foundation runtime smoke script;
- unit coverage for encryption, discovery, SQL policy and execution.

With the backend running, create and validate the deterministic source with:

```bash
python scripts/structured_foundation.py
```

Use `--keep-source` to retain and re-enable the demo source for the upcoming structured Chat/planner work.

The next implementation step is the structured planner/orchestrator path and structured evidence contract, followed by the Data UI.
