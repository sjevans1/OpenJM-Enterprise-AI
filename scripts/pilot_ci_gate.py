#!/usr/bin/env python3
"""Read-only, fail-closed GitHub CI gate for the VS4 pilot; Python stdlib + gh.

No logs, mutations, polling, model calls, credentials or local product services.
The command inspects GitHub twice to reject a PR/main revision changing mid-read.
"""
import argparse
import json
import re
import subprocess
import sys

REPO = "sjevans1/OpenJM-Enterprise-AI"
BACKEND = "Backend / Python 3.11"
FRONTEND = "Frontend / Node 22"
FAST = "Fast backend regression (pull requests)"
FULL = "Full backend regression (main or explicitly dispatched)"
REQUIRED_STEPS = {
    BACKEND: ("Install backend and test dependencies", "Ruff critical correctness checks"),
    FRONTEND: ("Install exactly from lockfile", "Run frontend tests", "TypeScript check and production build"),
}


class GateError(RuntimeError):
    """Observed evidence does not establish acceptance."""


def gh_json(path):
    try:
        result = subprocess.run(
            ["gh", "api", "--method", "GET", f"repos/{REPO}/{path}"],
            capture_output=True, text=True, timeout=45, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GateError("GitHub CLI unavailable or API timeout; no acceptance established") from exc
    if result.returncode:
        # gh stderr may contain account/config details; do not dump it automatically.
        raise GateError("GitHub API request failed; inspect access/service status separately")
    try:
        return json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise GateError("GitHub returned invalid JSON") from exc


def paged(api, path, key):
    rows = []
    separator = "&" if "?" in path else "?"
    for page in range(1, 21):
        payload = api(f"{path}{separator}per_page=100&page={page}")
        batch = payload.get(key)
        if not isinstance(batch, list):
            raise GateError(f"Missing API collection: {key}")
        rows.extend(batch)
        if len(batch) < 100:
            return rows
    raise GateError("API pagination bound exceeded; do not accept incomplete evidence")


def only_named(items, name, kind):
    matches = [item for item in items if item.get("name") == name]
    if len(matches) != 1:
        raise GateError(f"Expected exactly one {kind}: {name}")
    return matches[0]


def succeeded(item, description):
    if item.get("status") != "completed" or item.get("conclusion") != "success":
        raise GateError(f"{description} has not completed successfully")


def validate_run(run, jobs, *, sha, workflow_id, event, tier, branch, pr_number=None):
    if run.get("head_sha") != sha:
        raise GateError("Run is for a different commit")
    if run.get("workflow_id") != workflow_id or run.get("path", "").split("@")[0] != ".github/workflows/ci.yml":
        raise GateError("Run is not the approved CI workflow")
    if run.get("event") != event or run.get("head_branch") != branch:
        raise GateError("Run event/branch does not match this gate")
    if pr_number is not None and not any(
        item.get("number") == pr_number for item in run.get("pull_requests", [])
    ):
        raise GateError("Run is not associated with this PR")
    succeeded(run, "Workflow")
    for name, steps in REQUIRED_STEPS.items():
        job = only_named(jobs, name, "job")
        succeeded(job, name)
        required = steps + ((FAST if tier == "fast" else FULL,) if name == BACKEND else ())
        for step in required:
            succeeded(only_named(job.get("steps", []), step, "step"), step)
    return {
        "run_id": run["id"], "head_sha": sha, "tier": tier,
        "event": event, "url": run.get("html_url"),
        "jobs": [BACKEND, FRONTEND],
    }


def workflow(api):
    value = api("actions/workflows/ci.yml")
    if value.get("path") != ".github/workflows/ci.yml" or value.get("state") != "active":
        raise GateError("Expected active ci.yml workflow is unavailable")
    return value["id"]


def latest_run(api, workflow_id, sha, event, pr_number=None):
    runs = paged(api, f"actions/workflows/{workflow_id}/runs?head_sha={sha}&event={event}", "workflow_runs")
    candidates = [run for run in runs if run.get("head_sha") == sha and run.get("event") == event]
    if pr_number is not None:
        candidates = [run for run in candidates if any(
            pr.get("number") == pr_number for pr in run.get("pull_requests", [])
        )]
    if not candidates:
        raise GateError("No matching CI run; absence is not success")
    # Never fall back to an older green run when a newer run is pending or failed.
    latest = max(candidates, key=lambda run: int(run["id"]))
    return api(f"actions/runs/{latest['id']}")


def inspect_run(api, run, **expected):
    jobs = paged(api, f"actions/runs/{run['id']}/jobs?filter=latest", "jobs")
    result = validate_run(run, jobs, **expected)
    again = api(f"actions/runs/{run['id']}")
    for key in ("id", "head_sha", "status", "conclusion", "run_attempt", "updated_at"):
        if again.get(key) != run.get(key):
            raise GateError("CI run changed while evidence was being collected")
    return result


def pr_snapshot(pr):
    return (
        pr.get("state"), pr.get("head", {}).get("sha"),
        pr.get("base", {}).get("sha"), pr.get("merge_commit_sha"),
    )


def verify_pr(api, number, expect_head, full_run=None):
    pr = api(f"pulls/{number}")
    if pr.get("state") != "open" or pr.get("base", {}).get("ref") != "main":
        raise GateError("Expected an open PR targeting main")
    if pr.get("head", {}).get("repo", {}).get("full_name") != REPO:
        raise GateError("Pilot requires a branch in the approved repository")
    sha = pr["head"]["sha"]
    if sha != expect_head:
        raise GateError("PR head differs from the caller's recorded revision")
    main = api("branches/main")["commit"]["sha"]
    comparison = api(f"compare/{main}...{sha}")
    if comparison.get("status") not in ("ahead", "identical"):
        raise GateError("PR does not contain current main; reconcile the base first")
    workflow_id = workflow(api)
    run = latest_run(api, workflow_id, sha, "pull_request", number)
    results = [inspect_run(
        api, run, sha=sha, workflow_id=workflow_id, event="pull_request", tier="fast",
        branch=pr["head"]["ref"], pr_number=number,
    )]
    if full_run is not None:
        full = api(f"actions/runs/{full_run}")
        latest_full = latest_run(api, workflow_id, sha, "workflow_dispatch")
        if latest_full["id"] != full_run:
            raise GateError("Selected full dispatch is not the latest dispatch for this revision")
        results.append(inspect_run(
            api, full, sha=sha, workflow_id=workflow_id, event="workflow_dispatch", tier="full",
            branch=pr["head"]["ref"],
        ))
    if pr_snapshot(api(f"pulls/{number}")) != pr_snapshot(pr):
        raise GateError("PR changed while evidence was being collected")
    if api("branches/main")["commit"]["sha"] != main:
        raise GateError("Main changed while evidence was being collected")
    # Recheck selection as well: a newer ready_for_review run can share the SHA.
    if latest_run(api, workflow_id, sha, "pull_request", number)["id"] != run["id"]:
        raise GateError("A newer PR run appeared; wait for that run")
    return {"repository": REPO, "pr": number, "head_sha": sha, "base_sha": main, "evidence": results}


def verify_main(api):
    sha = api("branches/main")["commit"]["sha"]
    workflow_id = workflow(api)
    run = latest_run(api, workflow_id, sha, "push")
    result = inspect_run(api, run, sha=sha, workflow_id=workflow_id, event="push", tier="full", branch="main")
    if api("branches/main")["commit"]["sha"] != sha:
        raise GateError("Main changed while evidence was being collected")
    if latest_run(api, workflow_id, sha, "push")["id"] != run["id"]:
        raise GateError("A newer main run appeared; wait for that run")
    return {"repository": REPO, "main_sha": sha, "evidence": [result]}


def positive_int(raw):
    value = int(raw)
    if value < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--pr", type=positive_int)
    group.add_argument("--main", action="store_true")
    parser.add_argument("--expect-head", help="Full 40-character PR commit SHA")
    parser.add_argument("--full-run", type=positive_int, help="Full workflow_dispatch run for this PR revision")
    args = parser.parse_args()
    if args.pr and (not args.expect_head or not re.fullmatch(r"[0-9a-f]{40}", args.expect_head)):
        parser.error("--pr requires --expect-head with the full commit SHA")
    if args.main and (args.expect_head or args.full_run):
        parser.error("--expect-head/--full-run apply only to --pr")
    try:
        result = verify_main(gh_json) if args.main else verify_pr(gh_json, args.pr, args.expect_head, args.full_run)
    except (GateError, KeyError, TypeError, ValueError) as exc:
        print(json.dumps({"accepted": False, "reason": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps({"accepted": True, **result}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
