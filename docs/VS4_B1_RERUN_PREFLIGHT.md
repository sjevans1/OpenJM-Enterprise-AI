# VS4-B1 — Governed report rerun preflight

## Status

The first VS4-B increment implements an **explicit, read-only rerun preparation step**.
It does **not** implement scheduled jobs, background reruns, reusable SQL templates,
automatic model execution, or automatic revision of immutable saved report snapshots.

Base: VS4-A merged (PR #14) and efficient GitHub Actions CI merged (PR #16).

## Product contract

1. A user opens an available historical Saved Report.
2. The user clicks **Prepare rerun in Chat**.
3. The backend resolves the report **under the current owner** and checks the
   saved Evidence's currently authorized sources, structured tables, equivalent
   document references and grounded policy references, as in VS4-A.
4. Only if the historical assistant transcript still matches the immutable
   report and has a uniquely adjacent original user question with the matching
   explicitly chosen Knowledge/Data/Hybrid mode does it return the question.
5. The UI clears any previously loaded report, opens a **new** empty Chat
   conversation and fills the original question and exact mode into its composer.
   An unmistakable note explains that the user must review and press **Send**.
6. **No tool or model executes during preflight.** If the user explicitly
   presses Send, the existing Chat endpoint routes through current authorization,
   current SQL planning, evidence retrieval and dependent Hybrid policy/currency
   checks. The historical saved report remains unchanged.

### API

`GET /api/reports/{report_id}/rerun-preview`

Success JSON:

```json
{
  "report_id": "uuid",
  "source_message_id": "uuid",
  "original_question": "Which customers exceed the approved FY2025 threshold?",
  "mode": "hybrid",
  "snapshot_as_of": "2026-10-02T10:00:00Z",
  "original_source_count": 2,
  "requires_explicit_send": true,
  "executes_queries": false
}
```

404 for nonexistent/foreign-owned reports; 409 for revoked sources, missing/changed
originating conversation, ambiguous pairing, historical transcript changes,
invalid/unsupported mode, or oversized/empty original questions. Errors expose
neither the old report answer nor evidence passages nor the original question.

The endpoint is a read-only proposal, **not** an authorization capability,
query plan, replay token, stored SQL definition or signed approval. The browser
may use its data as editable composer text only. Query parameters do not
override mode, question, source IDs or policy.

## Security properties and intentional limitations

- No client-supplied answer, evidence or SQL is replayed.
- No historical SQL or model output is executed.
- Original question/mode are server-resolved; changed assistant answer or
  evidence causes fail-closed refusal.
- Source revocation after preview and before user send is handled by current
  Chat planning; **preview is not an execution-time authorization grant**.
- The fresh Chat request may select other **currently authorized sources**,
  because existing Chat planning is not pinned to the historical report
  source set. The UI explicitly describes current sources/policies; source
  pinning and repeatable typed report templates need a separate VS4-B2 gate.
- Historical reports whose originating conversation lacks an unambiguous
  preceding user turn or whose mode metadata is missing are intentionally
  *not* rerunnable through this feature; do not infer a generic mode.
- Present auth remains the single trusted dev user setting until VS5.
- The historical conversation is not modified by preparation. Sending creates
  an independent new conversation.
- The product remains separate from OpenJM Workspace; no cross-repo import,
  shared database, ORM model or runtime dependency.

## Acceptance and CI

PR GitHub Actions (fast regression) automatically checks backend Python 3.11,
critical Ruff, deterministic Pytest (including
`test_report_rerun_preflight.py`), frontend Vitest and TypeScript/Vite build.

Targeted failure/regression tests prove: owner isolation, all 3 modes, source
revocation, table-level changes, missing/edited original turns, ambiguous
mode, no tool traces, no auto-send from UI, and no stale evidence display after
a 409 response.

When implementation changes require real Gemma/Chroma/DB acceptance, run it
**once in isolated local fixtures** via Hermes, not blindly on every commit.
The existing VS3 policy and read-only SQL execution gates apply to actual
new Chat submissions; the preflight endpoint itself performs no execution.

## Release gate

- [ ] PR fast CI green and targeted tests pass.
- [ ] Manual review of historical question pairing and source revocation.
- [ ] Verify the UI never posts /api/chat on Prepare alone.
- [ ] Verify pressing Send uses an independent, new Chat conversation.
- [ ] Document limitations above in PR.
- [ ] Maintainer approval before merge.

Future VS4-B2: explicitly typed, source-bound rerunnable report definitions,
versioned parameters and immutable execution history. Do not invent a
second ungoverned SQL runner. VS4-C: export/UI polish; VS4-D: end-to-end
acceptance.
