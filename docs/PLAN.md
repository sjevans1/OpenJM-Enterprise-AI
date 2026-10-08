# VS4 lifecycle pilot — OpenJM Enterprise AI

## Current post-VS8 work — INF1 (2026-10-08)

The VS4 pilot below is historical. The current post-VS8 execution umbrella is
[Issue #45](https://github.com/sjevans1/OpenJM-Enterprise-AI/issues/45), with
metering and entitlements in [Issue #46](https://github.com/sjevans1/OpenJM-Enterprise-AI/issues/46).
For inference-serving work, use [INF1 delivery and acceptance](plan/INF1.md),
[architecture](architecture/INF1_RAHKIA_SERVING_PLANE.md),
[contracts](architecture/INF1_CONTRACTS.md) and the
[executor handoff](INF1_EXECUTION_PROMPT.md).

INF1 architecture is prepared for review; runtime implementation follows
BV3-C PostgreSQL acceptance and integration of BV3-A/B/C. Then INF1-A and M2 may
proceed in parallel where contracts do not conflict. Reconcile INF1 admission
with M3 before commercial completion. No merge authority is implied.

The historical pilot's single-user/SQLite and uncompleted-VS5 assumptions do not
supersede the current accepted code or Issue #45. Preserve its evidence and
review discipline without treating its old stage as current.


Plan revision: `vs4-pilot-1` · Approved direction: Shane Evans, 2026-10-02.
Repository: **sjevans1/OpenJM-Enterprise-AI only**.
Pilot tracker: [#24](https://github.com/sjevans1/OpenJM-Enterprise-AI/issues/24).
Product roadmap: [#11](https://github.com/sjevans1/OpenJM-Enterprise-AI/issues/11).

## Start here

Read [STATUS.md](STATUS.md), this index, and only the active batch contract below.
Follow [the Hermes handoff](../HERMES_VS4_PILOT_PROMPT.md). Read referenced code
before editing. Git/GitHub are evidence of what happened; STATUS is a resumable
checkpoint, not authority to ignore a conflicting live branch or PR.

The pilot setup PR must be approved/merged and its main-push CI must pass before
Hermes starts B2C1. Each row below is **one separately reviewed PR**; its internal
phases run without further confirmation. Stop at review after each row. Following
an authorized merge and successful main regression, resume the next row on fresh
main. Do not reopen completed work or infer permission to merge.

## Verified starting point

Main `9343f5b1e70552fba034f05e745d8f44b8bea5ed`, inspected 2026-10-02:

| Delivered foundation | Evidence |
| --- | --- |
| VS4-A: immutable saved snapshots and basic save/list/open/delete UI | PR #14 |
| VS4-B1: read-only preflight and explicit exploratory Chat handoff | PR #18 |
| VS4-B2A: immutable, source-pinned definition v1; not runnable | PR #20 |
| VS4-B2B: scope enforcement before retrieval, model planning and SQL | PR #22 |
| Full post-merge backend and frontend regression | [Run 36975813425](https://github.com/sjevans1/OpenJM-Enterprise-AI/actions/runs/36975813425) |

No ReportRun model/API or CSV/HTML export exists at this baseline. Basic Reports
UI **does** exist; extend it. Historical PR #5 (Phase E parity) and PR #13 (old
roadmap/README refresh) are excluded from active pilot implementation. Preserve
them and surface overlap; do not close, merge or copy them automatically.

## Remaining VS4 review batches

| Order / batch | Outcome and contract | Tracker | Depends on |
| --- | --- | --- | --- |
| 1 · VS4-B2C1 | [Run persistence, idempotency and read-only history](plan/VS4_B2C1.md) | [#23](https://github.com/sjevans1/OpenJM-Enterprise-AI/issues/23) | Pilot setup merged + green main |
| 2 · VS4-B2C2 | [Explicit governed execution and immutable results](plan/VS4_B2C2.md) | #23; [#19](https://github.com/sjevans1/OpenJM-Enterprise-AI/issues/19) | B2C1 merged + green main |
| 3 · VS4-C1 | [Manual-run UI, history and failure states](plan/VS4_C1.md) | [#25](https://github.com/sjevans1/OpenJM-Enterprise-AI/issues/25) | B2C2 merged + green main |
| 4 · VS4-C2 | [Authorized bounded CSV and HTML/print export](plan/VS4_C2.md) | #25 | C1 merged + green main |
| 5 · VS4-D | [Integrated acceptance, recovery and release evidence](plan/VS4_D.md) | [#26](https://github.com/sjevans1/OpenJM-Enterprise-AI/issues/26) | C2 merged + green main |

This groups #23's proposed increments into storage and execution PRs; immutable
results and failure metadata belong to B2C2, and the user-facing controls to C1.
Its B2C4/B2-D security and real-runtime gates apply **before B2C2 acceptance**;
VS4-D repeats only the integration checks needed to close the assembled feature.

## Shared invariants

- Workspace remains a standalone repository/product. No imports, shared DB,
  secrets, deployment, CI or local-port changes; optional integration is #9.
- Preserve VS1–VS3, ordinary Chat routing, saved snapshots, exact source/table
  pins, permission checks, SQL AST/read-only/row/timeout policy and citations.
- No SQL replay from Evidence, client-supplied question/pins/model output,
  fallback to all authorized sources, scheduled reports or automatic execution.
- An incomplete Hybrid answer is a failed report run, never partial success.
- Reauthorize at execution, every read and export. Do not expose stale cached
  evidence after a source is revoked. State the existing cross-process source
  lifecycle limitation (#6); do not claim this pilot solves it.
- Current identity is a trusted **single development user**. Wrong-owner tests
  are required; multi-tenant/SSO guarantees and production identity are VS5.
- SQLite metadata upgrades are additive and tested on synthetic old schemas.
  PostgreSQL application-metadata migrations remain #7. Never touch real data.
- Existing controls cannot be weakened to pass CI. New behavior needs meaningful
  positive/negative acceptance tests; implementation-mirroring tests are insufficient.

## Evidence and completion

Every batch requires final-revision `OpenJM CI`: `Backend / Python 3.11` and
`Frontend / Node 22`. The backend's selected fast/full test step must actually
succeed; its intentionally unselected tier may be skipped. A green workflow
with missing/skipped expected jobs or steps is insufficient.

Run `python scripts/pilot_ci_gate.py --pr NUMBER --expect-head SHA` for the fast
PR gate; add `--full-run RUN_ID` for the required full dispatch. Full hosted CI
is required before accepting each product batch. It does not replace local
Gemma/Chroma/SQLite acceptance where the batch contract requires it.

Commit implementation and checkpoint changes **before** the final CI gate.
Publish final evidence in the PR using [the review template](pilot/REVIEW.md);
do not create another commit merely to record that commit's passing checks.
After marking ready, inspect any newly triggered run before presenting acceptance.

Use `planned`, `in_progress`, `awaiting_ci`, `awaiting_local_verification`,
`awaiting_review`, `blocked`, `awaiting_main_ci`, and `accepted` precisely.
Only a human-authorized merge followed by green full main regression permits
`accepted`. Never check off the next batch using an earlier revision's result.

## Decision ownership and pilot evaluation

Hermes may choose implementation details within scope. Requirements, architecture,
acceptance criteria, phase dependencies and security boundaries need a documented
plan-change decision. PLAN plus linked batch contracts are normative; issue text
tracks them. If they contradict one another, stop and propose a correction.

Planner/reviewer: ChatGPT. Implementer: Hermes. Merge/production authority: Shane.
Hermes uses GPT-5.6 Sol, then its native free Laguna when GPT credits finish.
No provider reconfiguration or extra fallback work is part of the pilot. The
application's local Gemma runtime is independent of Hermes's coding model.

Measure per accepted PR: elapsed and active work time, CI run count/duration,
failed repair attempts, review corrections, human interventions and provider
usage/cost **when available**. Record unknowns as unknown. The first B2C1 PR
also rehearses interruption/resume without duplicate commits, branches or PRs.
Evaluate the pilot after that PR before changing the operating contract.

