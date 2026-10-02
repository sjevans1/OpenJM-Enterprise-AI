"""Real-process checks of the WSL pilot's repository boundary and host lock."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "pilot_session.sh"


@unittest.skipUnless(all(shutil.which(name) for name in ("bash", "git", "flock", "timeout")), "Linux/WSL launcher prerequisites")
class PilotSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = {**os.environ, "XDG_STATE_HOME": str(self.root / "state")}

    def repo(self, name, remote="https://github.com/sjevans1/OpenJM-Enterprise-AI.git"):
        path = self.root / name
        path.mkdir()
        subprocess.run(["git", "init", "-q", str(path)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(path), "remote", "add", "origin", remote], check=True, capture_output=True)
        return path

    def launch(self, repo, code="pass"):
        return subprocess.run(
            ["bash", str(SCRIPT), sys.executable, "-c", code],
            cwd=repo, env=self.env, text=True, capture_output=True, timeout=5,
        )

    def test_wrong_repository_cannot_run_command(self):
        repo = self.repo("other", "https://github.com/sjevans1/Workspace-Platform.git")
        result = self.launch(repo, "from pathlib import Path; Path('unexpected').touch()")
        self.assertEqual(result.returncode, 2)
        self.assertFalse((repo / "unexpected").exists())

    def test_child_failure_is_not_reported_as_success(self):
        self.assertEqual(self.launch(self.repo("one"), "raise SystemExit(7)").returncode, 7)

    def test_same_host_clones_are_serialized_and_lock_releases(self):
        first, second = self.repo("first"), self.repo("second")
        process = subprocess.Popen(
            ["bash", str(SCRIPT), sys.executable, "-c", "import sys; print('child-ready', flush=True); sys.stdin.readline()"],
            cwd=first, env=self.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
        )
        try:
            # Both messages are tiny; select bounds the handshake if launch fails.
            import select
            self.assertTrue(select.select([process.stdout], [], [], 3)[0])
            self.assertIn("Pilot lock acquired", process.stdout.readline())
            # The parent already holds the lock; child startup need not be polled.
            blocked = self.launch(second)
            self.assertEqual(blocked.returncode, 3)
            process.communicate("continue\n", timeout=5)
            self.assertEqual(process.returncode, 0)
            self.assertEqual(self.launch(second).returncode, 0)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
