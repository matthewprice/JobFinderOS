"""The portable skill runner: no --dangerously-skip-permissions, JSON output, usage in the run log.
Logs are redirected to a temp dir (JOBFINDEROS_LOG_DIR) so the repo's logs and the vault mirror stay untouched."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "scripts" / "jobfinderos_run_skill.py"
FAKE = REPO / "tests" / "fake_claude.py"


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.logs = Path(self._tmp.name) / "logs"

    def tearDown(self):
        self._tmp.cleanup()

    def run_runner(self, skill, extra_env=None):
        env = {**os.environ, "CLAUDE_BIN": str(FAKE), "JOBFINDEROS_LOG_DIR": str(self.logs), **(extra_env or {})}
        return subprocess.run([sys.executable, str(RUNNER), "test-label", skill], cwd=REPO, env=env, capture_output=True, text=True, timeout=60)

    def run_log_tail(self) -> str:
        return (self.logs / "launchd-runs.log").read_text(encoding="utf-8").splitlines()[-1]

    def test_missing_skill_exits_2_without_calling_claude(self):
        r = self.run_runner("no-such-skill")
        self.assertEqual(r.returncode, 2)
        self.assertIn("missing skill file", r.stderr)

    def test_completed_run_logs_usage_and_uses_safe_flags(self):
        argv_log = Path(self._tmp.name) / "argv.log"
        r = self.run_runner("jobs-scout", {"FAKE_CLAUDE_LOG": str(argv_log)})
        self.assertEqual(r.returncode, 0, r.stderr)
        argv = json.loads(argv_log.read_text().splitlines()[-1])["argv"]
        self.assertNotIn("--dangerously-skip-permissions", argv)
        self.assertIn("--output-format", argv)
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "acceptEdits")
        self.assertEqual(argv[argv.index("-p") + 1], "/jobs-scout")
        self.assertIn("test-label — completed (est. $", self.run_log_tail())
        self.assertTrue(any(p.name.endswith("-test-label.txt") for p in (self.logs / "skills").iterdir()))

    def test_error_result_is_a_failure(self):
        r = self.run_runner("jobs-scout", {"FAKE_CLAUDE_MODE": "error"})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("failed", self.run_log_tail())


if __name__ == "__main__":
    unittest.main()
