# Deterministic CI and Hermes handoff — OpenJM Enterprise AI

## Purpose and scope

GitHub Actions owns repeatable checks for `sjevans1/OpenJM-Enterprise-AI`.
Hermes owns code changes, targeted debugging, human-quality review and **only**
the environment-dependent acceptance steps that GitHub-hosted runners cannot
verify. OpenJM Workspace is an independently deployed product and is **not**
checked out, built, linked or called by this workflow.

Workflow: [`.github/workflows/ci.yml`](../.github/workflows/ci.yml).
Tracking: [Issue #15](https://github.com/sjevans1/OpenJM-Enterprise-AI/issues/15).

## Trigger matrix and cost boundaries

| Event | Backend | Frontend | Runs twice for same PR push? |
| --- | --- | --- | --- |
| PR opened/updated/reopened/marked ready | Python 3.11 + bounded Ruff + fast pytest | npm ci, Vitest, TypeScript/Vite | No: no feature-branch push trigger |
| `main` push | Python 3.11 + bounded Ruff + **full** pytest | Same | No: one workflow per `main` push |
| Manual dispatch with `suite=fast` | Fast pytest | Same | Explicit manual run |
| Manual dispatch with `suite=full` | Full pytest | Same | Explicit manual run |

Superseded runs in the **same PR/ref** are canceled; each job has a finite
timeout (backend 45 minutes, frontend 20). Both npm and pip use hosted caches
keyed to their dependency declarations. GitHub's Actions usage and billing
limits depend on current repository visibility/plan; the repository was
public when this workflow was prepared.

This workflow does not schedule recurring jobs, push commits, or create GitHub
issues. No automatic retries. It does not run on *every feature-branch push*
because that would duplicate the `pull_request` run.

**Initial Ruff baseline:** `E9,F63,F7,F82` only (fatal syntax/name problems).
This is *not* a claim that the repository currently meets all Ruff
formatting/style rules; add broader rules in a separate, benchmarked cleanup PR.
The frontend build already performs `tsc --noEmit`.

**Fast pytest:** `python -m pytest -q --tb=short --maxfail=3 --ignore=tests/test_upload_source_name_integration.py`.

**Full pytest:** same command without the ignore. The omitted upload test
boots a real isolated API server, ingests/retrieves with an embedding model,
and can depend on a public model download. It does not use Gemma.

## Local vs hosted acceptance

- Hosted CI validates deterministic application/API tests, critical lint,
  frontend components and build. Full CI additionally runs an isolated
  ingestion/embedding integration test (if model and network are available).
- Hosted CI does **not** authenticate to customer data sources or call the
  workstation's `127.0.0.1:18080` Gemma worker.
- A PR touching real grounded generation, Chroma, database connectors or
  policy-dependent hybrid execution still requires an **explicit local
  acceptance** in a test-owned isolated environment. Retain the previous
  A–I/VS3/VS4 acceptance gates where relevant.
- Production data, `.env`, secrets, credentials, and the existing
  `data/openjm.db` must never be uploaded to Actions artifacts or logs.
  Tests must create temporary data; never copy a real development DB into CI.
- Independent per-repository CI is mandatory; no Workspace dependency.

## Hermes operator contract

**Normal PR workflow**

1. Implement the smallest coherent change on a feature branch from current
   `main`. Use one PR, not competing feature branches.
2. Run the *focused changed tests locally at most once* when useful to catch
   obvious errors before a costly push. Do not re-run all tests for each edit.
3. Push a meaningful commit. Let GitHub Actions run **once** on PR update.
4. Read only the PR check conclusion and names:
   `gh pr checks <number> --repo sjevans1/OpenJM-Enterprise-AI`
5. If green: do not consume LLM tokens reading passing logs. Proceed to
   security/architecture review and any required local-model acceptance.
6. If red: identify the failing job; read **only failed logs**:
   `gh run view <run-id> --repo sjevans1/OpenJM-Enterprise-AI --log-failed`.
   Inspect the smallest relevant traceback, fix once, push again. Do not
   blindly retry a transient install/registry/network failure or rewrite
   functional code to hide a hosted-environment outage.
7. Preserve branch, PR, acceptance report, untracked
   `scripts/serve_frontend.py` and `test-documents/`. Never directly
   merge without explicit authorization.

**Full gate before merge (when warranted)**

After this workflow has been merged to default `main`, manually dispatch
on the PR branch (requires GitHub CLI permissions or the Actions UI):

```bash
gh workflow run ci.yml \
  --repo sjevans1/OpenJM-Enterprise-AI \
  --ref feature/my-branch \
  -f suite=full
```

Check the triggered run via:
```bash
gh run list --repo sjevans1/OpenJM-Enterprise-AI --workflow ci.yml --limit 5
gh run view <run-id> --repo sjevans1/OpenJM-Enterprise-AI --json status,conclusion,jobs
```
For failed steps only: `gh run view <run-id> --log-failed`.
The manual run checks its selected **branch SHA**; record that SHA in the
PR evidence, then repeat/review if code changes after acceptance.

**Important bootstrap limitation:** GitHub normally requires a
`workflow_dispatch` workflow to exist on the default branch before manual
dispatch. The CI PR itself is first validated via its `pull_request` event,
then manual full regression becomes available once the workflow is merged.

## Distinguish failure classes

- **Test/assertion failure:** real product regression unless proven otherwise;
  capture reproduction and fix in code/tests.
- **Packaging or dependency resolution failure:** inspect Python version,
  `pip` resolution and known DB-GPT compatibility; don't silently exclude
  failing imports or disable tests.
- **Embedding-model availability/download failure:** hosted-environment issue
  needing separate recorded resolution. Do not mark the full gate green.
- **Actions infrastructure outage:** note external status and retry once when
  service recovers rather than generating repeated noise.
- **Model-worker unavailable on CI:** expected; Gemma acceptance is a
  separate local gate and must be documented as such.

## Review before enabling branch protections

After the first green PR and full runs, use the two job check names
`Backend / Python 3.11` and `Frontend / Node 22` as required PR status
checks *if account-level repository rules are available*. Do not enable a
required check until it has proven stable and appears on the relevant
branches, and do not claim a protected branch exists until confirmed.

## Security properties

- Trigger uses `pull_request` (not privileged `pull_request_target`).
- Minimum workflow permission: `contents: read`.
- Checkout drops persisted credentials.
- Hosted ephemeral runners only, no private self-hosted machine access.
- No untrusted branch fields interpolated into shell commands or secrets.
- No `curl | bash`, no automatic commits, no artifact upload of datasets.
- Backend and frontend install from their project dependencies/lockfile.
- No retry loops, and check logs are read only if a check fails.

## First-run acceptance checklist

- [ ] The new workflow is visible on CI PR.
- [ ] Backend: Python 3.11 install + Ruff critical checks + fast tests pass.
- [ ] Frontend: npm ci + Vitest + TypeScript/Vite pass.
- [ ] Obsolete PR run cancels after a newer push (no forced synthetic pushes).
- [ ] After merge, manual full run and main push run demonstrate separate full-tier checks.
- [ ] No local model/data/Workspace dependencies or secret exposure.
- [ ] Document actual CI execution links, runtimes and any fail conditions.
