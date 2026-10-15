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

## Identifiable workflow runs for the Framework controller (staged)

This repository retains its original `OpenJM CI` FAST workflow and CI-3L job coverage. For **new owner-authorized work packages** under the risk-qualified Hermes controller, use these distinct workflow identities for the two higher tiers:

| Tier | GitHub Actions workflow name | Dispatch |
| --- | --- | --- |
| FAST | `OpenJM CI` | Automatic PR run (or the existing FAST dispatch) |
| STANDARD | `OpenJM STANDARD qualification` | Manual workflow dispatch at the reviewed candidate ref |
| FULL | `OpenJM FULL qualification` | Manual workflow dispatch at the frozen candidate ref **after** independent Haiku acceptance and fresh Luna review |
| Docs-only | `Documentation checks` | Automatic for Markdown-only PRs; no package build, STANDARD or FULL |

The STANDARD and FULL entrypoints reuse the existing CI implementation via GitHub `workflow_call`; they do not duplicate or relax the application, security, build, deployment or offline jobs. The older single-workflow dispatch remains available for legacy/manual troubleshooting but **must not be claimed as an independently identified STANDARD/FULL run** by the new controller.

The controller's `ci_workflows` values must match the actual GitHub `workflowName`. Its per-tier `ci_tiers` job-name manifests must be captured from a real successful run of each wrapper because reusable job names may have a qualified prefix. An unverified job name, dispatch context, missing suite identity or mismatched SHA blocks activation.

Docs-only qualification is permitted only for an exact-head GitHub diff consisting entirely of literal Markdown files (`.md`/`.mdx`), with verified independent Haiku/Luna stages and a human merge decision. Changes to `framework.lock`, `.github/workflows/`, scripts, application code or security configuration cannot claim docs-only. The dedicated documentation workflow checks UTF-8, conflict markers and diff whitespace. Non-Markdown changes continue to run the main CI.

**Important:** These entries introduce a CI transport compatible with future controller activation; they do not activate or re-admit currently running Hermes work packages, alter a reserved correction allowance, change model routing or authorize a merge. See [framework adoption](FRAMEWORK_ADOPTION.md).
