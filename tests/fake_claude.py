#!/usr/bin/env python3
"""A stand-in for the `claude` binary so the test suite and dry simulations spend no tokens.

Point `CLAUDE_BIN` at this file. It accepts the same flags jfgraph/llm.py passes, reads
the prompt from stdin, and prints a result JSON shaped like the real CLI's
(see tests/data/claude_result_sample.json).

How it decides what to answer, in order:
  1. FAKE_CLAUDE_MODE=error            -> is_error result ("fake error")
     FAKE_CLAUDE_MODE=exit:<n>         -> exit with code n, no JSON
     FAKE_CLAUDE_MODE=sleep:<seconds>  -> sleep, then answer normally (for timeout tests)
  2. FAKE_CLAUDE_RESPONSE (JSON text)  -> used as structured_output (or result if a string)
  3. The prompt's first line `# jfgraph-prompt: <name> v<version>` selects
     tests/data/fake_responses/<name>.json. That file is either one response object or a
     list of {"when": "<substring of prompt>", "response": {...}} rules, first match wins.
  4. Otherwise: with --json-schema, a minimal object that satisfies the schema's required
     string/number/boolean properties; without it, the text "ok".

Every invocation appends {"argv": [...], "prompt": "..."} to FAKE_CLAUDE_LOG (JSONL) when
that variable is set, so tests can assert on the flags the client used.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROMPT_HEADER_RE = re.compile(r"^#\s*jfgraph-prompt:\s*(?P<name>[\w.-]+)\s+v(?P<ver>[\w.-]+)", re.M)


def parse_args(argv: list[str]) -> dict:
    opts: dict = {"schema": None, "model": None, "output_format": "text", "tools": None, "prompt_arg": None}
    i = 0
    while i < len(argv):
        a = argv[i]
        nxt = argv[i + 1] if i + 1 < len(argv) else None
        if a == "--json-schema":
            opts["schema"] = json.loads(nxt or "{}")
            i += 2
        elif a == "--model":
            opts["model"] = nxt
            i += 2
        elif a == "--output-format":
            opts["output_format"] = nxt
            i += 2
        elif a == "--tools":
            opts["tools"] = nxt
            i += 2
        elif a in ("-p", "--print"):
            if nxt is not None and not nxt.startswith("-"):
                opts["prompt_arg"] = nxt
                i += 2
            else:
                i += 1
        elif a in ("--allowedTools", "--allowed-tools", "--permission-mode", "--permission-prompts",
                   "--max-budget-usd", "--add-dir", "--append-system-prompt", "--system-prompt"):
            i += 2
        else:
            i += 1
    return opts


def minimal_for_schema(schema: dict) -> object:
    t = schema.get("type")
    if t == "object" or "properties" in schema:
        props = schema.get("properties", {})
        required = schema.get("required", list(props))
        return {k: minimal_for_schema(props.get(k, {})) for k in required}
    if t == "array":
        return []
    if t == "number" or t == "integer":
        return 0
    if t == "boolean":
        return False
    if "enum" in schema:
        return schema["enum"][0]
    return "ok"


def canned_response(prompt: str) -> object | None:
    m = PROMPT_HEADER_RE.search(prompt)
    if not m:
        return None
    path = HERE / "data" / "fake_responses" / f"{m.group('name')}.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        for rule in data:
            if rule.get("when", "") in prompt:
                return rule.get("response")
        return None
    return data


def main() -> int:
    argv = sys.argv[1:]
    opts = parse_args(argv)
    prompt = opts["prompt_arg"] if opts["prompt_arg"] is not None else sys.stdin.read()

    log_path = os.environ.get("FAKE_CLAUDE_LOG")
    if log_path:
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"argv": argv, "prompt": prompt}) + "\n")

    mode = os.environ.get("FAKE_CLAUDE_MODE", "")
    if mode.startswith("exit:"):
        return int(mode.split(":", 1)[1])
    if mode.startswith("sleep:"):
        time.sleep(float(mode.split(":", 1)[1]))

    started = time.time()
    is_error = mode == "error"
    structured: object | None = None
    text: str

    if is_error:
        text = "fake error"
    else:
        override = os.environ.get("FAKE_CLAUDE_RESPONSE")
        if override:
            structured = json.loads(override)
        else:
            structured = canned_response(prompt)
        if structured is None and opts["schema"] is not None:
            structured = minimal_for_schema(opts["schema"])
        if isinstance(structured, str):
            text, structured = structured, None
        elif structured is not None:
            text = json.dumps(structured, separators=(",", ":"))
        else:
            text = "ok"

    in_tok = max(1, len(prompt) // 4)
    out_tok = max(1, len(text) // 4)
    result = {
        "type": "result",
        "subtype": "success",
        "is_error": is_error,
        "terminal_reason": "api_error" if is_error else "completed",
        "result": text,
        "total_cost_usd": 0.0 if is_error else round(in_tok * 3e-6 + out_tok * 1.5e-5, 6),
        "duration_ms": int((time.time() - started) * 1000) + 5,
        "num_turns": 1,
        "session_id": str(uuid.uuid4()),
        "usage": {
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
        "modelUsage": {},
        "permission_denials": [],
        "fake": True,
        "fake_model": opts["model"],
    }
    if structured is not None and not is_error:
        result["structured_output"] = structured

    if opts["output_format"] == "json":
        sys.stdout.write(json.dumps(result))
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
