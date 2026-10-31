"""Graph C: near misses get classified, confirmed ones join the pipeline, terms are learned
after two companies, and the widened regex catches the title on the next run."""

import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from jfgraph.ats_adapters import BOARD_URLS
from jfgraph.classify import LEARNED_FILENAME, NEAR_MISS_LOG, learn_terms, load_accepted_terms, load_learned, normalize_term
from jfgraph.config import Filters, load_discovery_config, load_yaml
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
FILTERS = Filters.from_dict(load_yaml(GOLDEN / "boards.yaml")["filters"])


class ClassifyUnitTests(unittest.TestCase):
    def test_normalize_term(self):
        self.assertEqual(normalize_term("Regional Director, Field Engineering", FILTERS), "field engineering")
        self.assertEqual(normalize_term("Head of Solutions Consulting", FILTERS), "solutions consulting")
        self.assertEqual(normalize_term("Director, Sales Engineering", FILTERS), "")      # already covered by the regex
        self.assertEqual(normalize_term("VP", FILTERS), "")
        self.assertEqual(normalize_term("a b c d e f", FILTERS), "")

    def test_learn_terms_two_company_rule_and_review(self):
        c1 = {"posting": {"req_id": "a:1", "company": "Acme"}, "title_term": "Field Engineering", "evidence": "e1"}
        c2 = {"posting": {"req_id": "b:1", "company": "Bolt"}, "title_term": "Director, Field Engineering", "evidence": "e2"}
        recs, changed, review = learn_terms([c1], {}, FILTERS, min_companies=2, require_review=False, today="d", run_id="r1")
        self.assertEqual(recs["field engineering"]["status"], "proposed")
        recs, changed, review = learn_terms([c2], recs, FILTERS, min_companies=2, require_review=False, today="d", run_id="r2")
        self.assertEqual(recs["field engineering"]["status"], "accepted")
        self.assertEqual(recs["field engineering"]["companies"], ["Acme", "Bolt"])
        recs2, _, review2 = learn_terms([c1, c2], {}, FILTERS, min_companies=2, require_review=True, today="d", run_id="r")
        self.assertEqual(recs2["field engineering"]["status"], "proposed")
        self.assertEqual(review2[0]["id"], "term:field-engineering")
        widened = FILTERS.with_learned(["field engineering"])
        self.assertTrue(widened.function_re.search("Regional Director, Field Engineering"))


class GraphCTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.paths = Paths.sim(Path(self._tmp.name) / "scratch")
        self.paths.ensure_layout()
        shutil.copy(GOLDEN / "boards.yaml", self.paths.config_dir / "ats_boards.yaml")
        (self.paths.config_dir / "profile.md").write_text("# P\n\n## Professional Identity\nI run SE teams.\n", encoding="utf-8")
        (self.paths.config_dir / "scoring_rubric.md").write_text("# Rubric\n", encoding="utf-8")
        # a second company (Dune, already registered via the yaml below) with two more near misses
        boards = load_yaml(self.paths.config_dir / "ats_boards.yaml")
        boards["boards"].append({"company": "Dune", "ats": "greenhouse", "slug": "dune", "tier": "2"})
        import yaml
        (self.paths.config_dir / "ats_boards.yaml").write_text(yaml.safe_dump(boards, allow_unicode=True, sort_keys=False), encoding="utf-8")
        self.responses = dict(RESPONSES)
        self.responses[BOARD_URLS["greenhouse"].format(slug="dune")] = {"jobs": [
            {"id": 950, "title": "Director of Marketing", "location": {"name": "Remote - US"}, "absolute_url": "https://boards.greenhouse.io/dune/jobs/950"},
            {"id": 951, "title": "Head of Recruiting", "location": {"name": "Remote - US"}, "absolute_url": "https://boards.greenhouse.io/dune/jobs/951"},
            {"id": 952, "title": "Software Engineer", "location": {"name": "Remote - US"}, "absolute_url": "https://boards.greenhouse.io/dune/jobs/952"}]}
        self.responses["https://boards-api.greenhouse.io/v1/boards/dune/jobs/950"] = {"content": "<p>Own demand generation.</p>"}
        self.responses["https://boards-api.greenhouse.io/v1/boards/dune/jobs/951"] = {"content": "<p>Run recruiting.</p>"}
        self.env = {**os.environ, "CLAUDE_BIN": FAKE}
        self.env.pop("FAKE_CLAUDE_RESPONSE", None)

    def tearDown(self):
        self._tmp.cleanup()

    def run_c(self, run_id, *, overrides=None, review=None):
        from jfgraph.classify import load_accepted_terms
        cfg = load_discovery_config(self.paths, overrides=overrides, learned_terms=load_accepted_terms(self.paths.state_dir))
        metrics = MetricsCollector()
        llm = ClaudeCLI(self.paths.cache_dir, claude_bin=FAKE, env=self.env, metrics=metrics, paths=self.paths, max_live_calls=20)
        ctx = RunContext(run_id=run_id, paths=self.paths, cfg=cfg, transport=DictTransport(self.responses), llm=llm, notifier=NullNotifier(),
                         review=review or AutoPolicy("none"), clock=CLOCK, sim=True, metrics=metrics, log=lambda s: None, extra=graph_flags("C"))
        state = build_graph("C").run(ctx, new_state(run_id, "C", "sim", "2026-10-31"))
        return state, ctx

    def test_classify_learn_and_widen(self):
        state, ctx = self.run_c("c1", overrides={"web_sweep.enabled": "false", "discover.enabled": "false"})
        near = sorted(p["req_id"] for p in state["near_misses"])
        self.assertEqual(near, ["acme:107", "dune:950", "dune:951"])
        confirmed = sorted(c["posting"]["req_id"] for c in state["resp_confirmed"])
        self.assertEqual(confirmed, ["acme:107", "dune:950"])
        self.assertTrue(all(c["match_mode"] == "responsibility" for c in state["resp_confirmed"]))
        surfaced = {c["posting"]["req_id"] for c in state["surfaced"]}
        self.assertIn("acme:107", surfaced)                       # confirmed near miss went through location + comp + score
        self.assertIn("dune:950", surfaced)
        by = {r["node"]: r for r in funnel(state["node_metrics"])}
        self.assertEqual(by["classify_responsibility"]["drops"], {"responsibility no": 1})
        self.assertEqual(by["classify_responsibility"]["llm_calls"], 1)      # three near misses, one batched call
        recs = load_learned(self.paths.state_dir / LEARNED_FILENAME)
        self.assertEqual(recs["marketing"]["status"], "accepted")       # two companies -> accepted
        self.assertEqual(sorted(recs["marketing"]["companies"]), ["Acme", "Dune"])
        self.assertEqual(load_accepted_terms(self.paths.state_dir), ["marketing"])
        log = (self.paths.state_dir / NEAR_MISS_LOG).read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(log), 3)
        inbox = (self.paths.vault_dir / "Market Intel" / "ATS Inbox.md").read_text(encoding="utf-8")
        self.assertIn("- **Match:** responsibility", inbox)

        # next run: the widened regex catches the marketing titles at the title gate, no classifier calls needed
        (self.paths.state_dir / "ats_seen.json").unlink()
        state2, _ = self.run_c("c2", overrides={"web_sweep.enabled": "false", "discover.enabled": "false"})
        self.assertEqual([p["req_id"] for p in state2["near_misses"]], ["dune:951"])
        self.assertIn("acme:107", {c["posting"]["req_id"] for c in state2["title_pass"]})
        by2 = {r["node"]: r for r in funnel(state2["node_metrics"])}
        self.assertEqual(by2["classify_responsibility"]["llm_calls"], 0)   # dune:951's verdict replays from the log

    def test_require_review_routes_terms_to_the_human(self):
        state, _ = self.run_c("c1", overrides={"web_sweep.enabled": "false", "discover.enabled": "false", "classifier.require_review": "true"})
        nodes = [m["node"] for m in state["node_metrics"]]
        self.assertIn("review_terms", nodes)
        recs = load_learned(self.paths.state_dir / LEARNED_FILENAME)
        self.assertEqual(recs["marketing"]["status"], "proposed")
        note = (self.paths.vault_dir / "Market Intel" / "Discovery Review.md").read_text(encoding="utf-8")
        self.assertIn("- [ ] `term:marketing`", note)
        (self.paths.state_dir / "ats_seen.json").unlink()
        state2, _ = self.run_c("c2", overrides={"web_sweep.enabled": "false", "discover.enabled": "false", "classifier.require_review": "true"}, review=AutoPolicy("reject"))
        recs2 = load_learned(self.paths.state_dir / LEARNED_FILENAME)
        self.assertEqual(recs2["marketing"]["status"], "rejected")


if __name__ == "__main__":
    unittest.main()
