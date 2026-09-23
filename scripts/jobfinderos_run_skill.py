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

    log = root / "logs" / "launchd-runs.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a", encoding="utf-8") as file:
        file.write(line)

    vault_log = root / "vault" / "Automation" / "JobFinderOS — Schedule & Run Log.md"
    try:
        with open(vault_log, "a", encoding="utf-8") as file:
            file.write(line)
    except OSError:
        pass


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

    command = [
        claude_bin,
        "-p",
        f"/{skill}",
        "--allowedTools",
        allowed_tools,
        "--dangerously-skip-permissions",
    ]

    append_run_log(root, label, "start")

    try:
        result = subprocess.run(
            command,
            cwd=root,
            env=env,
            stdin=subprocess.DEVNULL,
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

    if result.returncode != 0:
        append_run_log(
            root,
            label,
            f"failed (exit {result.returncode})",
        )
        return result.returncode

    append_run_log(root, label, "completed")

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
