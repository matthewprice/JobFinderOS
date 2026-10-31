"""Graph B end to end on the golden fixture with the fake claude: discovery loop, web
sweep, scoring, routing, review note, registry write-back."""

import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from jfgraph.ats_adapters import BOARD_URLS
from jfgraph.config import load_discovery_config
from jfgraph.discover import GREENHOUSE_BOARD_URL
from jfgraph.gates import parse_comp
from jfgraph.graphs import build_graph, graph_flags
from jfgraph.llm import ClaudeCLI
from jfgraph.metrics import MetricsCollector, funnel
from jfgraph.paths import Paths
from jfgraph.review import AutoPolicy
from jfgraph.runtime import NullNotifier, RunContext
from jfgraph.state import new_state
from jfgraph.transport import DictTransport

GOLDEN = Path(__file__).resolve().parent / "data" / "golden"
RESPONSES = json.loads((GOLDEN / "responses.json").read_text(encoding="utf-8"))
FAKE = str(Path(__file__).resolve().parent / "fake_claude.py")
CLOCK = lambda: datetime(2026, 10, 31, 9, 0, tzinfo=ZoneInfo("America/Chicago"))  # noqa: E731


class GraphBTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.paths = Paths.sim(Path(self._tmp.name) / "scratch")
        self.paths.ensure_layout()
        shutil.copy(GOLDEN / "boards.yaml", self.paths.config_dir / "ats_boards.yaml")
        # a profile whose ATS table names a company with no registered board (Dune -> discoverable on greenhouse)
        (self.paths.config_dir / "profile.md").write_text(
            "# P\n\n## Professional Identity\nI run SE teams.\n\n### Direct ATS URL table\n\n| Company | Tier | Careers / ATS URL | ATS |\n|---|---|---|---|\n"
            "| Dune | 2 | https://dune.example/careers | site |\n| Ember | 2 | https://ember.example/jobs | site |\n\n## Keywords (for searching)\n**Role titles to search:**\n```\nHead of SE\n```\n",
            encoding="utf-8")
        (self.paths.config_dir / "scoring_rubric.md").write_text("# Rubric\nRole fit 50%, company 50%.\n", encoding="utf-8")
        self.responses = dict(RESPONSES)
        # Dune has a greenhouse board with a matching name; Ember matches nothing
        self.responses[BOARD_URLS["greenhouse"].format(slug="dune")] = {"jobs": [
            {"id": 901, "title": "Director, Solutions Engineering", "location": {"name": "Remote - US"}, "absolute_url": "https://boards.greenhouse.io/dune/jobs/901"},
            {"id": 902, "title": "Software Engineer", "location": {"name": "Remote - US"}, "absolute_url": "https://boards.greenhouse.io/dune/jobs/902"},
            {"id": 903, "title": "Account Executive", "location": {"name": "Austin, TX"}, "absolute_url": "https://boards.greenhouse.io/dune/jobs/903"}]}
        self.responses[GREENHOUSE_BOARD_URL.format(slug="dune")] = {"name": "Dune"}
        self.responses["https://boards-api.greenhouse.io/v1/boards/dune/jobs/901"] = {"content": "<p>$240,000 - $280,000</p>"}
        self.env = {**os.environ, "CLAUDE_BIN": FAKE}
        self.env.pop("FAKE_CLAUDE_RESPONSE", None)

    def tearDown(self):
        self._tmp.cleanup()

    def run_b(self, run_id="b1", *, overrides=None, env=None, review=None):
        cfg = load_discovery_config(self.paths, overrides=overrides)
        metrics = MetricsCollector()
        llm = ClaudeCLI(self.paths.cache_dir, claude_bin=FAKE, env=env or self.env, metrics=metrics, paths=self.paths, max_live_calls=10)
        ctx = RunContext(run_id=run_id, paths=self.paths, cfg=cfg, transport=DictTransport(self.responses), llm=llm, notifier=NullNotifier(),
                         review=review or AutoPolicy("none"), clock=CLOCK, sim=True, metrics=metrics, log=lambda s: None, extra=graph_flags("B"))
        state = build_graph("B").run(ctx, new_state(run_id, "B", "sim", "2026-10-31"))
        return state, ctx

    def test_discovery_loop_scoring_and_persistence(self):
        env = {**self.env, "FAKE_CLAUDE_RESPONSE": json.dumps({"scores": [{"req_id": "acme:101", "score": 8.5, "rationale": "great", "flags": []},
                                                                           {"req_id": "dune:901", "score": 6.0, "rationale": "ok", "flags": ["comp unknown"]},
                                                                           {"req_id": "bolt:a3", "score": 3.0, "rationale": "weak", "flags": []}],
                                                                "companies": []})}
        state, ctx = self.run_b(env=env)
        nodes = [m["node"] for m in state["node_metrics"]]
        self.assertEqual(nodes[:5], ["load_context", "sync_registry", "discover_boards", "review_boards", "sync_registry"])
        self.assertIn("parallel_fetch", nodes)
        found = {r["company"]: r["status"] for r in state["discover_results"]}
        self.assertEqual(found, {"Dune": "found", "Ember": "not_found"})
        self.assertIn(("greenhouse", "dune"), {(b.get("ats"), b.get("slug")) for b in state["registry"]})
        self.assertTrue(any(p["req_id"] == "dune:901" for p in state["raw_postings"]))   # the discovered board was fetched in the same run
        scored = {c["posting"]["req_id"]: c for c in state["scored"]}
        self.assertEqual(len(scored), 8)                                                # 7 golden survivors + dune
        self.assertEqual(scored["acme:101"]["score"], 8.5)
        self.assertIn("unscored", scored["cue:l1"]["flags"][0])
        surfaced_ids = [c["posting"]["req_id"] for c in state["surfaced"]]
        self.assertNotIn("bolt:a3", surfaced_ids)                                        # 3.0 < log threshold 5
        self.assertEqual(surfaced_ids[0], "acme:101")                                    # sorted by score, unscored last
        self.assertEqual([c["posting"]["req_id"] for c in state["act_now"]], ["acme:101"])
        by = {r["node"]: r for r in funnel(state["node_metrics"])}
        self.assertEqual(by["route_by_score"]["drops"], {"below log threshold": 1})
        self.assertGreaterEqual(by["score"]["llm_calls"], 1)
        inbox = (self.paths.vault_dir / "Market Intel" / "ATS Inbox.md").read_text(encoding="utf-8")
        self.assertIn("- **Score:** 8.5/10 — great", inbox)
        self.assertIn("- **Status:** scored", inbox)
        yaml_text = (self.paths.config_dir / "ats_boards.yaml").read_text(encoding="utf-8")
        self.assertIn("slug: dune", yaml_text)
        self.assertIn("source: discover", yaml_text)
        cache = json.loads((self.paths.state_dir / "discover_cache.json").read_text(encoding="utf-8"))
        self.assertEqual(cache["ember"]["status"], "not_found")

        # second run: Ember is a known miss, Dune is registered -> no discovery pass, nothing new
        state2, _ = self.run_b("b2", env=env)
        nodes2 = [m["node"] for m in state2["node_metrics"]]
        self.assertNotIn("discover_boards", nodes2)
        self.assertEqual(state2["new_postings"], [])

    def test_ambiguous_board_goes_to_review_and_accept_enrolls(self):
        # Dune's board has a mismatched name hint -> weak confidence -> ambiguous
        self.responses[GREENHOUSE_BOARD_URL.format(slug="dune")] = {"name": "Dune Analytics Ltd"}
        state, ctx = self.run_b(review=AutoPolicy("none"))
        self.assertEqual({r["company"]: r["status"] for r in state["discover_results"]}["Dune"], "ambiguous")
        self.assertNotIn("dune", {b.get("slug") for b in state["registry"]})
        note = (self.paths.vault_dir / "Market Intel" / "Discovery Review.md").read_text(encoding="utf-8")
        self.assertIn("- [ ] `board:dune`", note)
        state2, _ = self.run_b("b2", review=AutoPolicy("accept"))
        self.assertIn("dune", {b.get("slug") for b in state2["registry"]})

    def test_block_on_pending_pauses_the_run(self):
        self.responses[GREENHOUSE_BOARD_URL.format(slug="dune")] = {"name": "Dune Analytics Ltd"}
        state, ctx = self.run_b(overrides={"review.block_on_pending": "true"})
        info = json.loads((ctx.run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(info["status"], "awaiting_human")
        self.assertEqual(info["resume_node"], "review_boards")

    def test_comp_rule_top_vs_midpoint(self):
        self.assertEqual(parse_comp("$180,000 - $230,000", 220000)[0], "under")
        self.assertEqual(parse_comp("$180,000 - $230,000", 220000, "top")[0], "clear")
        self.assertEqual(parse_comp("$150,000 - $170,000", 220000, "top")[0], "under")
        state_mid, _ = self.run_b("mid")
        (self.paths.state_dir / "ats_seen.json").unlink()      # fresh seen-set, or the second run sees nothing new
        state_top, _ = self.run_b("top", overrides={"gates.comp_rule": "top"})
        self.assertEqual(len(state_mid["comp_pass"]), 8)
        self.assertEqual(len(state_top["comp_pass"]), 8)       # no golden range straddles the floor; the rule is wired, the unit test covers the semantics


if __name__ == "__main__":
    unittest.main()
