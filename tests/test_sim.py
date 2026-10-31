import json
import os
import tempfile
import unittest
from pathlib import Path

from jfgraph import sim
from jfgraph.config import load_discovery_config
from jfgraph.metrics import pipeline_metrics, repeatability
from jfgraph.nodes import map_url_to_posting, parse_scout_output
from jfgraph.runtime import RunContext

REPO = Path(__file__).resolve().parents[1]
GOLDEN = REPO / "tests" / "data" / "golden"
FAKE = str(REPO / "tests" / "fake_claude.py")


class MetricsTests(unittest.TestCase):
    def test_pipeline_metrics_lenient_and_strict(self):
        labels = {"a": "fit", "b": "fit", "c": "not_fit", "d": "borderline", "e": "fit"}
        m = pipeline_metrics(["a", "c", "d", "zz"], ["a"], labels)
        self.assertEqual((m["tp"], m["fp"], m["fn"]), (1, 1, 2))
        self.assertAlmostEqual(m["recall"], 1 / 3, places=3)
        self.assertEqual(m["precision"], 0.5)            # d (borderline) left out of the denominator
        self.assertAlmostEqual(m["precision_strict"], 1 / 3, places=3)
        self.assertEqual(m["unlabeled_surfaced"], 1)
        self.assertEqual(m["act_precision"], 1.0)
        empty = pipeline_metrics([], [], labels)
        self.assertEqual(empty["recall"], 0.0)
        self.assertIsNone(empty["precision"])

    def test_repeatability(self):
        r = repeatability([{"a", "b"}, {"a", "c"}, {"a", "b"}])
        self.assertAlmostEqual(r["mean_jaccard"], (1 / 3 + 1 + 1 / 3) / 3, places=4)
        self.assertEqual(r["always"], ["a"])
        self.assertEqual(sorted(r["sometimes"]), ["b", "c"])


class ScoutOutputTests(unittest.TestCase):
    POSTINGS = [
        {"req_id": "acme:101", "ats": "greenhouse", "company": "Acme", "title": "Head of SE", "url": "https://boards.greenhouse.io/acme/jobs/101"},
        {"req_id": "bolt:0f3c2e6a-1111-2222-3333-444455556666", "ats": "ashby", "company": "Bolt", "title": "Head of Sales Eng", "url": "https://jobs.ashbyhq.com/bolt/0f3c2e6a-1111-2222-3333-444455556666"},
        {"req_id": "cue:9d9d9d9d-aaaa-bbbb-cccc-dddddddddddd", "ats": "lever", "company": "Cue", "title": "Head of FDE", "url": "https://jobs.lever.co/cue/9d9d9d9d-aaaa-bbbb-cccc-dddddddddddd"},
    ]

    def test_url_mapping_variants(self):
        self.assertEqual(map_url_to_posting(["https://job-boards.greenhouse.io/acme/jobs/101?gh_src=x"], self.POSTINGS)["req_id"], "acme:101")
        self.assertEqual(map_url_to_posting(["https://acme.com/careers?gh_jid=101"], self.POSTINGS)["req_id"], "acme:101")
        self.assertEqual(map_url_to_posting(["https://jobs.ashbyhq.com/bolt/0f3c2e6a-1111-2222-3333-444455556666/application"], self.POSTINGS)["company"], "Bolt")
        self.assertEqual(map_url_to_posting(["https://jobs.lever.co/cue/9d9d9d9d-aaaa-bbbb-cccc-dddddddddddd/apply"], self.POSTINGS)["company"], "Cue")
        self.assertIsNone(map_url_to_posting(["https://example.com/jobs/1"], self.POSTINGS))

    def test_parse_scout_output_splits_registry_and_outside(self):
        class Cfg:
            class thresholds:
                act = 7
        note_in = "## Overview\n- **Company:** Acme\n- **Role:** Head of SE\n- **Score:** 8/10\n- **Job URL:** https://boards.greenhouse.io/acme/jobs/101\n"
        note_low = "## Overview\n- **Company:** Cue\n- **Role:** Head of FDE\n- **Score:** 5.5/10\n- **Posting:** [Job posting](https://jobs.lever.co/cue/9d9d9d9d-aaaa-bbbb-cccc-dddddddddddd)\n"
        note_out = "## Overview\n- **Company:** Zeta\n- **Role:** VP SE\n- **Score:** 9/10\n- **Job URL:** https://zeta.example/jobs/77\n"
        ctx = RunContext(run_id="t", paths=None, cfg=Cfg(), log=lambda s: None)
        up = parse_scout_output(ctx, {"raw_postings": self.POSTINGS, "scout_notes": [{"path": "a", "text": note_in}, {"path": "b", "text": note_low}, {"path": "c", "text": note_out}]})
        self.assertEqual([c["posting"]["req_id"] for c in up["surfaced"]], ["acme:101", "cue:9d9d9d9d-aaaa-bbbb-cccc-dddddddddddd"])
        self.assertEqual([c["posting"]["req_id"] for c in up["act_now"]], ["acme:101"])
        self.assertEqual(up["surfaced_outside_registry"][0]["posting"]["company"], "Zeta")
        self.assertEqual(up["surfaced"][1]["score"], 5.5)


class InstanceLifecycleTests(unittest.TestCase):
    """init -> (snapshot from a fake transport) -> label with the fake claude -> poller run -> compare; no network, no tokens."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.sim_root = Path(self._tmp.name).resolve() / "instances"
        self.profile_dir = Path(self._tmp.name) / "profile"
        self.profile_dir.mkdir()
        (self.profile_dir / "profile.md").write_text("# P\n\n## Contact\n- x\n\n## Professional Identity\nI lead SE teams.\n\n## Target Roles (ranked)\n1. Head of SE\n\n## Keywords (for searching)\n```\nsecret terms\n```\n", encoding="utf-8")
        (self.profile_dir / "scoring_rubric.md").write_text("# R\n", encoding="utf-8")
        boards = (GOLDEN / "boards.yaml").read_text(encoding="utf-8").replace("boards:", "sim:\n  universe_title_terms: \"head|director|manager|engineer\"\n  universe_max_postings: 50\nboards_unused:")
        (self.profile_dir / "ats_boards.yaml").write_text(boards + "\nboards: []\n", encoding="utf-8")
        self.registry = Path(self._tmp.name) / "registry.yaml"
        self.registry.write_text("boards:\n  - {company: Acme, ats: greenhouse, slug: acme, tier: '1'}\n  - {company: Bolt, ats: ashby, slug: bolt, tier: '2'}\n  - {company: Cue, ats: lever, slug: cue, tier: '2'}\n", encoding="utf-8")
        self.inst = sim.Instance.at("t1", self.sim_root)
        self.env = {**os.environ, "CLAUDE_BIN": FAKE}
        self._old_env = dict(os.environ)
        os.environ.update(self.env)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._old_env)
        self._tmp.cleanup()

    def test_full_offline_cycle(self):
        meta = sim.init(self.inst, self.profile_dir, self.registry)
        self.assertEqual(meta["boards"], 3)
        self.assertTrue((self.inst.root / ".claude").is_symlink())
        self.assertTrue((self.inst.root / "CLAUDE.md").is_file())
        self.assertTrue((self.inst.root / "vault" / "Market Intel").is_dir())
        cfg = load_discovery_config(self.inst.paths)
        self.assertEqual([b["company"] for b in cfg.boards], ["Acme", "Bolt", "Cue"])
        self.assertEqual(cfg.filters.min_comp, 200000)

        # snapshot through a fake live transport (monkeypatched)
        from jfgraph.transport import DictTransport
        responses = json.loads((GOLDEN / "responses.json").read_text(encoding="utf-8"))
        original = sim.LiveTransport
        sim.LiveTransport = lambda ua: DictTransport(responses)  # type: ignore[assignment]
        try:
            m = sim.snapshot(self.inst, throttle=0, log=lambda s: None)
        finally:
            sim.LiveTransport = original
        self.assertEqual(m["total_postings"], 16)
        # core = the 13 titles matching the golden function regex (all pass seniority); adjacent adds
        # "Director of Marketing"; the two IC "Solutions Engineer" titles match neither (the golden regex
        # needs "engineering") and fail seniority, so they stay out of the universe
        self.assertEqual(m["universe"]["core_total"], 13)
        self.assertEqual(m["universe"]["adjacent_sampled"], 1)
        self.assertEqual(m["universe"]["ic_sampled"], 0)
        self.assertEqual(m["universe_count"], 14)
        self.assertTrue((self.inst.snapshot_dir / "index.jsonl").is_file())
        ids = {x["req_id"] for x in self.inst.universe()}
        self.assertIn("acme:107", ids)
        self.assertNotIn("acme:102", ids)
        uni = self.inst.universe()
        self.assertTrue(any(x["req_id"] == "acme:101" and "$230,000" in x["description"] for x in uni))

        # labeling brief never includes the search vocabulary
        brief = sim.candidate_brief((self.inst.paths.config_dir / "profile.md").read_text(encoding="utf-8"))
        self.assertIn("I lead SE teams", brief)
        self.assertNotIn("secret terms", brief)

        res = sim.label(self.inst, live=False, max_live_calls=5, log=lambda s: None)
        self.assertEqual(res["labeled"], 14)
        self.assertEqual(res["live_calls"], 1)
        fin = sim.finalize_labels(self.inst)
        self.assertEqual(fin["final"], 14)
        self.assertEqual(len(self.inst.labels()), 14)

        # poller run replays the snapshot; second identical run is free and quiet
        r = sim.run(self.inst, "poller", log=lambda s: None)
        self.assertEqual(r[0]["status"], "ok")
        self.assertEqual(r[0]["surfaced"], 7)
        out = sim.compare(self.inst)
        text = out.read_text(encoding="utf-8")
        self.assertIn("## Metrics against the labels", text)
        self.assertIn("poller", text)
        self.assertIn("```mermaid", text)
        self.assertTrue(all(str(p).startswith(str(self.inst.root)) for p in self.inst.root.rglob("*") if not p.is_symlink()))


if __name__ == "__main__":
    unittest.main()
