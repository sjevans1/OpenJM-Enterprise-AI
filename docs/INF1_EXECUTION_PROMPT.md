# Hermes handoff — INF1 Rahkia inference serving plane

Work only in `sjevans1/OpenJM-Enterprise-AI`. This is a gated implementation
handoff, not permission to merge, deploy or skip the active BV3 work.

Read:

1. [Issue #45](https://github.com/sjevans1/OpenJM-Enterprise-AI/issues/45), including its INF1 decision.
2. [INF1 delivery plan](plan/INF1.md).
3. [Architecture](architecture/INF1_RAHKIA_SERVING_PLANE.md) and [contracts](architecture/INF1_CONTRACTS.md).
4. [Issue #46](https://github.com/sjevans1/OpenJM-Enterprise-AI/issues/46), the accepted M1 code and current BV3 integration evidence.
5. Applicable repository instructions and the current CI workflow. Earlier VS4 pilot status is historical, not the current product stage.

## First action: prove the start gate

Read the live BV3-A/B/C PRs #52/#53/#54 and current main. Verify latest-head
BV3-C PostgreSQL acceptance, human-authorized integration of the train and
required green main regression. Do not substitute an older SHA's evidence.
If the gate is not earned, report its exact missing item and leave runtime and
migrations untouched. Architecture preparation does not waive this gate.

If earned, use a fresh isolated branch from current main. Preserve existing
uncommitted work. Reconcile the new docs with the live source before coding.
If another INF1/M2 branch already exists, inspect it before creating duplicates.

## Implement INF1-A only

Create the bounded registry, typed adapter, routing and attribution foundation
specified in `plan/INF1.md`. Keep the application `chat(...)` contract and
accepted output validation. Resolve every tenant/actor/policy from server
context. Reuse M1 and BV3 primitives; do not rebuild identity, ledgers or admin
planes. Keep secrets/addresses out of customer responses and content out of
operational records and export payloads.

Before modifying shared files, agree with M2 on migration order, the one-to-one
usage-attribution join and the business-request/logical-call/attempt mapping.
M2 may aggregate independently; it must retain legacy usage without fabricated
deployment attribution. Do not assume that a request/role/ordinal key separates
every real planner and synthesis call: inspect and test the actual caller graph.

Use RED→GREEN tests for authorization and tenant safety. Additive migrations
must run against populated synthetic SQLite and PostgreSQL databases. Security
mutation checks start from committed implementation in an isolated worktree;
never direct `git checkout --`, `git restore`, `git reset --hard` or `git clean`
at uncommitted implementation work. Batch focused checks, use GitHub Actions for
full regression, and inspect failing logs rather than repeatedly reading green
logs. After two unexplained correction cycles, preserve work and state the
blocker rather than weakening acceptance.

No autoscaling, Kubernetes, new public inference API, streaming UI, fine-tuning,
payments, external model fallback or Workspace changes. A may register shared
mode as disabled; INF1-B owns real shared-serving qualification. Do not claim
vLLM/SGLang isolation or performance from an OpenAI-compatible fixture.

## Freeze and hand off

Open a draft PR and stop at the review boundary. Report:

- exact head/base, dependencies and migration chain;
- A01–A14 evidence, with NOT RUN where the required environment is absent;
- exact-head CI jobs/runs and any PostgreSQL/runtime acceptance limits;
- M2 interface reconciliation and risks remaining for INF1-B/M3;
- what changed in product behavior and what remains a contract only.

Do not mark INF1-A accepted from documentation or protocol fixtures alone.
Do not start B/C automatically, merge any PR, or change commercial policy.
Shane retains merge/production authority. Request only missing product decisions
that materially change the agreed contract; routine implementation choices do
not require repeated confirmation.
