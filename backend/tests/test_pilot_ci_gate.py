"""Acceptance must never be inferred from stale or incomplete CI evidence."""
import copy
import importlib.util
from pathlib import Path
import unittest

_path = Path(__file__).resolve().parents[2] / "scripts" / "pilot_ci_gate.py"
_spec = importlib.util.spec_from_file_location("pilot_ci_gate", _path)
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)

HEAD = "a" * 40
BASE = "b" * 40
BRANCH = "milestone/vs4-b2c1-run-history"


def completed(name):
    return {"name": name, "status": "completed", "conclusion": "success"}


def jobs(tier="fast"):
    result = []
    for name, steps in gate.REQUIRED_STEPS.items():
        names = list(steps)
        if name == gate.BACKEND:
            names.append(gate.FAST if tier == "fast" else gate.FULL)
        result.append({**completed(name), "steps": [completed(step) for step in names]})
    return result


def run(ident=10, event="pull_request", sha=HEAD):
    return {
        "id": ident, "workflow_id": 7, "path": ".github/workflows/ci.yml",
        "head_sha": sha, "head_branch": "main" if event == "push" else BRANCH,
        "event": event, "status": "completed", "conclusion": "success",
        "pull_requests": [{"number": 27}] if event == "pull_request" else [],
        "run_attempt": 1, "updated_at": "2026-10-02T07:00:00Z",
        "html_url": f"https://github.com/{gate.REPO}/actions/runs/{ident}",
    }


class FakeAPI:
    def __init__(self):
        self.calls = []
        self.pr = {
            "state": "open", "head": {"sha": HEAD, "ref": BRANCH, "repo": {"full_name": gate.REPO}},
            "base": {"sha": BASE, "ref": "main"}, "merge_commit_sha": "c" * 40,
        }
        self.main_sha = BASE
        self.comparison = "ahead"
        self.runs = {10: run(), 20: run(20, "workflow_dispatch"), 30: run(30, "push", BASE)}
        self.job_sets = {10: jobs(), 20: jobs("full"), 30: jobs("full")}
        self.change = None

    def __call__(self, path):
        self.calls.append(path)
        if self.change:
            changed = self.change(path, self.calls.count(path))
            if changed is not None:
                return copy.deepcopy(changed)
        if path == "branches/main":
            return {"commit": {"sha": self.main_sha}}
        if path == "pulls/27":
            return copy.deepcopy(self.pr)
        if path.startswith("compare/"):
            return {"status": self.comparison}
        if path == "actions/workflows/ci.yml":
            return {"id": 7, "path": ".github/workflows/ci.yml", "state": "active"}
        if path.startswith("actions/workflows/7/runs?"):
            event = path.split("event=")[1].split("&")[0]
            sha = path.split("head_sha=")[1].split("&")[0]
            return {"workflow_runs": copy.deepcopy([
                row for row in self.runs.values() if row["event"] == event and row["head_sha"] == sha
            ])}
        if path.startswith("actions/runs/"):
            ident = int(path.split("/")[2])
            if "/jobs?" in path:
                return {"jobs": copy.deepcopy(self.job_sets[ident])}
            return copy.deepcopy(self.runs[ident])
        raise AssertionError(f"Unexpected request: {path}")


class PilotCIGateTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeAPI()

    def test_accepts_complete_current_pr(self):
        result = gate.verify_pr(self.api, 27, HEAD)
        self.assertEqual(result["head_sha"], HEAD)
        self.assertEqual(result["evidence"][0]["tier"], "fast")

    def test_accepts_full_dispatch_only_in_addition_to_pr(self):
        result = gate.verify_pr(self.api, 27, HEAD, 20)
        self.assertEqual([item["tier"] for item in result["evidence"]], ["fast", "full"])

    def test_accepts_latest_main_full_regression(self):
        self.assertEqual(gate.verify_main(self.api)["main_sha"], BASE)

    def test_rejects_stale_recorded_head(self):
        with self.assertRaisesRegex(gate.GateError, "recorded revision"):
            gate.verify_pr(self.api, 27, "d" * 40)

    def test_rejects_wrong_pr_repository_state_or_target(self):
        for field in ("repo", "state", "base"):
            with self.subTest(field=field):
                api = FakeAPI()
                if field == "repo":
                    api.pr["head"]["repo"]["full_name"] = "other/repo"
                elif field == "state":
                    api.pr["state"] = "closed"
                else:
                    api.pr["base"]["ref"] = "old-branch"
                with self.assertRaises(gate.GateError):
                    gate.verify_pr(api, 27, HEAD)

    def test_rejects_outdated_or_diverged_base(self):
        for status in ("behind", "diverged", None):
            self.api.comparison = status
            with self.subTest(status=status), self.assertRaises(gate.GateError):
                gate.verify_pr(self.api, 27, HEAD)

    def test_missing_run_is_not_success(self):
        del self.api.runs[10]
        with self.assertRaisesRegex(gate.GateError, "No matching CI run"):
            gate.verify_pr(self.api, 27, HEAD)

    def test_latest_failed_or_pending_run_does_not_reuse_old_green(self):
        for status, conclusion in (("completed", "failure"), ("in_progress", None)):
            api = FakeAPI()
            api.runs[11] = {**run(11), "status": status, "conclusion": conclusion}
            api.job_sets[11] = jobs()
            with self.subTest(status=status), self.assertRaises(gate.GateError):
                gate.verify_pr(api, 27, HEAD)

    def test_rejects_missing_duplicate_skipped_or_neutral_required_job(self):
        for change in ("missing", "duplicate", "skipped", "neutral"):
            api = FakeAPI()
            if change == "missing":
                api.job_sets[10].pop()
            elif change == "duplicate":
                api.job_sets[10].append(copy.deepcopy(api.job_sets[10][0]))
            else:
                api.job_sets[10][0]["conclusion"] = change
            with self.subTest(change=change), self.assertRaises(gate.GateError):
                gate.verify_pr(api, 27, HEAD)

    def test_rejects_skipped_selected_backend_step_even_with_green_job(self):
        self.api.job_sets[10][0]["steps"][-1]["conclusion"] = "skipped"
        with self.assertRaisesRegex(gate.GateError, "Fast backend"):
            gate.verify_pr(self.api, 27, HEAD)

    def test_unselected_tier_may_be_skipped(self):
        self.api.job_sets[10][0]["steps"].append({**completed(gate.FULL), "conclusion": "skipped"})
        gate.verify_pr(self.api, 27, HEAD)

    def test_rejects_missing_frontend_test_or_build(self):
        for step in ("Run frontend tests", "TypeScript check and production build"):
            api = FakeAPI()
            api.job_sets[10][1]["steps"] = [item for item in api.job_sets[10][1]["steps"] if item["name"] != step]
            with self.subTest(step=step), self.assertRaises(gate.GateError):
                gate.verify_pr(api, 27, HEAD)

    def test_full_dispatch_must_have_run_the_full_step(self):
        self.api.job_sets[20] = jobs("fast")
        with self.assertRaisesRegex(gate.GateError, "Full backend"):
            gate.verify_pr(self.api, 27, HEAD, 20)

    def test_full_dispatch_cannot_replace_missing_pr_ci(self):
        del self.api.runs[10]
        with self.assertRaises(gate.GateError):
            gate.verify_pr(self.api, 27, HEAD, 20)

    def test_rejects_full_run_for_wrong_revision_workflow_branch_or_event(self):
        changes = {"head_sha": BASE, "workflow_id": 999, "head_branch": "other", "event": "push", "path": "other.yml"}
        for key, value in changes.items():
            api = FakeAPI()
            api.runs[20][key] = value
            with self.subTest(key=key), self.assertRaises(gate.GateError):
                gate.verify_pr(api, 27, HEAD, 20)

    def test_rejects_run_for_another_pr(self):
        self.api.runs[10]["pull_requests"] = [{"number": 999}]
        with self.assertRaises(gate.GateError):
            gate.verify_pr(self.api, 27, HEAD)

    def test_rejects_older_full_dispatch_when_newer_one_exists(self):
        self.api.runs[21] = {**run(21, "workflow_dispatch"), "conclusion": "failure"}
        with self.assertRaisesRegex(gate.GateError, "latest dispatch"):
            gate.verify_pr(self.api, 27, HEAD, 20)

    def test_rejects_pr_head_changing_during_inspection(self):
        def change(path, count):
            if path == "pulls/27" and count == 2:
                updated = copy.deepcopy(self.api.pr)
                updated["head"]["sha"] = "e" * 40
                return updated
        self.api.change = change
        with self.assertRaisesRegex(gate.GateError, "PR changed"):
            gate.verify_pr(self.api, 27, HEAD)

    def test_rejects_main_changing_during_inspection(self):
        def change(path, count):
            if path == "branches/main" and count == 2:
                return {"commit": {"sha": "e" * 40}}
        self.api.change = change
        with self.assertRaisesRegex(gate.GateError, "Main changed"):
            gate.verify_main(self.api)

    def test_rejects_rerun_started_during_job_inspection(self):
        def change(path, count):
            if path == "actions/runs/10" and count == 2:
                return {**self.api.runs[10], "run_attempt": 2, "status": "in_progress", "conclusion": None}
        self.api.change = change
        with self.assertRaisesRegex(gate.GateError, "CI run changed"):
            gate.verify_pr(self.api, 27, HEAD)

    def test_rejects_new_same_sha_run_appearing_at_end(self):
        def change(path, count):
            if path.startswith("actions/workflows/7/runs?") and "event=pull_request" in path and count == 2:
                self.api.runs[11] = {**run(11), "status": "in_progress", "conclusion": None}
        self.api.change = change
        with self.assertRaisesRegex(gate.GateError, "newer PR run"):
            gate.verify_pr(self.api, 27, HEAD)

    def test_pagination_fetches_all_jobs(self):
        calls = []
        def api(path):
            calls.append(path)
            return {"jobs": [{}] * (100 if path.endswith("page=1") else 2)}
        self.assertEqual(len(gate.paged(api, "jobs?filter=latest", "jobs")), 102)
        self.assertEqual(len(calls), 2)

    def test_missing_or_unbounded_pagination_refuses_acceptance(self):
        with self.assertRaises(gate.GateError):
            gate.paged(lambda path: {}, "jobs", "jobs")
        with self.assertRaisesRegex(gate.GateError, "pagination bound"):
            gate.paged(lambda path: {"jobs": [{}] * 100}, "jobs", "jobs")


if __name__ == "__main__":
    unittest.main()
