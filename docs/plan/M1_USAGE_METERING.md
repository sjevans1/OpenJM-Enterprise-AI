# #46 M1: immutable LLM usage-metering foundation

Base: BV2 local head `9a38ed3`. Package 4 of the #45 temporary autonomous work
package. Local branch: `feature/m46-m1-usage-metering`.

Scope is **metering only**: no pricing, invoices, payments, Stripe, tax,
subscription UI or commercial billing screens.

## Model

`model_usage_events` (append-only, one row per model invocation):
tenant, principal, parent `request_id`, conversation/message, execution class,
provider route, model name, usage source (provider_reported | estimated),
input/output/total tokens, optional cached-input and reasoning tokens, call role
(primary | retry | fallback), `parent_call_id`, `idempotency_key`, status and
failure category, started/finished timestamps.

Database guards: unique `idempotency_key`, check constraints for source, role,
status and non-negative tokens, and (SQLite) a `BEFORE UPDATE` trigger that
aborts any update of a finalized row, so a correction must be a new adjustment
row.

## Service

`app/services/usage_metering.py`:
- `ModelCallUsage` with `estimate(...)` (deterministic ~4 chars/token, marked
  estimated) and `from_provider_usage(...)` (OpenAI-compatible; malformed or
  absent usage returns None so the caller estimates rather than trusting an
  unclear payload).
- `record_model_usage(...)`: exactly-once finalization keyed on
  `<request_id>:<call_role>:<attempt>`. A replay returns the existing row; a
  concurrent insert is resolved by the unique constraint. A retry or fallback is
  a separate row linked by `parent_call_id`.
- `summarize_usage(...)`: tenant-scoped totals, optionally narrowed to one
  parent request.

## Instrumentation

The central `OpenAICompatibleModelGateway` is the single seam:
`chat(..., usage_context, db)` and `_generate(...)` meter each HTTP attempt
(primary attempt 0, bounded retry attempt 1 with `parent_call_id` set to the
primary row). Provider `usage` is captured when present; otherwise an estimate is
recorded. Metering failures never break the model path. The ordinary Chat route
supplies the context, so metering is attributable to tenant, principal,
conversation and execution class.

## Tests

`backend/tests/test_usage_metering.py`: estimation determinism, provider usage
capture, malformed-usage fallback, estimated local-model row, exactly-once
finalization, retry/fallback as separate linked rows, duplicate-key rejection at
the database level, tenant-scoped summary, request-narrowed summary, migration +
append-only trigger, and gateway instrumentation with and without a context.

Mutation checks: dropping the unique constraint fails the duplicate-key test
(`test_duplicate_key_is_rejected_at_the_database_level`). Removing the service's
fast-path pre-check does **not** fail the suite, because the unique constraint and
the `IntegrityError` fallback still enforce exactly-once: the guard is
defence-in-depth rather than a single point.

## Correction pass (PR #51 review)

- **Per-turn request identity.** The Chat route now mints a fresh `uuid4` per
  submission as the correlation id and passes it both to the orchestrator and to
  the gateway. `conversation_id` is stored separately for aggregation, so two
  turns in one conversation are two distinct billable events.
- **Transaction-safe exactly-once.** `record_model_usage` inserts inside a
  SAVEPOINT, so a unique-constraint race unwinds only the savepoint. It never
  calls `rollback()` on the caller's session, so unrelated pending business state
  survives and still commits.
- **Failed attempts keep evidence without orphan Chat state.** On a provider
  failure the gateway attaches the consumed-attempt evidence to the raised
  error. The Chat route rolls back the abandoned turn (no orphan user message),
  then persists the failure usage row on its own and commits. Report runs do the
  same after marking the run failed; the orchestrator does it for an abandoned
  planner attempt. A second session is deliberately **not** used: it cannot
  commit while the caller holds SQLite's write lock.
- **Invocation coverage.** The structured-planner and report-execution model
  calls are now metered through the central gateway (successful calls record
  usage; abandoned attempts record a failure row). Ordinary Chat, planner and
  report execution are all covered.

## NOT RUN / limitations

- Local model token accounting uses the deterministic estimate, not a real
  tokenizer.
- No pricing/entitlement conversion, no admin views.
- GitHub CI for the correction head: dispatched, see the PR freeze comment.

