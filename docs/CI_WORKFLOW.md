# Continuous integration and review workflow

This document is **tool-neutral** and complements the authoritative [CI tier contract](CI_TIERS.md), [GitHub Actions implementation](../.github/workflows/ci.yml) and [contribution rules](../CONTRIBUTING.md).

## Source control and evidence

- Branch from the current accepted base. Keep PRs small and distinguish documentation, UI, backend, migration and release changes.
- On every PR, review the changed files and run relevant focused checks locally before consuming hosted CI. Resolve a failing check from actual logs/traces and record the bounded cause and correction; do not claim an old green run accepts a new head.
- Use the three declared verification levels: **FAST** for iteration, **STANDARD** for complete work-package qualification, **FULL** for frozen release/offline-bundle qualification and material lifecycle changes. Always inspect the **actual committed workflow**, required job names and accepted project policy before classifying a run.
- Backend Python 3.11 and frontend Node 22 are independent gates. TypeScript/Vite, Vitest, Chromium Playwright and lock/offline qualification must not be substituted for one another.
- Record each run id, exact head SHA, run event/dispatch suite, required jobs and statuses, result and representative failures. Skipped, cancelled, failed and not-run checks cannot be represented as PASS.

## Human review and acceptance

1. Confirm tests are at the current PR head and the change is within the approved business scope.
2. Review tenant/policy/identity effects, migration and recovery plan, dependency/license posture and actual UI screens/states.
3. For functionality connected to PostgreSQL, Keycloak, models or external data sources, identify which journeys were exercised live; fixture tests do not qualify live dependencies.
4. Record reviewer findings and resolution, plus any pending legal/security/UX decisions. The maintainer—not CI—authorizes merge and production promotion.
5. After merge, verify that the accepted `main` run and release evidence apply to the assembled tree. Do not automatically repeat release FULL for documentation-only changes, but do not skip it when the artifact/lock/release path changes.

## Responsibility and boundaries

This is a conventional engineering handover. It specifies **what must be proven**, not which editor, assistant, automation product or individual generated the code. Repo-specific operational facts, fixture assumptions and historical qualified artifact SHAs remain in their respective technical documents. Secret material and private team coordination belong in an approved external channel, never in source control.
