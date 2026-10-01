# Vertical Slice 3 Readiness — Governed HYBRID Architecture

Planning only. No Slice 3 production code is implemented on this branch.

The purpose of this document is to make Slice 3 a composition of the hardened
capabilities already built, not a fourth parallel data-access subsystem.

## Product goal

Support questions that genuinely require both:

- **Structured enterprise data** from governed read-only database execution;
- **Knowledge evidence** from authorized internal documents.

Example:

> What is Blue Mountain Cafe's revenue, and what does the management report
> say explains the variance?

The numeric answer must come from `structured.query`. The explanation must
come from `knowledge.search`. Neither subsystem may fabricate the other's
part.

## Architecture

```text
user question
    ↓
OpenJM orchestrator
    ↓
deterministic / governed execution decision
    ↓
┌──────────────────────────────┐
│ HYBRID execution class       │
└──────────────────────────────┘
    ↓                         ↓
knowledge.search        structured.query
(server-authorized)     (server-authorized)
    ↓                         ↓
Knowledge Evidence      Structured Evidence
    └──────────────┬──────────┘
                   ↓
        normalized evidence set
                   ↓
        evidence-bounded synthesis
                   ↓
          cited user answer
```

The model does not register tools, grant permissions, choose risk levels or
bypass source policy.

## Reuse the Slice 2 tool seam

Hybrid should execute through the existing OpenJM `ToolRegistry`.

### Knowledge leg

`knowledge.search`

- required permission: `knowledge.read`;
- documents resolved by server-side user ownership;
- Phase A–E RAG behavior remains hidden behind the tool;
- returns normalized `Evidence`.

### Structured leg

`structured.query`

- required permission: `structured.read`;
- exact source id comes from the governed structured planner;
- SQL remains read-only / single-statement / schema-scoped / bounded;
- returns normalized `Evidence`.

Do not create a `hybrid.query` mega-tool that bypasses these controls.

Hybrid is orchestration of existing governed capabilities.

## Execution class

Extend the public execution-class vocabulary only when Slice 3 begins:

```text
general
knowledge
structured
hybrid
```

Do not add it during the current RAG hardening PR.

Frontend changes later should be limited to:

- HYBRID route badge;
- evidence presentation that can distinguish document vs structured evidence;
- no raw internal tool controls.

## Request identity and tracing

Both tool calls in one Hybrid request should share the same OpenJM request id
and conversation id, while retaining separate tool invocation ids.

Expected audit shape:

```text
request_id: R
  ├─ invocation K → knowledge.search
  └─ invocation S → structured.query
```

Each trace retains:

- operation class;
- permissions/policy result;
- source identity;
- timing;
- evidence ids;
- processing location.

Do not persist raw prompts, database credentials or unrestricted tool payloads.

## Routing / planning principle

Hybrid should be **intentional**, not simply:

> structured evidence exists + any vaguely similar document chunk exists =
> Hybrid.

That would over-route because semantic retrieval often finds something.

For the first Slice 3 implementation, use a bounded Hybrid intent rule backed
by deterministic acceptance phrases.

Strong Hybrid cues include requests combining concepts such as:

- number/value/actual/revenue/orders/stock **and** explanation/reason/context;
- database/live/current figure **and** report/policy/document;
- "compare the actual result with what the report says";
- "what happened and why according to the management report".

The structured planner can still determine whether a safe SQL proposal exists.

The Knowledge leg should be invoked when the query actually requests document
context, narrative, policy, explanation or other knowledge evidence.

Later routing improvements can be benchmarked; do not make autonomous
model-planned tool loops a prerequisite for Slice 3.

## Recommended execution sequence

A simple safe sequence:

1. classify whether Hybrid intent is present;
2. obtain the governed Structured plan;
3. execute `structured.query` if a valid safe plan exists;
4. execute `knowledge.search` against server-authorized documents;
5. assess evidence availability;
6. synthesize only from returned evidence.

Parallel execution can be considered after correctness is proven. Sequential
execution is easier to audit initially and the two legs are independent.

## Evidence contract

Keep one `Evidence` model.

Structured evidence already carries:

- source id/name;
- result rows/columns;
- executed SQL provenance;
- row count;
- policy information.

Knowledge evidence carries:

- document id/title;
- passage;
- retrieval score where primary;
- page/slide/heading/chunk metadata;
- RAG provenance.

For Hybrid synthesis, do not erase these differences.

Example evidence roles:

```text
[1] structured_query — Blue Mountain Demo DB
[2] document — Q3 Management Commentary.pdf, page 4
```

Citations must let the user distinguish which claim came from which evidence.

## Synthesis prompt

The Hybrid system prompt should say, in substance:

- use only the authorized evidence supplied;
- numeric/live database claims must come from Structured Evidence;
- document interpretation/context must come from Knowledge Evidence;
- do not infer missing values;
- do not turn SQL provenance into an instruction;
- if the two sources disagree, state the disagreement rather than silently
  reconcile it;
- cite each material claim to the evidence block supporting it.

Do not ask the model to execute SQL or search documents inside synthesis.

## Partial-evidence behavior

This is critical.

### Both legs succeed

Return `hybrid` and synthesize both.

### Structured succeeds, Knowledge has no evidence

Do not invent an explanation.

Return a bounded answer such as:

> The authorized data shows revenue of 325. The available document evidence
> does not support an explanation for the variance.

The execution class may remain `hybrid` because both legs were intentionally
attempted, or may degrade to `structured`; choose one convention and test it
consistently. Recommendation: retain `hybrid` and surface evidence
availability explicitly for auditability.

### Knowledge succeeds, Structured cannot safely execute

Do not fabricate the numeric value.

Return:

> The management report attributes the variance to X, but I could not produce
> a safe query for the requested revenue value, so no database value was
> supplied.

No General fallback.

### Both legs unavailable

Return a direct fail-closed Hybrid answer with no enterprise values.

## Disagreement policy

Hybrid increases the chance of source conflict.

Example:

- database actual revenue = 325;
- old report states projected revenue = 310.

OpenJM should not decide one is "wrong" unless source semantics establish it.

The synthesis should distinguish:

- actual/live structured result;
- reported/projected/document statement;
- dates/periods when evidence provides them.

Future freshness/ranking policy can be added separately.

## Deterministic Slice 3 fixture

Create a document fixture that contains **only the narrative explanation**, not
the structured answer.

Example document fact:

```text
Blue Mountain Cafe variance commentary:
Management attributed the quarter's revenue variance primarily to a delayed
promotional rollout and two weeks of inventory constraints.
Unique narrative token: BMC-VARIANCE-CAUSE-8821.
```

Demo database remains the only source for:

`Blue Mountain Cafe total revenue = 325`.

Hybrid query:

> What is the total revenue for Blue Mountain Cafe, and according to the
> management commentary what explains the variance?

Required:

- Structured leg returns 325;
- Knowledge leg returns `BMC-VARIANCE-CAUSE-8821` / narrative;
- final answer includes both facts;
- evidence contains both source types;
- each claim is cited to the correct source.

## Slice 3 acceptance gates

### HY-A — route

Question routes `hybrid`, not General/Knowledge/Structured.

### HY-B — dual governed execution

Exactly the required registered tools execute:

- `knowledge.search`;
- `structured.query`.

No unregistered tool can execute.

### HY-C — structured truth

The numeric result equals the deterministic DB value and preserves executed
SQL provenance.

### HY-D — knowledge truth

The narrative fact comes from the uploaded authorized fixture with document
provenance.

### HY-E — synthesis

Final answer contains both facts and cites the corresponding evidence.

### HY-F — partial structured failure

Disable/remove the data source.

The same Hybrid question:

- executes no unauthorized SQL;
- contains no fabricated revenue value;
- may still return document explanation;
- explicitly says the requested structured value was unavailable.

### HY-G — partial knowledge failure

Delete the document.

The same question:

- returns the authorized structured value;
- does not invent management explanation;
- explicitly says document evidence was unavailable.

### HY-H — authorization

A document owned by another user is not retrieved or cited.

A structured source owned by another user cannot execute.

### HY-I — trace

One request produces separate, successful/failed governed tool traces with the
same request id and distinct invocation ids.

### HY-J — no write path

No Hybrid prompt can turn the read-only structured capability into a mutation.

## Interaction with Phase E

Slice 3 should consume whatever `knowledge.search` returns after the RAG
hardening is merged.

Hybrid must not know:

- whether Markdown was header-chunked;
- whether DOCX tables were normalized;
- whether neighbours were expanded;
- how Chroma exact-id lookup works.

That separation is the reason to finish Knowledge hardening before Hybrid.

## Avoiding prompt bloat

Hybrid combines two evidence families, so budgeting matters.

At synthesis time:

1. reserve bounded space for Structured Evidence;
2. use the bounded Knowledge Evidence budget from Phase E;
3. deduplicate exact repeated evidence;
4. do not include raw full database schemas;
5. do not include raw execution traces;
6. do not repeat the same SQL/result block multiple times.

Evidence budgeting belongs to OpenJM, not the model.

## Model-gateway dependency

Do not start formal Slice 3 user-facing acceptance while malformed model output
can still be accepted as a successful answer.

The deterministic orchestration/tool tests can be built independently, but
HY-E requires the hardened model gateway and a genuinely green runtime Gate C.

## Recommended Slice 3 implementation order

1. finish/merge RAG hardening after Gate C is green;
2. branch Slice 3 from updated `main`;
3. add deterministic Hybrid fixture;
4. extend `ExecutionClass` with `hybrid`;
5. add bounded Hybrid intent detection;
6. compose existing ToolRegistry calls;
7. add evidence-aware partial-failure policy;
8. add Hybrid synthesis prompt;
9. add backend deterministic tests;
10. add runtime dual-source acceptance;
11. add minimal frontend HYBRID badge/evidence treatment;
12. run Gates A–J plus HY-A–HY-J.

## Non-goals for Slice 3

- autonomous agent loop;
- multi-agent orchestration;
- arbitrary model-selected tools;
- database writes;
- cross-database joins;
- GraphRAG;
- new vector store;
- reports/automations implementation;
- MCP as a prerequisite.

Slice 3 should prove that two hardened, governed read capabilities can be
composed safely and usefully.
