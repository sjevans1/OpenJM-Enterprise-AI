# PR evidence and pilot measurements

Use this template in the PR description/comment; never put secrets or real
customer evidence in it. Leave unchecked items unchecked until observed.

## Outcome and scope

Batch / issue / plan revision:
Problem solved and user-visible behavior:
Implementation choices / approved deviations:
Preserved boundaries and known limitations:

## Evidence

- Code SHA (full), PR URL, base main SHA:
- Fast PR CI run / selected backend test step / frontend result:
- Full dispatch run and SHA / full backend step / frontend result:
- Local verification: required or not-applicable with contract reason:
- Actual commands / environment / model / timestamp / assertion results:
- Fixture isolation, source read-only proof and test-owned cleanup:
- Migration/recovery and failure-case evidence:
- Screenshots/artifacts (synthetic data only):
- Independent reviewer findings / corrections:
- Remaining manual checks:

## Pilot measurements

Started / finished (UTC):
Active implementation time / waiting time / elapsed time:
Coding model(s) actually used:
Usage/cost from provider, if available (else unknown):
CI runs and durations / cancelled superseded runs:
Repair attempts / service retries / review corrections:
Human interventions and reasons:
Interruption/resume result (B2C1 mandatory):

## Decision

- [ ] Final committed revision matches the attached evidence.
- [ ] Expected jobs and selected tier steps succeeded (not skipped/neutral).
- [ ] Applicable local and negative acceptance gates passed.
- [ ] PR is ready for review; no auto-merge enabled.
- [ ] Maintainer authorized merge (record reference; Hermes does not merge).
- [ ] Actual merged main SHA and full post-merge CI passed.

Do not use this template's presence as evidence of completion.
