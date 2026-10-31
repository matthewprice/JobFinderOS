import json
import os
import tempfile
import unittest
from pathlib import Path

from jfgraph.llm import ClaudeCLI, LLMCall, LLMError, LLMResult, LiveBudgetExceeded
from jfgraph.metrics import MetricsCollector
from jfgraph.paths import Paths

FAKE = str(Path(__file__).resolve().parent / "fake_claude.py")
SAMPLE = Path(__file__).resolve().parent / "data" / "claude_result_sample.json"
SCHEMA = {"type": "object", "properties": {"fit": {"enum": ["yes", "no", "unsure"]}, "confidence": {"type": "number"}}, "required": ["fit", "confidence"]}


def call(**kw) -> LLMCall:
    base = dict(node="classify", prompt_name="classify_responsibility", prompt="Title: Head of Widgets\n", schema=SCHEMA)
    base.update(kw)
    return LLMCall(**base)


class ArgvTests(unittest.TestCase):
    def test_structured_call_has_no_tools_and_no_skip_permissions(self):
        cli = ClaudeCLI(Path("/tmp/none"), claude_bin="claude")
        argv = cli.argv(call())
        self.assertNotIn("--dangerously-skip-permissions", argv)
        self.assertNotIn("--bare", argv)
        self.assertEqual(argv[:2], ["claude", "-p"])
        self.assertIn("--output-format", argv)
        self.assertEqual(argv[argv.index("--output-format") + 1], "json")
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "dontAsk")
        self.assertIn("--json-schema", argv)
        self.assertIn("--max-budget-usd", argv)

    def test_skill_call_uses_prompt_argument_and_explicit_tools(self):
        cli = ClaudeCLI(Path("/tmp/none"), claude_bin="claude")
        c = call(node="scout_baseline", prompt_name="jobs-scout", prompt="/jobs-scout", schema=None, tools=None,
                 allowed_tools=("Read", "Write", "WebFetch"), permission_mode="acceptEdits", add_dirs=("/ws",), prompt_via_stdin=False)
        argv = cli.argv(c)
        self.assertTrue(argv[2].endswith("/jobs-scout"))
        self.assertNotIn("--tools", argv)
        self.assertEqual(argv[argv.index("--allowedTools") + 1], "Read,Write,WebFetch")
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "acceptEdits")
        self.assertEqual(argv[argv.index("--add-dir") + 1], "/ws")


class ResultParsingTests(unittest.TestCase):
    def test_pinned_sample_parses(self):
        d = json.loads(SAMPLE.read_text())
        r = LLMResult.from_cli_json(d)
        self.assertEqual(r.structured, {"answer": "ok"})
        self.assertEqual(r.text, '{"answer":"ok"}')
        self.assertAlmostEqual(r.cost_usd, 0.013938, places=6)
        self.assertEqual(r.cache_create_tokens, 3351)
        self.assertEqual(r.num_turns, 2)
        self.assertFalse(r.is_error)
        e = LLMResult.from_cli_json(d["_error_sample"])
        self.assertTrue(e.is_error)
        self.assertIn("Not logged in", e.text)


class FakeClaudeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.paths = Paths.sim(Path(self._tmp.name) / "scratch")
        self.paths.ensure_layout()
        self.log = self.paths.state_dir / "fake.log"
        self.env = {**os.environ, "FAKE_CLAUDE_LOG": str(self.log)}
        self.env.pop("FAKE_CLAUDE_MODE", None)
        self.env.pop("FAKE_CLAUDE_RESPONSE", None)

    def tearDown(self):
        self._tmp.cleanup()

    def cli(self, **kw) -> ClaudeCLI:
        base = dict(claude_bin=FAKE, paths=self.paths, env=self.env, metrics=MetricsCollector())
        base.update(kw)
        return ClaudeCLI(self.paths.cache_dir, **base)

    def invocations(self) -> list[dict]:
        return [json.loads(l) for l in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_cache_miss_then_hit_then_live_bypass_and_sample_keying(self):
        cli = self.cli()
        r1 = cli.call(call())
        self.assertFalse(r1.cached)
        self.assertEqual(r1.structured["fit"], "yes")       # minimal_for_schema picks the first enum value
        self.assertGreater(r1.input_tokens, 0)
        r2 = cli.call(call())
        self.assertTrue(r2.cached)
        self.assertEqual(len(self.invocations()), 1)
        self.assertEqual(cli.live_calls, 1)
        cli.call(call(sample=1))
        self.assertEqual(len(self.invocations()), 2)        # different sample => different key
        live = self.cli(live=True)
        live.call(call())
        self.assertEqual(len(self.invocations()), 3)        # live bypasses the cache read
        # the prompt reached the fake with the header the real model would also see
        self.assertTrue(self.invocations()[0]["prompt"].startswith("# jfgraph-prompt: classify_responsibility v1\n"))
        m = cli.metrics.completed
        self.assertEqual(m, [])                             # no node started: metrics ignored, no crash

    def test_metrics_attribution_when_node_active(self):
        cli = self.cli()
        cli.metrics.start_node("classify", "llm", 1, 2)
        cli.call(call())
        cli.call(call())
        metric = cli.metrics.finish_node(items_out=2, drops={})
        self.assertEqual(metric.llm["calls"], 2)
        self.assertEqual(metric.llm["cached"], 1)
        self.assertGreater(metric.llm["input_tokens"], 0)

    def test_budget_guard_trips_on_sixth_live_call_but_cached_calls_are_free(self):
        cli = self.cli(max_live_calls=5)
        for i in range(5):
            cli.call(call(prompt=f"posting {i}"))
        for i in range(5):
            cli.call(call(prompt=f"posting {i}"))            # cached, not counted
        with self.assertRaises(LiveBudgetExceeded) as cm:
            cli.call(call(prompt="posting 6"))
        self.assertIn("budget of 5", str(cm.exception))
        self.assertEqual(cli.live_calls, 5)
        self.assertEqual(len(self.invocations()), 5)

    def test_error_result_and_crash_paths(self):
        cli = self.cli(env={**self.env, "FAKE_CLAUDE_MODE": "error"})
        r = cli.call(call(prompt="err"))
        self.assertTrue(r.is_error)
        cli2 = self.cli(env={**self.env, "FAKE_CLAUDE_MODE": "exit:3"})
        with self.assertRaises(LLMError):
            cli2.call(call(prompt="crash"))
        cli3 = self.cli(env={**self.env, "FAKE_CLAUDE_MODE": "sleep:2"})
        with self.assertRaises(LLMError):
            cli3.call(call(prompt="slow", timeout_s=1))

    def test_canned_response_override(self):
        cli = self.cli(env={**self.env, "FAKE_CLAUDE_RESPONSE": json.dumps({"fit": "no", "confidence": 0.9})})
        self.assertEqual(cli.call(call(prompt="override")).structured, {"fit": "no", "confidence": 0.9})

    def test_cache_written_under_scratch_only(self):
        cli = self.cli()
        cli.call(call())
        files = list(self.paths.cache_dir.rglob("*.json"))
        self.assertEqual(len(files), 1)
        self.assertTrue(str(files[0]).startswith(str(self.paths.scratch)))


if __name__ == "__main__":
    unittest.main()
