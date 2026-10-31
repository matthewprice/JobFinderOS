import json
import random
import tempfile
import threading
import time
import unittest
from pathlib import Path

from jfgraph.llm import LiveBudgetExceeded
from jfgraph.paths import Paths
from jfgraph.runtime import END, AwaitHuman, Graph, GraphError, RunContext, fan_out, load_run_json, load_state, parallel
from jfgraph.state import new_state


def ctx_in(tmp: str, run_id: str = "r1") -> RunContext:
    paths = Paths.sim(Path(tmp) / "scratch")
    paths.ensure_layout()
    return RunContext(run_id=run_id, paths=paths, log=lambda s: None)


def st() -> dict:
    return new_state("r1", "demo", "sim", "2026-10-31")


class LinearAndConditionalTests(unittest.TestCase):
    def test_linear_graph_merges_and_checkpoints(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = ctx_in(tmp)
            g = (Graph("demo").set_entry("a")
                 .add_node("a", lambda c, s: {"items": [1, 2, 3], "decisions": [{"req_id": "x", "stage": "title", "outcome": "drop", "reason": "ic", "node": "a"}]}, out_key="items")
                 .add_node("b", lambda c, s: {"kept": [i for i in s["items"] if i > 1]}, in_key="items", out_key="kept")
                 .add_edge("a", "b").add_edge("b", END))
            state = g.run(ctx, st())
            self.assertEqual(state["kept"], [2, 3])
            self.assertEqual(len(state["decisions"]), 1)
            metrics = state["node_metrics"]
            self.assertEqual([m["node"] for m in metrics], ["a", "b"])
            self.assertEqual(metrics[0]["items_out"], 3)
            self.assertEqual(metrics[0]["drops"], {"ic": 1})
            self.assertEqual(metrics[1]["items_in"], 3)
            cps = sorted(p.name for p in (ctx.run_dir / "checkpoints").iterdir())
            self.assertEqual(cps, ["001-a.json", "002-b.json"])
            self.assertEqual(load_run_json(ctx)["status"], "ok")
            self.assertEqual(load_state(ctx)["kept"], [2, 3])
            self.assertIn("flowchart TD", g.mermaid())

    def test_conditional_edge_and_sequence_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = ctx_in(tmp)
            trace = []
            mk = lambda n: (lambda c, s: (trace.append(n), {})[1])
            g = Graph("demo").set_entry("start")
            for n in ("start", "x", "y", "z"):
                g.add_node(n, mk(n))
            g.add_conditional_edge("start", lambda s: ["x", "y"] if s.get("two") else "z", targets=("x", "y", "z"))
            g.add_edge("x", END).add_edge("y", "z").add_edge("z", END)
            g.run(ctx, {**st(), "two": True})
            # x then y run in sequence, then continue from y's edge (-> z)
            self.assertEqual(trace, ["start", "x", "y", "z"])
            trace.clear()
            g.run(ctx_in(tmp, "r2"), st())
            self.assertEqual(trace, ["start", "z"])

    def test_loop_bounded_by_max_steps(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = ctx_in(tmp)
            g = (Graph("loop").set_entry("a")
                 .add_node("a", lambda c, s: {"n": s.get("n", 0) + 1})
                 .add_conditional_edge("a", lambda s: "a" if s["n"] < 3 else END, targets=("a", END)))
            state = g.run(ctx, st())
            self.assertEqual(state["n"], 3)
            g2 = Graph("forever").set_entry("a").add_node("a", lambda c, s: {}).add_edge("a", "a")
            with self.assertRaises(GraphError):
                g2.run(ctx_in(tmp, "r3"), st(), max_steps=5)
            self.assertEqual(load_run_json(ctx_in(tmp, "r3"))["status"], "error")

    def test_validation_errors(self):
        g = Graph("bad").add_node("a", lambda c, s: {})
        with self.assertRaises(GraphError):
            g.validate()                      # no entry
        g.set_entry("a")
        with self.assertRaises(GraphError):
            g.validate()                      # no outgoing edge
        g.add_edge("a", "nowhere")
        with self.assertRaises(GraphError):
            g.validate()


class PauseAndResumeTests(unittest.TestCase):
    def test_await_human_then_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = ctx_in(tmp)
            answered = {"yes": False}

            def human(c, s):
                if not answered["yes"]:
                    raise AwaitHuman("boards:acme")
                return {"approved": True}

            g = (Graph("h").set_entry("a").add_node("a", lambda c, s: {"x": 1})
                 .add_node("review", human, kind="human").add_node("b", lambda c, s: {"done": s.get("approved")})
                 .add_edge("a", "review").add_edge("review", "b").add_edge("b", END))
            state = g.run(ctx, st())
            info = load_run_json(ctx)
            self.assertEqual(info["status"], "awaiting_human")
            self.assertEqual(info["resume_node"], "review")
            self.assertEqual(info["review_id"], "boards:acme")
            self.assertNotIn("done", state)
            answered["yes"] = True
            state2 = g.run(ctx, load_state(ctx), resume_from="review")
            self.assertTrue(state2["done"])
            self.assertEqual(load_run_json(ctx)["status"], "ok")
            self.assertEqual([m["node"] for m in state2["node_metrics"]], ["a", "review", "review", "b"])

    def test_budget_exceeded_checkpoints_and_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = ctx_in(tmp)

            def spender(c, s):
                raise LiveBudgetExceeded("budget of 5 reached")

            g = Graph("b").set_entry("a").add_node("a", lambda c, s: {"x": 1}).add_node("llm", spender, kind="llm")
            g.add_edge("a", "llm").add_edge("llm", END)
            state = g.run(ctx, st())
            self.assertEqual(state["x"], 1)
            self.assertEqual(load_run_json(ctx)["status"], "budget_exceeded")
            self.assertEqual(state["node_metrics"][-1]["status"], "budget_exceeded")

    def test_unexpected_exception_recorded_and_reraised(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = ctx_in(tmp)
            g = Graph("e").set_entry("a").add_node("a", lambda c, s: 1 / 0).add_edge("a", END)
            with self.assertRaises(ZeroDivisionError):
                g.run(ctx, st())
            self.assertEqual(load_run_json(ctx)["status"], "error")


class CombinatorTests(unittest.TestCase):
    def test_fan_out_is_deterministic_and_isolates_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = ctx_in(tmp)

            def per_item(c, item):
                time.sleep(random.random() / 50)          # shuffle completion order
                if item == 3:
                    raise ValueError("boom")
                return {"out": [item * 10], "decisions": [{"req_id": str(item), "stage": "t", "outcome": "pass", "reason": "", "node": "fo"}]}

            node = fan_out("items", per_item, out_key="out", max_workers=4)
            g = Graph("f").set_entry("fo").add_node("fo", node, kind="fanout", in_key="items", out_key="out").add_edge("fo", END)
            results = set()
            for i in range(5):
                state = g.run(ctx_in(tmp, f"r{i}"), {**st(), "items": [1, 2, 3, 4, 5]})
                results.add(tuple(state["out"]))
                self.assertEqual(len(state["problems"]), 1)
                self.assertIn("item 2", state["problems"][0])
                self.assertEqual(state["node_metrics"][0]["items_in"], 5)
                self.assertEqual(state["node_metrics"][0]["items_out"], 4)
            self.assertEqual(results, {(10, 20, 40, 50)})

    def test_fan_out_empty_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            node = fan_out("items", lambda c, i: {"out": [i]}, out_key="out")
            self.assertEqual(node(ctx_in(tmp), {}), {"out": []})

    def test_parallel_merges_and_rejects_scalar_collisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = ctx_in(tmp)
            node = parallel({"left": lambda c, s: {"a": [1], "problems": ["l"]}, "right": lambda c, s: {"a": [2], "b": "x", "problems": ["r"]}})
            up = node(ctx, st())
            self.assertEqual(up["a"], [1, 2])
            self.assertEqual(up["b"], "x")
            self.assertEqual(up["problems"], ["l", "r"])
            bad = parallel({"l": lambda c, s: {"k": 1}, "r": lambda c, s: {"k": 2}})
            with self.assertRaises(GraphError):
                bad(ctx, st())


if __name__ == "__main__":
    unittest.main()
