"""The `poller` preset must reproduce the original script's decisions on the golden fixture."""

import json
import re
import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from jfgraph.config import load_discovery_config
from jfgraph.graphs import build_graph, graph_flags
from jfgraph.metrics import funnel
from jfgraph.nodes import load_seen, seen_path
from jfgraph.paths import Paths
from jfgraph.runtime import NullNotifier, RunContext
from jfgraph.state import new_state
from jfgraph.transport import DictTransport
from jfgraph.vault_writer import inbox_path

GOLDEN = Path(__file__).resolve().parent / "data" / "golden"
EXPECTED = json.loads((GOLDEN / "expected.json").read_text(encoding="utf-8"))
RESPONSES = json.loads((GOLDEN / "responses.json").read_text(encoding="utf-8"))
CLOCK = lambda: datetime(2026, 10, 31, 9, 0, tzinfo=ZoneInfo("America/Chicago"))  # noqa: E731
ROW_FIELDS = ("req_id", "company", "ats", "title", "location", "lane", "location_class", "location_reason", "comp_status", "comp_range", "url")


class PollerParityTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.paths = Paths.sim(Path(self._tmp.name) / "scratch")
        self.paths.ensure_layout()
        shutil.copy(GOLDEN / "boards.yaml", self.paths.config_dir / "ats_boards.yaml")
        # parity is with the original script, whose comp rule was the midpoint; the live default is now "top"
        self.cfg = load_discovery_config(self.paths, overrides={"gates.comp_rule": "midpoint"})

    def tearDown(self):
        self._tmp.cleanup()

    def run_poller(self, run_id: str, **extra):
        transport = DictTransport(RESPONSES)
        ctx = RunContext(run_id=run_id, paths=self.paths, cfg=self.cfg, transport=transport, notifier=NullNotifier(),
                         clock=CLOCK, sim=True, log=lambda s: None, extra={**graph_flags("poller"), **extra})
        state = build_graph("poller").run(ctx, new_state(run_id, "poller", "sim", "2026-10-31"))
        return state, ctx, transport

    def rows(self, state):
        out = []
        for c in state["comp_pass"]:
            p = c["posting"]
            out.append({"req_id": p["req_id"], "company": p["company"], "ats": p["ats"], "title": p["title"], "location": p["location"],
                        "lane": c["lane"], "location_class": c["location_class"], "location_reason": c["location_reason"],
                        "comp_status": c["comp_status"], "comp_range": c["comp_range"], "url": p["url"]})
        return out

    def test_first_run_matches_golden(self):
        state, ctx, transport = self.run_poller("r1")
        self.assertEqual(sorted(c["posting"]["req_id"] for c in state["location_pass"]), EXPECTED["survivors_after_title_location"])
        self.assertEqual(self.rows(state), [{k: r[k] for k in ROW_FIELDS} for r in EXPECTED["survivors_after_comp"]])
        self.assertEqual(state["problems"], EXPECTED["problems"])
        self.assertEqual(sorted(set(transport.calls)), EXPECTED["http_calls"])
        self.assertEqual(state["surfaced"], state["comp_pass"])
        self.assertEqual(state["act_now"], [])

        inbox = inbox_path(self.paths).read_text(encoding="utf-8")
        self.assertIn("## 2026-10-31 — 7 new", inbox)
        entries = re.findall(r"### .*?(?=\n### |\Z)", inbox.split("## 2026-10-31 — 7 new\n\n", 1)[1], flags=re.S)
        entries = [re.sub(r"- \*\*Found:\*\* .*\n", "- **Found:** <ts>\n", e.rstrip("\n") + "\n\n") for e in entries]
        self.assertEqual(entries, EXPECTED["inbox_entries"])

        # new seen-set records every fetched posting with its last decision
        seen = load_seen(seen_path(self.paths))
        self.assertEqual(len(seen), 16)
        self.assertEqual(seen["acme:101"]["last_outcome"], "pass")
        self.assertEqual(seen["acme:102"], {"first_seen": "2026-10-31", "last_stage": "title", "last_outcome": "drop", "last_run": "r1"})
        self.assertEqual(seen["acme:108"]["last_stage"], "comp")
        self.assertEqual(ctx.notifier.sent[0][0], "5 new matching reqs")

        rows = funnel(state["node_metrics"])
        by = {r["node"]: r for r in rows}
        self.assertEqual(by["fetch_boards"]["out"], 16)
        self.assertEqual(by["gate_title"]["drops"], {"function mismatch": 1, "ic override": 1, "no seniority token": 1})
        self.assertEqual(by["gate_location"]["drops"], {"hub-only at a non-Tier-1": 2, "international only": 2})
        self.assertEqual(by["gate_comp"]["drops"], {"below comp floor": 2})
        self.assertEqual(by["finalize_unscored"]["out"], 7)
        self.assertTrue(all(str(p).startswith(str(self.paths.scratch)) for p in self.paths.scratch.rglob("*")))

    def test_second_run_is_quiet_and_reconsider_reevaluates_drops(self):
        self.run_poller("r1")
        inbox_before = inbox_path(self.paths).read_text(encoding="utf-8")
        state2, ctx2, _ = self.run_poller("r2")
        self.assertEqual(state2["new_postings"], [])
        self.assertEqual(state2["surfaced"], [])
        self.assertEqual(ctx2.notifier.sent, [])
        self.assertEqual(inbox_path(self.paths).read_text(encoding="utf-8"), inbox_before)
        by = {r["node"]: r for r in funnel(state2["node_metrics"])}
        self.assertEqual(by["diff_seen"]["drops"], {"already seen": 16})

        state3, _, _ = self.run_poller("r3", reconsider_dropped=True)
        self.assertEqual(sorted(p["req_id"] for p in state3["new_postings"]), ["acme:102", "acme:105", "acme:106", "acme:107", "acme:108", "bolt:a1", "bolt:a4", "cue:l2", "cue:l3"])
        self.assertEqual(state3["surfaced"], [])      # they all drop again

    def test_dry_run_and_seed_write_no_inbox_entries(self):
        state, _, _ = self.run_poller("dry", dry_run=True)
        self.assertEqual(len(state["surfaced"]), 7)
        self.assertFalse(inbox_path(self.paths).exists())
        self.assertFalse(seen_path(self.paths).exists())
        state, _, _ = self.run_poller("seed", seed=True)
        self.assertEqual(state["inbox_written"], 0)
        self.assertTrue(inbox_path(self.paths).read_text(encoding="utf-8").endswith("---\n\n"))
        self.assertEqual(len(load_seen(seen_path(self.paths))), 16)
        state, _, _ = self.run_poller("after-seed")
        self.assertEqual(state["surfaced"], [])

    def test_legacy_seen_list_migrates(self):
        legacy = {"seen": ["acme:101", "bolt:a2"], "count": 2, "last_run": "x", "note": "old"}
        with self.paths.open_write(seen_path(self.paths)) as fh:
            json.dump(legacy, fh)
        state, _, _ = self.run_poller("r1")
        self.assertEqual(sorted(c["posting"]["req_id"] for c in state["surfaced"]), ["acme:103", "acme:104", "bolt:a3", "cue:l1", "cue:l4"])
        seen = load_seen(seen_path(self.paths))
        self.assertEqual(seen["acme:101"]["last_stage"], "legacy")


if __name__ == "__main__":
    unittest.main()
