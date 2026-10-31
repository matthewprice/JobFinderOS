"""The LLM client: ``claude -p`` as a subprocess, with structured output, token capture,
an on-disk record/replay cache, and a live-call budget.

No SDK, no API key: the installed Claude Code CLI and its subscription auth do the work.
No ``--dangerously-skip-permissions`` either. Structured calls get no tools at all;
calls that need tools name them explicitly and run with ``--permission-mode``.

Pinned CLI result shape: tests/data/claude_result_sample.json (Claude Code 2.1.314).
``--bare`` is deliberately absent: in a subprocess it reported "Not logged in".
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

PROMPT_HEADER = "# jfgraph-prompt: {name} v{version}\n"


class LLMError(RuntimeError):
    pass


class LiveBudgetExceeded(RuntimeError):
    """The run has used every live call it was allowed. The graph stops and checkpoints."""


@dataclass
class LLMCall:
    node: str
    prompt_name: str
    prompt: str                                   # body; the header line is added by the client
    prompt_version: str = "1"
    schema: dict | None = None                    # --json-schema
    model: str = "sonnet"
    tools: tuple[str, ...] | None = ()            # () => --tools "" (no tools); None => CLI default tool set
    allowed_tools: tuple[str, ...] = ()           # --allowedTools, for tools that would otherwise prompt
    permission_mode: str = "dontAsk"              # acceptEdits for runs that must write files
    add_dirs: tuple[str, ...] = ()
    cwd: str | None = None
    max_budget_usd: float = 0.50
    timeout_s: int = 600
    sample: int = 0                               # repeatability index; part of the cache key
    prompt_via_stdin: bool = True                 # False => the prompt is the `-p` argument (slash commands)

    def full_prompt(self) -> str:
        return PROMPT_HEADER.format(name=self.prompt_name, version=self.prompt_version) + self.prompt


@dataclass
class LLMResult:
    text: str = ""
    structured: Any = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_create_tokens: int = 0
    cost_usd: float = 0.0
    duration_ms: int = 0
    num_turns: int = 0
    session_id: str = ""
    is_error: bool = False
    cached: bool = False
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_cli_json(cls, d: dict[str, Any], *, cached: bool = False) -> "LLMResult":
        usage = d.get("usage") or {}
        return cls(
            text=str(d.get("result", "") or ""),
            structured=d.get("structured_output"),
            input_tokens=int(usage.get("input_tokens", 0) or 0),
            output_tokens=int(usage.get("output_tokens", 0) or 0),
            cache_read_tokens=int(usage.get("cache_read_input_tokens", 0) or 0),
            cache_create_tokens=int(usage.get("cache_creation_input_tokens", 0) or 0),
            cost_usd=float(d.get("total_cost_usd", 0.0) or 0.0),
            duration_ms=int(d.get("duration_ms", 0) or 0),
            num_turns=int(d.get("num_turns", 0) or 0),
            session_id=str(d.get("session_id", "") or ""),
            is_error=bool(d.get("is_error", False)),
            cached=cached,
            raw=d,
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class PromptLibrary:
    """``prompts/<name>.md`` plus ``PROMPT_VERSIONS.yaml`` (name -> version string)."""

    def __init__(self, prompts_dir: Path) -> None:
        self.dir = Path(prompts_dir)
        versions_path = self.dir / "PROMPT_VERSIONS.yaml"
        self.versions: dict[str, str] = {}
        if versions_path.is_file():
            self.versions = {k: str(v) for k, v in (yaml.safe_load(versions_path.read_text(encoding="utf-8")) or {}).items()}

    def load(self, name: str, **fields: Any) -> tuple[str, str]:
        text = (self.dir / f"{name}.md").read_text(encoding="utf-8")
        if fields:
            text = text.format(**fields)
        return text, self.versions.get(name, "1")


class ClaudeCLI:
    def __init__(self, cache_dir: Path, *, live: bool = False, claude_bin: str | None = None,
                 max_live_calls: int = 0, metrics: Any = None, paths: Any = None,
                 env: dict[str, str] | None = None) -> None:
        self.cache_dir = Path(cache_dir)
        self.live = live
        self.claude_bin = claude_bin or os.environ.get("CLAUDE_BIN") or shutil.which("claude") or "claude"
        self.max_live_calls = int(max_live_calls)
        self.metrics = metrics
        self.paths = paths
        self.env = env
        self.live_calls = 0
        self.calls: list[LLMCall] = []

    # ------------------------------------------------------------------ keys

    def cache_key(self, call: LLMCall) -> str:
        material = {
            "node": call.node, "prompt_name": call.prompt_name, "prompt_version": call.prompt_version,
            "prompt": call.prompt, "schema": call.schema, "model": call.model,
            "tools": list(call.tools) if call.tools is not None else None,
            "allowed_tools": list(call.allowed_tools), "sample": call.sample,
        }
        return hashlib.sha256(json.dumps(material, sort_keys=True).encode("utf-8")).hexdigest()[:40]

    def cache_path(self, call: LLMCall) -> Path:
        return self.cache_dir / call.node / f"{self.cache_key(call)}.json"

    # ------------------------------------------------------------------ argv

    def argv(self, call: LLMCall) -> list[str]:
        args = [self.claude_bin, "-p"]
        if not call.prompt_via_stdin:
            args.append(call.full_prompt())
        args += [
            "--output-format", "json",
            "--model", call.model,
            "--permission-mode", call.permission_mode,
            "--permission-prompts", "none",
            "--no-session-persistence",
            "--strict-mcp-config",
            "--max-budget-usd", f"{call.max_budget_usd:.2f}",
        ]
        if call.schema is not None:
            args += ["--json-schema", json.dumps(call.schema, separators=(",", ":"))]
        if call.tools is not None:
            args += ["--tools", ",".join(call.tools)]
        if call.allowed_tools:
            args += ["--allowedTools", ",".join(call.allowed_tools)]
        if call.add_dirs:
            args += ["--add-dir", *call.add_dirs]
        return args

    # ------------------------------------------------------------------ call

    def call(self, call: LLMCall) -> LLMResult:
        self.calls.append(call)
        path = self.cache_path(call)
        if not self.live and path.is_file():
            result = LLMResult.from_cli_json(json.loads(path.read_text(encoding="utf-8"))["result"], cached=True)
            self._report(result)
            return result

        if self.max_live_calls > 0 and self.live_calls >= self.max_live_calls:
            raise LiveBudgetExceeded(
                f"live call budget of {self.max_live_calls} reached before node {call.node!r} "
                f"({call.prompt_name}); raise --max-live-calls to continue"
            )
        self.live_calls += 1

        env = dict(os.environ if self.env is None else self.env)
        try:
            proc = subprocess.run(
                self.argv(call),
                input=call.full_prompt() if call.prompt_via_stdin else None,
                stdin=None if call.prompt_via_stdin else subprocess.DEVNULL,
                capture_output=True, text=True, cwd=call.cwd, env=env, timeout=call.timeout_s, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise LLMError(f"claude timed out after {call.timeout_s}s in node {call.node}") from exc
        except OSError as exc:
            raise LLMError(f"could not run {self.claude_bin}: {exc}") from exc

        payload = self._parse(proc, call)
        result = LLMResult.from_cli_json(payload)
        if not result.is_error:
            self._store(path, call, payload)      # an error (budget exhausted, not logged in) must not be replayed
        self._report(result)
        return result

    # --------------------------------------------------------------- helpers

    @staticmethod
    def _parse(proc: subprocess.CompletedProcess, call: LLMCall) -> dict[str, Any]:
        out = (proc.stdout or "").strip()
        if out:
            try:
                return json.loads(out)
            except ValueError:
                start = out.find("{")
                if start >= 0:
                    try:
                        return json.loads(out[start:])
                    except ValueError:
                        pass
        raise LLMError(
            f"claude exited {proc.returncode} without a JSON result in node {call.node}: "
            f"{(proc.stderr or out or '')[:300]}"
        )

    def _store(self, path: Path, call: LLMCall, payload: dict[str, Any]) -> None:
        record = {"call": {k: v for k, v in asdict(call).items() if k != "prompt"}, "prompt_sha": hashlib.sha256(call.prompt.encode("utf-8")).hexdigest(), "result": payload}
        if self.paths is not None:
            fh = self.paths.open_write(path)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            fh = open(path, "w", encoding="utf-8")
        with fh:
            json.dump(record, fh, indent=1)

    def _report(self, result: LLMResult) -> None:
        if self.metrics is not None:
            self.metrics.record_llm(result)
