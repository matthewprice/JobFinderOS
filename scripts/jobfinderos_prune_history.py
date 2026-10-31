#!/usr/bin/env python3
"""
JobFinderOS - vault history retention pruner.
================================================================================
Deletes historical, write-only vault notes past their retention window. Git is
the permanent archive (every pruned file stays recoverable via `git show`);
pruning exists to keep Obsidian search and agent greps free of stale noise.

Policy:
  30 days : Daily Digests/, Archive/Daily Jobs Watch/, Archive/Daily Marketing
            Brief/, Market Intel/"Daily Marketing Brief*", Market Intel/"Market Pulse*"
  90 days : Market Intel/"Weekly Brief*", Market Intel/"Mark Follow-up*"

Safety rails:
  * Only files whose name matches the rule's prefix AND contains a parseable
    YYYY-MM-DD date are candidates. Jobs Handoff.json / ATS Inbox.md etc. are
    untouchable by construction.
  * The NEWEST file in each rule group is always kept, however old - the skills
    read "the most recent" digest/pulse, and a stalled pipeline must not lose
    its only copy.
  * Deletions are committed on their own, locally. Nothing is pulled or pushed
    unless --push is given: automation never pushes on its own (CLAUDE.md), and
    the repo may be public. --dry-run prints, touches nothing.

Flags:
  --dry-run   print what would be deleted, change nothing
  --push      after committing, pull --rebase and push origin main (opt-in)
  --no-push   accepted for compatibility; it is the default now
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore

# launchd starts with a minimal PATH - make git resolvable.
os.environ["PATH"] = ":".join([
    "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin",
    os.environ.get("PATH", ""),
])

ROOT = Path(__file__).resolve().parent.parent
RUN_LOG = ROOT / "vault" / "Automation" / "JobFinderOS — Schedule & Run Log.md"
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")

# (directory relative to ROOT, filename glob, retention days)
RULES = [
    ("vault/Daily Digests",                 "*",                        30),
    ("vault/Archive/Daily Jobs Watch",      "*",                        30),
    ("vault/Archive/Daily Marketing Brief", "*",                        30),
    ("vault/Market Intel",                  "Daily Marketing Brief*",   30),
    ("vault/Market Intel",                  "Market Pulse*",            30),
    ("vault/Market Intel",                  "Weekly Brief*",            90),
    ("vault/Market Intel",                  "Mark Follow-up*",          90),
]


def now():
    return datetime.now().astimezone()


def log(msg: str) -> None:
    line = f"[{now():%Y-%m-%d %H:%M:%S %Z}] {msg}"
    print(line, flush=True)
    try:
        logp = ROOT / "logs" / "prune.log"
        logp.parent.mkdir(parents=True, exist_ok=True)
        with open(logp, "a") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def git(*args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(ROOT), *args],
                          capture_output=True, text=True, timeout=timeout)


def file_date(name: str):
    m = DATE_RE.search(name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d").date()
    except ValueError:
        return None


def collect(today) -> list[Path]:
    """All files past retention, honoring the keep-newest-per-rule rail."""
    doomed: list[Path] = []
    for rel_dir, pattern, days in RULES:
        d = ROOT / rel_dir
        if not d.is_dir():
            continue
        cutoff = today - timedelta(days=days)
        dated = []
        for p in sorted(d.iterdir()):
            if not p.is_file() or not fnmatch.fnmatch(p.name, pattern):
                continue
            fd = file_date(p.name)
            if fd is not None:
                dated.append((fd, p))
        if not dated:
            continue
        newest = max(dated)[1]  # always kept
        for fd, p in dated:
            if p != newest and fd < cutoff:
                doomed.append(p)
                log(f"  prune ({days}d): {p.relative_to(ROOT)}  [{fd}]")
    return doomed


def append_run_log(n: int) -> None:
    try:
        with open(RUN_LOG, "a") as fh:
            fh.write(f"| {now():%Y-%m-%d %H:%M %Z} | prune-history | "
                     f"Pruned {n} vault history file(s) past retention "
                     f"(30d digests/watch/pulse, 90d weekly). Full history stays in git. |\n")
    except Exception as exc:
        log(f"WARN: run-log append failed: {exc}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--push", action="store_true", help="opt in: pull --rebase then push the deletion commit")
    ap.add_argument("--no-push", action="store_true", help="compatibility no-op; not pushing is the default")
    args = ap.parse_args()

    today = now().date()
    log(f"retention sweep (today {today}, dry_run={args.dry_run})")
    doomed = collect(today)
    if not doomed:
        log("nothing past retention")
        return 0
    if args.dry_run:
        log(f"DRY RUN - {len(doomed)} file(s) would be deleted")
        return 0

    if args.push:
        pull = git("pull", "--rebase", "--autostash", "origin", "main")
        if pull.returncode != 0:
            log(f"WARN: pull failed, continuing: {pull.stderr.strip()[:200]}")

    for p in doomed:
        try:
            p.unlink()
        except Exception as exc:
            log(f"WARN: could not delete {p.name}: {exc}")
    append_run_log(len(doomed))

    rels = [str(p.relative_to(ROOT)) for p in doomed]
    git("add", "--", str(RUN_LOG.relative_to(ROOT)), *rels)
    if git("diff", "--cached", "--quiet").returncode == 0:
        log("nothing staged; skipping commit")
        return 0
    msg = f"prune: vault history retention ({len(doomed)} files past 30/90d windows)"
    c = git("commit", "-m", msg)
    if c.returncode != 0:
        log(f"WARN: commit failed: {c.stderr.strip()[:200]}")
        return 1
    if not args.push:
        log(f"committed {len(doomed)} deletions locally (pass --push to publish)")
        return 0
    p = git("push", "origin", "main")
    if p.returncode != 0:
        log(f"WARN: push failed (will ride along with the next push): {p.stderr.strip()[:200]}")
    else:
        log(f"pruned {len(doomed)} file(s); committed + pushed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
