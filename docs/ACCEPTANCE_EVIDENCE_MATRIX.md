# OpenJM Acceptance Evidence Matrix

Planning-only quality matrix for PR #3 and the transition into Vertical Slice 3.

This matrix intentionally separates:

- deterministic product behavior;
- retrieval/evidence behavior;
- runtime model-generation behavior;
- acceptance-harness integrity.

A green retrieval check must never be used to imply a green end-user answer
check.

## Evidence classes

### D — deterministic

No live LLM required. Suitable as the strongest regression authority for code
contracts.

Examples:
- unit tests;
- policy selection;
- SQL validation;
- document extraction;
- metadata enrichment;
- exact chunk retrieval;
- permission checks.

### R — retrieval/runtime infrastructure

Uses real DB-GPT/Chroma/application runtime but does not depend on answer
generation being semantically correct.

Examples:
- ingest a document;
- retrieve expected evidence;
- verify source/page/slide/heading metadata;
- delete and verify no stale evidence.

### M — model-runtime

Requires a live model endpoint and validates actual user-facing generated
output.

Examples:
- conversation recall;
- grounded Phoenix answer;
- structured 325 answer synthesis.

### H — harness integrity

Proves the acceptance script itself is isolated, repeatable and cleans up its
own test artifacts.

## Current matrix

| Gate / capability | Expected evidence class | Current state at reviewed Phase D | Merge requirement |
|---|---|---|---|
| Gate A conversation memory | M + H | PASS; failed-turn history integrity fixed | GREEN |
| Gate B Knowledge catalog | R + H | PASS | GREEN |
| Gate C document answer | R + M + H | PASS end-to-end on designated final run; harness isolation still needs hardening | GREEN all legs |
| Gate D UI contract | deterministic/build/manual UI | prior Slice 1/2 acceptance | GREEN / no regression |
| Gate E no simulated success | D + R | PASS architecture; model malformed output currently demonstrates why gateway validation matters | GREEN |
| Gate F structured source | D + R + H | PASS | GREEN |
| Gate G read-only SQL | D + R | PASS | GREEN |
| Gate H structured grounded answer | D + M + H | PASS in latest reported run | GREEN |
| Gate I scope / hallucination resistance | D + R + M | PASS | GREEN |
| Gate J RAG fidelity | D + R | Phases A–D largely PASS; Phase E pending | GREEN before hardening merge |
| DOCX table-only 1,425 | D + R | PASS | GREEN |
| Markdown header fidelity | D + R | PASS | GREEN |
| PDF page provenance | D + R | PASS | GREEN |
| PPTX slide provenance | D + R | PASS | GREEN |
| .htm compatibility | D + R | PASS at Phase D | GREEN |
| filesystem-path redaction | D + R | PASS; original filename and storage-name redaction verified | GREEN |
| cross-user Knowledge isolation | D integration | architecture server-scoped; stronger test pending | GREEN test |
| document deletion | D + R | existing deletion tests + runtime behavior pass; Phase E should add exact-neighbour deletion test | GREEN |
| structured acceptance isolation | H | PASS | GREEN |
| Knowledge acceptance isolation | H | currently weak; stale Phoenix uploads accumulated after failures | GREEN |
| failed model-call history integrity | D + M | PASS; failed generation rolls back staged user turn | GREEN |
| malformed model-output validation | D + M | PASS; special-token garbage rejected with one bounded retry then fail-closed | GREEN |
| Phase E adjacency expansion | D + R | not started | GREEN only if promoted |
| Phase E parent expansion | D + R | optional, no trusted parent id yet | NOT REQUIRED unless implemented |
| Hybrid knowledge + SQL | D + R + M | Slice 3, not part of PR #3 | future Slice 3 gate |

## Gate C decomposition

Formal Gate C should be treated as five separate assertions:

```text
C1 upload/index succeeds
C2 correct source evidence is retrieved
C3 evidence reaches answer synthesis
C4 generated answer contains grounded fact
C5 delete removes source and future evidence
```

At reviewed hardening HEAD `428209e918be840ccd02f772ea1a39ad5fb82a40`:

- C1 GREEN
- C2 GREEN
- C3 reported GREEN
- C4 GREEN on the designated final runtime acceptance
- C5 GREEN when the script reaches cleanup

The designated final Gate C run is **GREEN**. Harness isolation/cleanup remains a separate H-class requirement before merge.

Do not average or majority-vote subchecks.

## Knowledge acceptance-harness contract

A formal Knowledge acceptance run should be repeatable from the same initial
state.

Required harness properties:

1. test document identity is unique to the run;
2. pre-existing user documents are not deleted;
3. the run cannot silently accumulate duplicate Phoenix fixtures after a
   normal error;
4. cleanup is attempted from `finally` for artifacts owned by the run;
5. an explicit `--keep-document` diagnostic mode may retain the test source;
6. the evidence assertion identifies the source created by the current run;
7. clean reruns do not change prompt shape merely because prior acceptance
   failed.

Best implementation is an isolated test DB/vector store. Fail-fast isolation is
the next-best option when that is impractical.

## Model-gateway acceptance contract

A live model response is successful only if:

- HTTP/response shape is valid;
- content is non-empty;
- content passes malformed-control-token validation;
- bounded retry/recovery rules, if configured, have not been exhausted;
- the generated answer satisfies the domain-specific acceptance assertion.

Malformed output is not a partial pass.

If generation fails:

- return a controlled failure;
- do not save malformed assistant text as successful history;
- do not silently retry indefinitely;
- do not mutate subsequent prompt history in a way that makes reruns
  incomparable.

## Phase E acceptance contract

Phase E should have separate promotion and regression checks.

### Promotion

The hard adjacency fixture must show:

```text
baseline evidence → missing required qualifier
±1 expansion       → required qualifier present
                     unrelated N+2 absent
```

### Security

- neighbour from another document collection cannot be fetched;
- arbitrary caller/model ids cannot trigger lookup;
- metadata document mismatch is rejected;
- deleted collection cannot be expanded.

### Budget

Report:

- primary count;
- candidate neighbour count;
- admitted neighbour count;
- deduplicated count;
- context characters/tokens;
- added latency.

### Regression

All Phase A–D facts remain green.

## Slice 3 Hybrid acceptance proposal

When Hybrid starts, do not collapse Knowledge and Structured correctness into
one opaque answer check.

Proposed Hybrid gate:

```text
H1 route selects HYBRID for a deterministic cross-source question
H2 structured.query returns the expected governed value
H3 knowledge.search returns the expected narrative evidence
H4 both evidence types retain independent provenance
H5 synthesis uses both sources and cites them distinctly
H6 disabling/removing either source yields an explicit partial/insufficient
   result rather than fabricated completion
H7 no write or unregistered tool path is introduced
H8 ExecutionTrace records both governed tool invocations
```

## Merge-readiness rule

PR #3 may move from draft only when every **required** item in this matrix is
green on one documented final validation state.

Known model non-determinism may be reported, but cannot be converted into a
green formal user-facing acceptance by retrying until one run happens to pass.

The final report should show both:

- deterministic suite result;
- exact live acceptance result from the designated final run.
