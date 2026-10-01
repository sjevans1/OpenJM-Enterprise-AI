# Model Gateway Reliability — Gate C Investigation and Fix

This document records the Phase B model-gateway reliability checkpoint:
the raw-response evidence, root-cause analysis, the controlled matrix, the
validation layer, the bounded-retry policy, the history-integrity defect
found along the way, and the formal Gate C result. No secrets are included.

## Environment under test

- Serving runtime: Windows-side `llama-server` (llama.cpp, build
  b10278-d52ec04a6), CPU profile (`--n-gpu-layers 0`, `--threads 8`),
  `--ctx-size 32768`, `--parallel 1`, `--timeout 600`, `--reasoning off`,
  loopback-only, API-key guarded.
- Model: `gemma-4-12b-it-qat-q4_0.gguf` served under alias
  `gemma-4-12b-local`; OpenJM consumes the OpenAI-compatible
  `/v1/chat/completions` endpoint.
- Chat template: the server's embedded "Google Gemma 4 Canonical Chat
  Template". Relevant behaviors: `<|turn>`/`<turn|>` turn markers,
  `<|channel>thought ... <channel|>` thinking channel, tool-call blocks,
  and a non-thinking mode that injects a CLOSED EMPTY thought channel at
  generation start when thinking is disabled.
- Server startup log warnings (verbatim meaning, no secrets): the GGUF's
  `<|tool_response|>` token "was not control-type; probably a bug in the
  model" and `</s>` was removed from the EOG list. This build's tokenizer
  metadata is imperfect, which matters for malformed-output analysis.

## Raw response behavior (reproduced outside the application)

All captured against the live server with the exact messages OpenJM sends
(HTTP status, response JSON shape, choices[0].message fields, content,
finish_reason, usage). API keys excluded.

- Malformed responses are HTTP 200 with a correct OpenAI shape:
  `finish_reason=stop` (or `length` when max_tokens bounds a garbage run),
  `choices[0].message` = `{role, content}`, and content consisting
  entirely of tokenizer control tokens, e.g.
  `<unused50><unused16>...<|tool_call>...<|"|>`.
- The garbage is IN the model's content itself: not a chat-template
  rendering artifact (simple, general, and structured prompts render and
  generate fine through the same template), not response parsing
  (the JSON shape is valid), and not a model/tokenizer/template version
  mismatch in the request path (the server accepts and renders every
  prompt correctly).
- Prompt-shape dependence was established empirically (see matrix):
  simple prompts (16 prompt tokens), general conversation (~100 tokens)
  and the structured planner/answer prompts (~190 tokens) never
  malformed; knowledge evidence-synthesis prompts did.

## Root cause (established at two levels)

1. Immediate trigger — duplicate/overlapping evidence. A controlled
   matrix (below) shows single-document evidence produced valid answers
   9/9 while the dev store's four semantically identical Phoenix
   documents (3x .md uploads left by failed acceptance runs + 1x .txt)
   produced malformed output 6/6 across every sampling shape, including
   the thinking-enabled retry (reasoning never engaged; the model
   emitted `<unused32>` runs until the token budget was exhausted).
   The dominant trigger is therefore the duplicated evidence content in
   the prompt, not history, not sampling, and not the thinking setting.

2. Model-side mechanism — empty thought channel. The Gemma 4 template's
   non-thinking path injects a closed, EMPTY thought channel before
   generation. This thinking-trained QAT-Q4 build is out-of-distribution
   generating immediately after an empty thought block; under
   high-redundancy evidence prompts its distribution collapses into the
   `<unusedNN>` vocabulary band. Enabling the template's thinking channel
   per request (`chat_template_kwargs: {enable_thinking: true}`)
   produces clean, separated reasoning + answer on single-document
   evidence prompts (6/6 direct probes), but cannot rescue the
   duplicate-evidence degenerate state.

An unexplained component remains: the byte-identical single-document
Gate C prompt failed 8/8 in one probe session and passed 9/9 in another.
The message arrays were verified identical; the difference is server-side
state (single-slot KV cache history across back-to-back probe requests,
including requests aborted mid-generation by client timeouts). Classification:
the model failure is content-triggered with an intermittent server-state
contribution; the practical fix is below.

## Controlled matrix (message arrays built synthetically; no orphans)

Conditions: E1 = single phoenix .md document evidence (isolated store);
EDUP = duplicate phoenix evidence (dev store: 3x .md + 1x .txt).
History pairs = 0/1/3 user+assistant turns. NORMAL = temp 0.2 no
max_tokens (production first attempt); RETRY = temp 0.0, max_tokens 512,
enable_thinking (the bounded-retry shape).

| Cell | n_msgs | prompt_tok | completion_tok | finish | out_len | density | reasoning | valid |
|------|--------|-----------|----------------|--------|---------|---------|-----------|-------|
| E1 x {0,1,3 pairs} x normal | 2-8 | 395-741 | 23 | stop | 70 | 0.00 | absent | 3/3 valid, answer present |
| E1 x {0,1,3 pairs} x retry | 2-8 | 393-739 | 116-290 | stop | 70 | 0.00 | present (371-900 chars) | 3/3 valid, answer present |
| E1 repeat stability x3 (normal / t0+512 / t0+512+think) | 2 | 393-395 | 23-290 | stop | 70 | 0.00 | per shape | 9/9 valid |
| EDUP x {0,1,3 pairs} x normal | 2-8 | ~700 | 5-29 | stop | 30-268 | 0.96-1.00 | absent | 0/3 malformed |
| EDUP x {0,1,3 pairs} x retry | 2-8 | ~700 | 512 | length | 5120 | 1.00 | absent | 0/3 malformed |

(An earlier EDUP probe pass was discarded as invalid: a probe bug mutated
the process-global cached Settings so the duplicate condition retrieved
from the wrong store, returning zero evidence. The corrected fresh-process
run is the data above.)

Conclusions from the matrix:

- history pairs: exonerated (E1 valid with 0/1/3 pairs; EDUP malformed
  with 0/1/3 pairs);
- sampling shape and thinking toggle: exonerated as sufficient rescues
  for the duplicate-evidence state;
- duplicate evidence: the dominant trigger.

## History-integrity defect (found during the investigation)

The chat API committed the user Message BEFORE model generation. A failed
model call therefore left an orphan user turn; any retry through the same
conversation then produced consecutive user turns, a degenerate template
state that no longer reproduced the original request. Fixed in the same
hardening branch: the user turn, title change and assistant answer now
commit together after generation succeeds; `ModelGatewayError` rolls the
whole turn back. A failed first turn persists nothing. Malformed output
is consequently never persisted as an assistant message.

## Validation rules (gateway defense-in-depth)

`validate_model_output` classifies raw content with proportion /
repetition / structure rules over tokenizer control-token families:

- families matched: `<unusedN>`, full pipe-delimited control tokens
  (`<|...|>`), half-pipe turn/channel/tool markers (`<|tool_call>`,
  `<turn|>`, `<channel|>`, `<tool_response|>`, ...), ``,
  `<bos>`/`<eos>`/`<start_of_turn>`/`<end_of_turn>`.
  Plain HTML (`<div>`), math (`x < 10`), and prose quoting a token-like
  string do not trip the rules.
- invalid if: empty/whitespace-only; control-token density above 0.34 of
  total characters; any single control token repeated 8+ times; the
  answer opens with a run of 3+ control tokens.
- no token stripping is ever performed: malformed generation is never
  laundered into an apparently-valid answer.

## Bounded retry policy

```
first generation (caller settings: temp 0.2, unbounded tokens)
        ↓ validate
valid → return
invalid
        ↓ ONE retry: temp 0.0, max_tokens 512 (caller's bound kept when smaller),
        and configurable request extras (default: enable_thinking)
        ↓ validate
valid → return
invalid → ModelGatewayError → API returns 502, nothing persisted
```

- exactly one retry; no unlimited retries; no retry-until-green loops;
- retry request extras are deployment-configurable
  (`OPENJM_MODEL_RETRY_REQUEST_EXTRAS`, default enables the Gemma 4
  template thinking channel; set `{}` to disable for other runtimes);
- the caller's max_tokens bound is preserved (planner uses 700);
- if the endpoint rejects the extras (strict non-llama.cpp server), the
  retry fails closed into the same controlled error path.

## Controlled-failure behavior

- `chat.py` returns HTTP 502 with a reason-only detail (no raw model
  content, no prompts, no secrets) and persists nothing (see
  history-integrity fix).
- raw model response bodies are never logged by the gateway; the only
  body handling is in-process extraction of the content field.

## Server/template configuration changes

None. The llama-server startup flags are unchanged (reasoning remains
server-side `off`; the retry's thinking enablement is per-request via
`chat_template_kwargs`, which llama-server applies through the template's
`enable_thinking` variable). Root cause at the environment level was
resolved by removing the duplicate test documents from the dev knowledge
store (they were leftover artifacts of failed acceptance runs, not user
content); evidence-side deduplication remains Phase E scope and is
explicitly not implemented here.

## Formal Gate C result

PASS (runtime acceptance, scripts/acceptance.py):

```
PASS health
PASS conversation created
PASS server-side conversation memory: Your name is Sam.
PASS persisted history: 4 messages
PASS document indexed: phoenix_launch_protocol.md
PASS document catalog
PASS document-grounded answer: The PRIMARY launch sequence code for Project Phoenix is 7-3-9-2-5 [1].
PASS grounded evidence count: 5
PASS document deleted
PASS deleted document absent from catalog
PASS deleted document no longer used as evidence
ALL REQUESTED CHECKS PASSED
```

Live scenario validation against the same running server: General memory
("Your name is Sam.") clean; Knowledge answer contains 7-3-9-2-5 with
evidence (heading_path present, no storage-name/path leak); Structured
grounded revenue answer 325.0 [1] with SQL provenance.

## Regression at this checkpoint

- backend pytest: 104/104 (20 gateway tests + 4 history-integrity tests
  added; all deterministic, no Gemma required);
- deterministic RAG benchmark A-H + Phoenix retrieval: all PASS;
- structured foundation acceptance: PASS;
- structured chat acceptance: PASS;
- Phase D metadata suite and upload/source-name integration tests: PASS.

## Remaining limitations

1. Duplicate/overlapping evidence can still degenerate this model build;
   the gateway now fails closed (502, nothing persisted) instead of
   returning garbage, but a good answer under duplicate evidence needs
   evidence-level deduplication (Phase E scope, not implemented).
2. The intermittent server-state component (byte-identical prompt failing
   in one session, passing in another) is not fully explained; suspected
   single-slot KV-cache state across aborted/back-to-back requests. The
   validation layer contains it.
3. `--reasoning off` remains the server default; OpenJM's retry enables
   thinking per request only. A server-side reasoning-profile change is a
   deployment decision outside this work package.
4. The GGUF's mistyped control tokens (per llama.cpp load warnings) are
   an upstream model-build issue; not fixable from OpenJM.
