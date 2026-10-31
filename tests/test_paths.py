import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jfgraph import paths
from jfgraph.paths import Paths, WriteOutsideAllowedRoots


class StateDirTests(unittest.TestCase):
    def test_env_var_overrides_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "state"
            with mock.patch.dict(os.environ, {paths.ENV_STATE_DIR: str(target)}):
                self.assertEqual(paths.state_dir(), target)
                self.assertTrue(target.is_dir())

    def test_default_is_home_dot_jobfinderos_without_creating(self):
        with mock.patch.dict(os.environ, {paths.ENV_STATE_DIR: ""}):
            d = paths.state_dir(create=False)
        self.assertEqual(d, Path.home() / ".jobfinderos")


class SimGuardTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        # resolve(): on macOS tempdirs live under /var, a symlink to /private/var
        self.scratch = (Path(self._tmp.name) / "scratch").resolve()
        self.scratch.mkdir()
        self.p = Paths.sim(self.scratch)

    def tearDown(self):
        self._tmp.cleanup()

    def test_layout_lives_under_scratch(self):
        for d in (self.p.config_dir, self.p.vault_dir, self.p.logs_dir, self.p.state_dir, self.p.runs_dir):
            self.assertTrue(str(d).startswith(str(self.scratch)), d)
        self.p.ensure_layout()
        self.assertTrue(self.p.vault_dir.is_dir())

    def test_write_inside_scratch_allowed_and_parents_created(self):
        target = self.p.vault_dir / "Market Intel" / "ATS Inbox.md"
        with self.p.open_write(target) as fh:
            fh.write("hi")
        self.assertEqual(target.read_text(), "hi")

    def test_write_outside_scratch_refused(self):
        outside = Path(self._tmp.name) / "elsewhere.txt"
        with self.assertRaises(WriteOutsideAllowedRoots):
            self.p.assert_writable(outside)
        with self.assertRaises(WriteOutsideAllowedRoots):
            self.p.open_write(Path.home() / ".jobfinderos" / "ats_seen.json")
        self.assertFalse(outside.exists())

    def test_symlink_escape_refused(self):
        outside_dir = Path(self._tmp.name) / "outside"
        outside_dir.mkdir()
        link = self.scratch / "link"
        link.symlink_to(outside_dir)
        with self.assertRaises(WriteOutsideAllowedRoots):
            self.p.assert_writable(link / "escaped.txt")

    def test_read_mode_rejected_by_open_write(self):
        with self.assertRaises(ValueError):
            self.p.open_write(self.p.vault_dir / "x.md", mode="r")


class LiveGuardTests(unittest.TestCase):
    def test_live_allows_vault_logs_config_state_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {paths.ENV_STATE_DIR: str(Path(tmp) / "state")}):
                p = Paths.live()
            self.assertEqual(p.root, paths.project_root())
            p.assert_writable(p.vault_dir / "Dashboard.md")
            p.assert_writable(p.state_dir / "ats_seen.json")
            p.assert_writable(p.logs_dir / "x.log")
            with self.assertRaises(WriteOutsideAllowedRoots):
                p.assert_writable(p.root / "README.md")
            with self.assertRaises(WriteOutsideAllowedRoots):
                p.assert_writable(p.root / "scripts" / "x.py")


if __name__ == "__main__":
    unittest.main()
