import unittest

from jfgraph.report import evaluate_gates, graph_stats, render_benchmark_section

GATES = {"baseline_noise_jaccard": 0.5, "gates": {
    "G-B": {"graph": "B", "vs": "A", "recall_delta_min": 0.15, "precision_delta_min": -0.05, "cost_per_tp_ratio_max": 0.5, "jaccard_min": 0.85,
            "act_precision_delta_min": -0.05, "act_jaccard_min": 0.85},
    "G-C": {"graph": "C", "vs": "B", "recall_delta_min": 0.05, "precision_delta_min": -0.03, "cost_per_tp_ratio_max": 1.5, "term_rejection_rate_max": 0.2},
    "G-discover": {"correct_rate_min": 0.8, "wrong_board_rate_max": 0.02, "mean_probes_max": 8},
}}
LABELS = {"a": "fit", "b": "fit", "c": "fit", "d": "fit", "e": "not_fit", "f": "borderline"}


def run(graph, run_id, surfaced, cost, act=()):
    return {"run_id": run_id, "graph": graph, "info": {"status": "ok"}, "dir": None,
            "state": {"surfaced": [{"posting": {"req_id": x}} for x in surfaced], "act_now": [{"posting": {"req_id": x}} for x in act],
                      "node_metrics": [{"node": "x", "seq": 1, "llm": {"cost_usd": cost, "input_tokens": 10, "output_tokens": 1}}]}}


class GateTests(unittest.TestCase):
    def test_stats_and_gates(self):
        runs = [run("A", "a1", ["a"], 0.8, act=["a"]), run("A", "a2", ["b", "e"], 0.8, act=["b"]), run("A", "a3", ["a", "b"], 0.8, act=["a"]),
                run("B", "b1", ["a", "b", "c", "e"], 0.3, act=["a", "b"]), run("B", "b2", ["a", "b", "c", "f"], 0.3, act=["a", "b"]),
                run("C", "c1", ["a", "b", "c", "d"], 0.6, act=["a", "b", "d"])]
        stats = graph_stats(runs, LABELS)
        self.assertEqual(stats["B"]["act_precision_mean"], 1.0)      # the log band carries an FP, the act-now set does not
        self.assertEqual(stats["B"]["act_jaccard"], 1.0)
        self.assertLess(stats["B"]["jaccard"], 1.0)
        self.assertAlmostEqual(stats["A"]["recall_mean"], (0.25 + 0.25 + 0.5) / 3, places=3)
        self.assertEqual(stats["A"]["recall_best"], 0.5)
        self.assertLess(stats["A"]["jaccard"], 0.5)          # noisy baseline
        rows = evaluate_gates(stats, GATES, discover={"correct_rate": 1.0, "wrong_board_rate": 0.0, "mean_probes": 2.0})
        by = {(r["gate"], r["check"]): r["verdict"] for r in rows}
        self.assertEqual(by[("G-B", "recall (vs A mean)")], "PASS")
        self.assertEqual(by[("G-B", "recall (vs A best run)")], "PASS")          # 0.75 - 0.5 = 0.25 >= 0.15
        self.assertEqual(by[("G-B", "cost per TP (vs A mean)")], "PASS")
        self.assertEqual(by[("G-B", "act-now precision (vs A mean)")], "PASS")
        self.assertEqual(by[("G-B", "repeatability (all surfaced)")], "FAIL")    # 0.6 on the log band
        self.assertEqual(by[("G-B", "repeatability (act-now)")], "PASS")
        self.assertEqual(by[("G-C", "recall (vs B mean)")], "PASS")              # 1.0 - 0.75
        self.assertEqual(by[("G-C", "cost per TP (vs B mean)")], "PASS")
        self.assertEqual(by[("G-C", "learned-term rejection rate")], "n/a")
        self.assertEqual(by[("G-discover", "wrong_board_rate")], "PASS")
        broken = evaluate_gates(stats, GATES, discover={"correct_rate": 0.5, "wrong_board_rate": 0.1, "mean_probes": 12})
        self.assertEqual([r["verdict"] for r in broken if r["gate"] == "G-discover"], ["FAIL", "FAIL", "FAIL"])
        text = render_benchmark_section("Profile 1: leadership-only SE", stats, rows, LABELS, {"snapshot_date": "2026-10-31", "total_postings": 100, "boards": [{"status": "ok"}]}, None)
        self.assertIn("| B | 2 |", text)
        self.assertIn("**Gates**", text)
        self.assertNotIn("req_id", text)

    def test_missing_graph_is_na(self):
        rows = evaluate_gates({"B": {"runs": 1, "recall_mean": 0.5, "recall_best": 0.5, "precision_mean": 1, "precision_best_run": 1, "f1_mean": 0.6,
                                     "cost_mean": 0.3, "cost_per_tp_mean": 0.1, "cost_per_tp_best_run": 0.1, "jaccard": None}}, GATES)
        self.assertTrue(all(r["verdict"] == "n/a" for r in rows))


if __name__ == "__main__":
    unittest.main()
