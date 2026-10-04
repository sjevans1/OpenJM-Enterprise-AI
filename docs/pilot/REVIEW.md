# VS4 pilot review evidence

## VS4-B2C2 Phase 1 local checkpoint

Starting main is `de391e92b510fb38e445e382fab33ee1517a2c12`. PR #28 is merged
and accepted; main CI run 37176830805 is green and `python
scripts/pilot_ci_gate.py --main` accepted. The active claim is
https://github.com/sjevans1/OpenJM-Enterprise-AI/issues/23#issuecomment-5982046257.

Phase 1 adds only `POST /api/reports/{report_id}/definitions/{version}/runs`.
The strict request contains one canonical string `idempotency_key`; malformed,
missing and extra fields return 422. The server resolves the exact definition ID
through the owned report, loads the validated trusted question/mode/scope with
the required `definition_id`, derives canonical pins, and reuses the B2C1
reservation service. New reservations and replays consistently return 202 with
the existing authorized `ReportRunDetail`. Replays pass through `_detail` again,
reserve no second row, and perform no execution. Different intent under the same
key returns a bounded 409; unknown/foreign report or version returns 404. A new
Phase-1 reservation remains `running` with no result. Public revoke remains absent.

Local evidence: `/home/sjeva/openjm-enterprise-ai/backend/.venv/bin/python -m
pytest -q tests/test_report_run_submission_api.py tests/test_report_run_regressions.py
tests/test_report_runs.py tests/test_report_runs_api.py tests/test_report_definitions.py
--maxfail=3` returned `102 passed in 15.92s`. The matching focused Ruff critical
check returned `All checks passed!`. Active-batch repair, total-repair and service
retry counters are all zero. No Phase 2 execution, public revoke, frontend,
provider, Workspace, push or PR work was performed.

## Historical VS4-B2C1 PR #28 final correction evidence

## Outcome and scope
Batch / issue / plan revision: VS4-B2C1 second correction pass. Issue #23. Plan `vs4-pilot-1`. Branch `milestone/vs4-b2c1-run-history`. Existing draft PR: https://github.com/sjevans1/OpenJM-Enterprise-AI/pull/28.

The four original defects already verified by the reviewer remain fixed. This revision closes the remaining run-read authorization finding. List and detail now authorize the run's exact definition ID, version, parent report, owner, requested mode, persisted evidence, equivalent document references, grounded-parameter document references, structured source IDs, table scope, current discovered schema and current source authorization. Any missing, foreign-owned, unavailable or out-of-scope source fails closed before protected metadata or results are returned.

The startup acceptance test now directs production `app.db.init_db()` at an isolated synthetic pre-B2C1 SQLite file twice. It preserves exact conversation, message, execution-trace, saved-report and definition rows, including IDs, relationships, answer/evidence, trace SQL, trace evidence IDs and metadata. Independent engines verify the new run schema, foreign keys, uniqueness, six checks, corrected trigger bodies, stale-run recovery, live-run preservation and trigger behavior. The placeholder manual `session.__aenter__()` call is removed, and every test-owned session and engine is context-managed or disposed in `finally`.

Preserved boundaries: no public run-submission or revoke endpoint; no model, retrieval, governed source-SQL or trace-writing calls on history reads; no frontend, provider, identity, deployment or Workspace-Platform changes. Pagination remains default 20 and maximum 50. The shared 131,072-byte result bound and exact 69,172-byte successful round trip remain covered. B2C2 remains paused.

## Local evidence before final hosted CI
- Python 3.11 project venv, isolated file-backed SQLite and FastAPI ASGI client.
- `python -m pytest -q tests/test_report_run_regressions.py tests/test_report_runs.py tests/test_report_runs_api.py tests/test_report_definitions.py --maxfail=3` returned `83 passed`.
- `tests/test_report_run_regressions.py` now collects 39 tests.
- New negative coverage returns 409 from both list and detail for: a pinned table removed from `schema_json` while the stale grant remains; exact definition pins changed to a missing document; mismatched definition identity; real foreign-owned evidence document; missing and owner-valid-but-out-of-scope documents; missing equivalent-source document; missing and owner-valid-but-out-of-scope structured sources; out-of-scope table; and missing grounded-parameter document.
- Positive fixtures now use the actual authorized document ID. Authorized reads, zero-execution sentinels, bounded pagination and the 69,172-byte result continue to pass.
- `python -m ruff check app/api/report_runs.py app/api/report_definitions.py tests/test_report_run_regressions.py tests/test_report_runs_api.py --select E9,F63,F7,F82` returned `All checks passed!`.
- No real development database was opened or copied.

## Production startup and recovery evidence
`test_synthetic_pre_b2c1_upgrade_preserves_data` creates a disposable file with nonempty conversations, two related messages, a saved report, immutable definition and execution trace. It removes only the B2C1 `report_runs` table, then monkeypatches the production database module's engine to that path and calls the actual `init_db()` twice. Fresh independent engines compare complete legacy row snapshots before and after each startup. Between startups, the test creates one expired and one live run. The second startup interrupts only the expired row, preserves the live row, reinstalls current triggers and leaves every legacy row byte-for-byte equivalent at the selected columns. Behavioral writes prove owner identity and terminal rows remain immutable.

## Hermes checkpoint and resume rehearsal
A real Hermes session restart occurred after review of `cc9ebdcc191a6998e0b30f8e513e6d19fc60dd52`. Before resume: branch `milestone/vs4-b2c1-run-history`, existing PR #28, phase `review corrections required`, active-batch code-repair counters 0, and next action was the review correction handoff. On resume, Hermes read repository files plus live Git and PR state, confirmed the same branch at `cc9ebdc` and the same open PR #28, and preserved unrelated working-tree files. It continued on that branch and PR through `982dc01456dc4fbe8446abf01fce17c4cdae6ca0`, `9d5482cd065cad5e1ae62218b1c2fb97584fc65b` and `372d45eb39cb893e9e7ac11557d652ae51bb2c0a`. No duplicate branch, PR or replacement history was created. Code-repair attempts remained 0; one transient GitHub API/network retry occurred without redispatch. Session evidence: @session:default/20261002_141343_c17344.

Limitation: the committed checkpoint at the original pause was stale and did not itself identify PR #28. Continuity is established by the resumed session's live branch/PR reconciliation and GitHub history. This revision records that limitation rather than replacing it with synthetic interruption evidence.

## Final-revision evidence (verified)
- Actual final 40-character PR head SHA: `21e1df78c0ea2a97388e372dfd4037808cc14a39` (single squashed correction+checkpoint commit; matches `git rev-parse HEAD` and live PR #28 `headRefOid`).
- Fast pull-request CI: run [37147192496](https://github.com/sjevans1/OpenJM-Enterprise-AI/actions/runs/37147192496), `success`, head `21e1df7`. Job `Backend / Python 3.11` (success), `Frontend / Node 22` (success). Selected fast backend step skipped by design.
- Full dispatch: run [37148238286](https://github.com/sjevans1/OpenJM-Enterprise-AI/actions/runs/37148238286) via `gh workflow run ci.yml --ref milestone/vs4-b2c1-run-history --field suite=full`, head `21e1df7`. Job `Backend / Python 3.11` (success) with selected step `Full backend regression (main or explicitly dispatched)` actually run and succeeded. Job `Frontend / Node 22` (success), TypeScript check and production build.
- Gate: `python scripts/pilot_ci_gate.py --pr 28 --expect-head 21e1df78c0ea2a97388e372dfd4037808cc14a39 --full-run 37148238286` returned `accepted: true`.
- This is the single final correction+checkpoint commit (code and tests byte-identical to the earlier 837f5d9 tree; only checkpoint/docs text differs). CI was run on this exact head.

## Pilot counters and decision
- Active-batch code-repair attempts: 0.
- Current normalized failure repair count: 0.
- Startup CLI prerequisite incidents: 1, resolved and classified as access/service rather than code repair.
- Transient API/network retries: 1.
- Formal review rounds: 2. Completed correction passes before this revision: 1.
- State at this push: `awaiting_review`, not accepted and not merged.

Stop after final-head CI, gate verification, and PR evidence publication. Do not merge or begin B2C2.