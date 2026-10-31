#!/usr/bin/env python3
"""Run a JobFinderOS Claude Code skill locally."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_TIMEOUT_SEC = 1800
DEFAULT_ALLOWED_TOOLS = "Agent,Bash,Read,Write,Edit,Glob,Grep,WebFetch,WebSearch"


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def append_run_log(root: Path, label: str, phase: str) -> None:
    line = (
        f"- {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}"
        f" — {label} — {phase}\n"
    )

    log = log_dir(root) / "launchd-runs.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a", encoding="utf-8") as file:
        file.write(line)

    if os.environ.get("JOBFINDEROS_LOG_DIR"):
        return      # a redirected log (tests, ad-hoc runs) never touches the vault mirror
    vault_log = root / "vault" / "Automation" / "JobFinderOS — Schedule & Run Log.md"
    try:
        with open(vault_log, "a", encoding="utf-8") as file:
            file.write(line)
    except OSError:
        pass


def log_dir(root: Path) -> Path:
    """logs/ under the repo, or $JOBFINDEROS_LOG_DIR when set."""
    configured = os.environ.get("JOBFINDEROS_LOG_DIR")
    return Path(configured).expanduser() if configured else root / "logs"


def record_result(root: Path, label: str, stdout: str, stderr: str) -> dict:
    """Save the skill's text to logs/skills/ and pull tokens + estimated cost out of the
    CLI's JSON result (see tests/data/claude_result_sample.json for the shape)."""
    import json

    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    out_dir = log_dir(root) / "skills"
    out_dir.mkdir(parents=True, exist_ok=True)
    usage: dict = {}
    text = stdout or ""
    try:
        payload = json.loads(text.strip() or "{}")
    except ValueError:
        payload = {}
    if isinstance(payload, dict) and "result" in payload:
        text = str(payload.get("result") or "")
        u = payload.get("usage") or {}
        usage = {
            "is_error": bool(payload.get("is_error")),
            "error": str(payload.get("terminal_reason") or "") if payload.get("is_error") else "",
            "cost_usd": float(payload.get("total_cost_usd") or 0.0),
            "input_tokens": int(u.get("input_tokens") or 0) + int(u.get("cache_read_input_tokens") or 0) + int(u.get("cache_creation_input_tokens") or 0),
            "output_tokens": int(u.get("output_tokens") or 0),
            "num_turns": int(payload.get("num_turns") or 0),
        }
        usage["summary"] = (f"est. ${usage['cost_usd']:.2f}, {usage['input_tokens']} in / {usage['output_tokens']} out tokens, "
                            f"{usage['num_turns']} turns")
    with open(out_dir / f"{stamp}-{label}.txt", "w", encoding="utf-8") as fh:
        fh.write(text)
        if stderr:
            fh.write("\n\n--- stderr ---\n" + stderr)
    return usage


def find_claude() -> str | None:
    configured = os.environ.get("CLAUDE_BIN")
    if configured:
        return configured

    return shutil.which("claude")


def main() -> int:
    if len(sys.argv) != 3:
        print(
            "Usage: jobfinderos_run_skill.py <log-label> <skill-name>",
            file=sys.stderr,
        )
        return 2

    label = sys.argv[1]
    skill = sys.argv[2]
    root = project_root()

    command_file = root / ".claude" / "commands" / f"{skill}.md"
    if not command_file.is_file():
        print(
            f"JobFinderOS_run_skill: missing skill file: {command_file}",
            file=sys.stderr,
        )
        return 2

    claude_bin = find_claude()
    if not claude_bin:
        print(
            "JobFinderOS_run_skill: claude CLI not found " "(set CLAUDE_BIN)",
            file=sys.stderr,
        )
        return 127

    try:
        timeout_sec = int(
            os.environ.get(
                "JOBFINDEROS_SKILL_TIMEOUT_SEC",
                str(DEFAULT_TIMEOUT_SEC),
            )
        )
    except ValueError:
        print(
            "JOBFINDEROS_SKILL_TIMEOUT_SEC must be an integer",
            file=sys.stderr,
        )
        return 2

    allowed_tools = os.environ.get(
        "JOBFINDEROS_SKILL_ALLOWED_TOOLS",
        DEFAULT_ALLOWED_TOOLS,
    )

    env = os.environ.copy()

    path_entries = [
        str(root / ".venv" / ("Scripts" if os.name == "nt" else "bin")),
    ]

    if os.name != "nt":
        path_entries.append(str(Path.home() / ".local" / "bin"))

    path_entries.append(env.get("PATH", ""))
    env["PATH"] = os.pathsep.join(path_entries)

    # Explicit tools and a permission mode instead of --dangerously-skip-permissions: a
    # scheduled run reads untrusted web pages and email with nobody watching. acceptEdits
    # lets the skill write the vault; anything outside the allowed tools is refused.
    command = [
        claude_bin,
        "-p",
        f"/{skill}",
        "--output-format",
        "json",
        "--allowedTools",
        allowed_tools,
        "--permission-mode",
        os.environ.get("JOBFINDEROS_PERMISSION_MODE", "acceptEdits"),
        "--permission-prompts",
        "none",
        "--no-session-persistence",
    ]

    append_run_log(root, label, "start")

    try:
        result = subprocess.run(
            command,
            cwd=root,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired:
        append_run_log(root, label, f"failed (timeout {timeout_sec}s)")
        return 124
    except OSError as exc:
        append_run_log(root, label, f"failed ({exc})")
        print(f"JobFinderOS_run_skill: {exc}", file=sys.stderr)
        return 127

    usage = record_result(root, label, result.stdout, result.stderr)

    if result.returncode != 0 or usage.get("is_error"):
        append_run_log(
            root,
            label,
            f"failed (exit {result.returncode}{', ' + usage['error'] if usage.get('error') else ''})",
        )
        return result.returncode or 1

    append_run_log(root, label, f"completed ({usage.get('summary', 'no usage data')})")

    fixer = root / "scripts" / "jobfinderos_fix_backslash_paths.py"
    subprocess.run(
        [sys.executable, str(fixer)],
        cwd=root,
        env=env,
        check=False,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
