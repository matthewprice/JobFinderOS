import json
import os
import shutil
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from jfgraph.config import Filters, load_discovery_config, load_yaml
from jfgraph.graphs import build_graph, graph_flags
from jfgraph.llm import ClaudeCLI
from jfgraph.metrics import MetricsCollector, funnel
from jfgraph.paths import Paths
from jfgraph.review import AutoPolicy
from jfgraph.runtime import NullNotifier, RunContext
from jfgraph.signals import about_to_hire_rows, derived_signals, hiring_signal_score, promote, score_all
from jfgraph.state import new_state
from jfgraph.transport import DictTransport

GOLDEN = Path(__file__).resolve().parent / "data" / "golden"
RESPONSES = json.loads((GOLDEN / "responses.json").read_text(encoding="utf-8"))
FAKE = str(Path(__file__).resolve().parent / "fake_claude.py")
FILTERS = Filters.from_dict(load_yaml(GOLDEN / "boards.yaml")["filters"])
CLOCK = lambda: datetime(2026, 10, 31, 9, 0, tzinfo=ZoneInfo("America/Chicago"))  # noqa: E731
TODAY = date(2026, 10, 31)


class SignalMathTests(unittest.TestCase):
    def test_score_decays_and_weights(self):
        sig = [{"date": "2026-10-31", "company": "Bolt", "type": "exec_hire", "strength": 1},
               {"date": "2026-09-16", "company": "Bolt", "type": "funding", "strength": 1},      # 45 days old -> half weight
               {"date": "2026-10-31", "company": "Bolt", "type": "layoff", "strength": 1},
               {"date": "2026-10-31", "company": "Cue", "type": "rename", "strength": 0.5}]
        self.assertAlmostEqual(hiring_signal_score("Bolt", sig, TODAY), 4 + 1.5 - 4, places=3)
        self.assertAlmostEqual(hiring_signal_score("cue", sig, TODAY), 1.0, places=3)
        scores = score_all(sig, ["Acme"], TODAY, {})
        self.assertEqual(scores["Acme"], 0.0)

    def test_promote_and_demote(self):
        reg = [{"company": "Bolt", "ats": "ashby", "slug": "bolt", "tier": "2"}, {"company": "Acme", "ats": "greenhouse", "slug": "acme", "tier": "1"}]
        reg2, promoted, demoted = promote(reg, {"Bolt": 6.0, "Acme": 0.0}, threshold=5.0, ttl_days=90, today=TODAY)
        self.assertEqual(promoted, ["Bolt"])
        bolt = next(b for b in reg2 if b["company"] == "Bolt")
        self.assertEqual((bolt["tier"], bolt["original_tier"], bolt["promoted_until"]), ("1", "2", "2027-01-29"))
        reg3, _, demoted = promote(reg2, {"Bolt": 1.0}, threshold=5.0, ttl_days=90, today=date(2027, 2, 1))
        self.assertEqual(demoted, ["Bolt"])
        self.assertEqual(next(b for b in reg3 if b["company"] == "Bolt")["tier"], "2")

    def test_derived_signals(self):
        raw = [{"company": "Bolt", "title": "Head of Sales Engineering", "url": "u", "posted_at": "2026-10-30T00:00:00Z"},
               {"company": "Cue", "title": "Solutions Engineer", "url": "u2"}]
        sigs = derived_signals({"Acme": 2}, raw, raw, FILTERS, "2026-10-31")
        kinds = {(s["company"], s["type"]) for s in sigs}
        self.assertIn(("Bolt", "first_posting"), kinds)
        self.assertIn(("Bolt", "exec_hire"), kinds)
        self.assertNotIn(("Cue", "first_posting"), kinds)
        self.assertEqual(derived_signals({}, raw, [], FILTERS, "2026-10-31"), [])    # no prior counts: nothing is "first"

    def test_about_to_hire_rows(self):
        rows = about_to_hire_rows([{"company": "Bolt", "ats": "ashby", "slug": "bolt", "tier": "1"}], {"Bolt": 6.0, "Zeta": 2.0, "Low": 0.5},
                                  [{"company": "Zeta", "type": "funding", "date": "2026-10-20", "source_url": "https://x"}], {"zeta": {"company": "Zeta", "status": "not_found"}})
        self.assertEqual([r["company"] for r in rows], ["Bolt", "Zeta"])
        self.assertIn("registered", rows[0]["board"])
        self.assertEqual(rows[1]["board"], "discovery: not_found")


class GraphDTests(unittest.TestCase):
    def test_promotion_changes_the_location_verdict_and_writes_the_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = Paths.sim(Path(tmp) / "scratch")
            paths.ensure_layout()
            shutil.copy(GOLDEN / "boards.yaml", paths.config_dir / "ats_boards.yaml")
            (paths.config_dir / "profile.md").write_text("# P\n\n## Professional Identity\nI run SE teams.\n", encoding="utf-8")
            with paths.open_write(paths.vault_dir / "Market Intel" / "Signals.jsonl") as fh:
                fh.write(json.dumps({"date": "2026-10-29", "company": "Bolt", "type": "funding", "strength": 1, "source_url": "https://example.com/bolt", "note": "Series C"}) + "\n")
                fh.write(json.dumps({"date": "2026-10-30", "company": "Bolt", "type": "exec_hire", "strength": 1, "source_url": "", "note": "new CRO"}) + "\n")
            cfg = load_discovery_config(paths, overrides={"web_sweep.enabled": "false", "discover.enabled": "false", "classifier.enabled": "false"})
            metrics = MetricsCollector()
            env = {**os.environ, "CLAUDE_BIN": FAKE}
            env.pop("FAKE_CLAUDE_RESPONSE", None)
            llm = ClaudeCLI(paths.cache_dir, claude_bin=FAKE, env=env, metrics=metrics, paths=paths, max_live_calls=20)
            ctx = RunContext(run_id="d1", paths=paths, cfg=cfg, transport=DictTransport(RESPONSES), llm=llm, notifier=NullNotifier(), review=AutoPolicy("none"),
                             clock=CLOCK, sim=True, metrics=metrics, log=lambda s: None, extra={**graph_flags("D"), "weekly": True})
            state = build_graph("D").run(ctx, new_state("d1", "D", "sim", "2026-10-31"))
            self.assertIn("score_signals", [m["node"] for m in state["node_metrics"]])
            self.assertEqual(state["promoted"], ["Bolt"])                      # 3 + 4 = 7 >= 5
            # Bolt's Seattle-only "Director of Customer Engineering" was a hub drop at tier 2; promoted to tier 1 it becomes a flag
            last = {d["req_id"]: d for d in state["decisions"] if d["stage"] == "location"}
            self.assertEqual(last["bolt:a1"]["outcome"], "flag")
            self.assertIn("bolt:a1", {c["posting"]["req_id"] for c in state["comp_pass"]})
            yaml_text = (paths.config_dir / "ats_boards.yaml").read_text(encoding="utf-8")
            self.assertIn("promoted_until", yaml_text)
            note = (paths.vault_dir / "Market Intel" / "About to Hire — 2026-10-31.md").read_text(encoding="utf-8")
            self.assertIn("| Bolt | 10.2 |", note)                               # 3 funding + 4 exec_hire + a derived exec_hire from the new Head of SE posting
            self.assertGreater(state["signal_scores"]["Bolt"], 7.0)
            self.assertIn("Promoted this run:** Bolt", note)
            counts = json.loads((paths.state_dir / "function_counts.json").read_text(encoding="utf-8"))["counts"]
            self.assertEqual(counts["Acme"], 5)                                 # 101, 103, 104, 106, 108 (105 is an IC override, 107 another function)
            by = {r["node"]: r for r in funnel(state["node_metrics"])}
            self.assertEqual(by["score_signals"]["out"], 1)


if __name__ == "__main__":
    unittest.main()
