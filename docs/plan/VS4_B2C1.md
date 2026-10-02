# VS4-B2C1 — Persisted run lifecycle and read-only history

Tracker: [#23](https://github.com/sjevans1/OpenJM-Enterprise-AI/issues/23).
Branch: `milestone/vs4-b2c1-run-history`. One PR. Next: B2C2 after review/merge.

## Preconditions and scope

Pilot setup merged, latest full main CI green, no other active VS4 implementation
PR/writer. Read models.py, db.py, schemas.py, api/report_definitions.py,
api/reports.py and the B2A/B2B contracts. Preserve old work and local .env/data.

Implement the persistence contract and safe read-only history API. **No public
run-submission endpoint, model calls, retrieval or SQL execution in this batch.**
The internal reservation/finalization functions are exercised by isolated tests;
they do not create fake reports in the application.

## Phases and acceptance

1. **Run identity and lifecycle.** Add a ReportRun tied to immutable definition
   id/version, report and trusted user, requested mode, start/deadline/finish
   times, status, bounded failure category, safe trace references and result
   payload. Intent/identity never change. Lifecycle transitions are monotonic;
   terminal answer/evidence/structured result are written once and immutable.
   Terminal states: succeeded, failed, interrupted. No PATCH/result-edit API.
2. **Idempotency and recovery.** Require a canonical UUID idempotency token for
   future submission. Unique `(user_id, idempotency_key)` in the database; bind
   it to report/definition/version using a request fingerprint. Same key+intent
   returns the same run; different intent returns 409; no duplicate work after
   concurrent requests or restart. Failed runs require a new explicit token to
   retry. Expired unfinished runs become interrupted, never silently re-executed.
   The reservation commits before external work and has a single winning owner.
3. **History API.** Add bounded owner-scoped GET list/detail under
   `/api/reports/{report_id}/runs`. Default page 20, max 50, stable ordering.
   Wrong owner/unknown ID yields 404; revoked/missing required sources mask
   metadata and deny result/evidence delivery consistently with saved reports.
   Reads create no execution traces or calls. Run data is deleted with its report
   through reviewed FKs, never source documents/databases/conversations. Deleting
   a report while a run is active must not later resurrect its history.
4. **Verification and checkpoint.** Exercise upgrade and repeated startup using
   a synthetic pre-B2C1 SQLite DB with real persisted fixture records. Preserve
   conversations, messages, traces, snapshots and definitions. Test concurrent
   reservation with separate connections to a temporary file-backed SQLite DB,
   stale-run recovery, payload tampering, wrong owner, revoked evidence, page
   bounds and terminal immutability. Existing Chat/report behavior stays green.

Implement in models/db/schemas and a small report-run service/API module; tests
in `backend/tests/test_report_runs.py` (or equally focused modules). No frontend,
SQL-policy redesign, deployment settings, real identity or provider changes.
Detailed security semantics above are fixed; helper names are implementation choices.

## Bounds and evidence

Preserve existing question bounds (12,000 characters / 48,000 UTF-8 bytes),
32 document pins / 8 source pins / 32 table pins and 24 evidence items. Terminal
answer <=24,000 characters and <=65,536 bytes; evidence <=65,536 bytes; total
serialized result including typed structured rows <=131,072 bytes. Oversize
results fail explicitly, never silently claim complete output. Existing source
SQL row/time bounds remain unchanged. B2C2 defines execution budgets.

Required: final-head fast PR CI plus full hosted dispatch (backend and frontend),
synthetic SQLite upgrade/concurrency evidence, and security review. Real Gemma
is **not required for this non-executing batch**. Demonstrate GET calls make zero
model/retrieval/SQL calls using test sentinels. No real development DB copies.

Pilot-specific: checkpoint mid-batch, resume from files plus live Git/PR state,
and prove the same branch/PR is reused with counters preserved. Do not kill a
running destructive operation or discard edits to manufacture an interruption.
Record metrics using `docs/pilot/REVIEW.md`; stop at awaiting_review.
