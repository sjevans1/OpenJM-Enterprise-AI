# CI-3L Verification Tiers

OpenJM Enterprise AI uses three CI levels so release packaging and full integration evidence are not used as the first feedback loop for every small edit.

## FAST — iteration

**Trigger:** every pull request, or manual dispatch with `suite=fast`.

Runs:
- backend install + critical Ruff checks;
- fast backend pytest regression (the isolated real embedding integration is deferred);
- frontend Vitest + TypeScript/Vite production build.

FAST is deterministic iteration feedback only.

## STANDARD — work-package qualification

**Trigger:** push to `main`, or manual dispatch with `suite=standard` or `suite=full`.

Adds/uses:
- full backend repository pytest suite, including the isolated upload/source embedding integration;
- frontend test/build;
- REL1 lock-drift gate;
- production binary-only wheelhouse proof from the committed lock.

STANDARD is the package-completion gate before supervisory review and candidate freeze.

## FULL — frozen-candidate acceptance

**Trigger:** manual dispatch only with `suite=full`.

FULL includes every STANDARD check and additionally runs:
- REL1 offline bundle assembly;
- registry-blocked Python `--no-index` and npm `--offline` acceptance.

Project-specific controlled live gates remain separate where already defined (for example real Gemma/model acceptance). CI-3L does not convert those into optional checks.

## Post-merge

A push to `main` runs STANDARD rather than automatically repeating FULL. Reuse a valid frozen-head FULL result only when the accepted tested tree/build inputs and required base relationship remain valid. Repeat FULL if merge resolution, base/workflow/configuration/build-input changes invalidate that evidence, or when the release contract explicitly requires a main FULL run.

## Repair and parallel progress

Use FAST for ordinary correction loops. Run STANDARD once the work package is materially complete. Freeze before FULL.

While FULL is running, one explicitly authorized independent next package may proceed in a separate worktree/branch from the correct accepted base. Dependent work waits for the merge. Never mutate the frozen acceptance branch.

No deterministic, dependency, offline or release gate has been removed. The change is about **when** each gate runs.
