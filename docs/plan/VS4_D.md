# VS4-D — Integrated acceptance and VS4 closure

Tracker: [#26](https://github.com/sjevans1/OpenJM-Enterprise-AI/issues/26).
Branch: `milestone/vs4-d-acceptance`. Depends on accepted C2.

## Phases and acceptance

1. **Reconcile evidence.** Map every VS4 acceptance criterion in #11/#19/#23/#25
   to an actual test, runtime result or remaining limitation. Do not claim the
   entire platform complete: VS5–VS8 remain outside this pilot. Preserve previous
   batch evidence; rerun only integration checks or changed-code gates that are
   needed for the final assembled feature.
2. **Integrated local acceptance.** On an isolated test instance at the final
   candidate revision, verify saved snapshots; pinned Knowledge/Data/independent
   and dependent Hybrid runs; fresh policy parameters; unchanged old snapshots
   and terminal runs; restart persistence; same-key and concurrent submissions;
   CSV/HTML export; deletion, disabled source and permission revocation. Verify
   partial Hybrid, hostile policy and wrong-owner requests fail closed. Inspect
   citations and as-of/failure UI, not just /health. Test source DB stays read-only.
3. **Upgrade and recovery.** Re-run a synthetic old-schema upgrade twice and
   verify record preservation. Demonstrate crash/expired-run recovery without
   re-execution, cleanup only test-owned resources and document disable/recovery
   steps. Do not downgrade by dropping tables or test on the real development DB.
4. **Review package.** Add `docs/VS4_ACCEPTANCE_REPORT.md` with exact SHAs, hosted
   fast/full run links, local commands/results, model/runtime identity, fixture
   isolation/cleanup and known limits. Update the accurate product documentation
   after reconciling open PR #13; do not discard its content or merge it blindly.
   Include observed pilot measurements, review corrections and interruption/resume
   outcome. Unknown costs/usage remain unknown. Request final VS4 review.

All normal CI gates and final local acceptance are required; no skipped acceptance
or speculative pass. Relevant fixes stay narrow and have regression tests. A newly
discovered architectural problem becomes a plan-change issue, not a last-minute
rewrite or expansion into identity, migrations, Workspace or agents.

After human-authorized merge, full main-push CI must pass before recording VS4
accepted and closing parent checklists. Hermes may report this read-only result;
it cannot merge, deploy or enable execution on an existing instance. Retain the
single-dev-user, #6 lifecycle and #7 migration limitations explicitly.
