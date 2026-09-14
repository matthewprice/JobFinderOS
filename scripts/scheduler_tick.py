#!/usr/bin/env python3
"""
Master scheduler tick — invoked every StartInterval by launchd (or manually).
Decides whether to run daily Jobs, weekly Mark+Jobs, or neither based on
config/scheduler.yaml and ~/.jobfinderos/scheduler_state.json.

Rules:
- Weekly: at most once per ISO week, after this week's scheduled weekday+time (catch-up if missed).
- Daily: at most once per local calendar day, after today's daily time — unless weekly is still
  due for the current week (block daily until weekly completes).
- Weekly wins: after a successful weekly run, skip daily that same day (state: last_daily_date = today).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

if os.name == "nt":
    import msvcrt
else:
    import fcntl

from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as e:
    print("scheduler_tick.py requires PyYAML: pip install PyYAML", file=sys.stderr)
    raise SystemExit(1) from e

STATE_FILENAME = "scheduler_state.json"
LOCK_FILENAME = "scheduler.lock"


def project_root() -> Path:
    env = (Path(__file__).resolve().parent.parent).resolve()
    return env


def state_path() -> Path:
    d = Path.home() / ".jobfinderos"
    d.mkdir(parents=True, exist_ok=True)
    return d / STATE_FILENAME


def lock_path() -> Path:
    d = Path.home() / ".jobfinderos"
    d.mkdir(parents=True, exist_ok=True)
    return d / LOCK_FILENAME


def append_run_log(root: Path, label: str, phase: str) -> None:
    log = root / "logs" / "launchd-runs.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    line = f"- {ts} — {label} — {phase}\n"
    try:
        with open(log, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


def load_state() -> dict[str, Any]:
    p = state_path()
    if not p.exists():
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(state: dict[str, Any]) -> None:
    p = state_path()
    tmp = p.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    tmp.replace(p)


def launchd_weekday_to_python(launchd_wd: int) -> int:
    """launchd: 0=Sun … 6=Sat → Python date.weekday(): Mon=0 … Sun=6."""
    if launchd_wd < 0 or launchd_wd > 6:
        raise ValueError(f"weekday must be 0-6, got {launchd_wd}")
    return {0: 6, 1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5}[launchd_wd]


def monday_of_week_containing(d: date) -> date:
    return d - timedelta(days=d.weekday())


def week_slot_start_local(
    now: datetime,
    launchd_weekday: int,
    hour: int,
    minute: int,
) -> datetime:
    """Start of this week's scheduled window (local tz)."""
    tz = now.tzinfo
    if tz is None:
        raise ValueError("now must be timezone-aware")
    py_wd = launchd_weekday_to_python(launchd_weekday)
    monday = monday_of_week_containing(now.date())
    slot_date = monday + timedelta(days=py_wd)
    t = time(hour, minute, tzinfo=tz)
    return datetime.combine(slot_date, t.replace(tzinfo=None), tzinfo=tz)


def daily_slot_today_local(now: datetime, hour: int, minute: int) -> datetime:
    tz = now.tzinfo
    if tz is None:
        raise ValueError("now must be timezone-aware")
    t = time(hour, minute, tzinfo=tz)
    return datetime.combine(now.date(), t.replace(tzinfo=None), tzinfo=tz)


def iso_week_tuple(d: date) -> tuple[int, int]:
    y, w, _ = d.isocalendar()
    return (y, w)


def weekly_is_due(
    state: dict[str, Any],
    now: datetime,
    cfg: dict[str, Any],
) -> bool:
    wcfg = cfg.get("weekly") or {}
    launchd_wd = int(wcfg.get("weekday", 1))
    wh = int(wcfg.get("hour", 8))
    wm = int(wcfg.get("minute", 0))

    slot_start = week_slot_start_local(now, launchd_wd, wh, wm)
    if now < slot_start:
        return False

    cur = iso_week_tuple(now.date())
    last_y = state.get("last_weekly_iso_year")
    last_w = state.get("last_weekly_iso_week")
    if last_y is not None and last_w is not None and (last_y, last_w) == cur:
        return False
    return True


def daily_is_due(
    state: dict[str, Any],
    now: datetime,
    cfg: dict[str, Any],
    weekly_due: bool,
) -> bool:
    """Daily due only if not blocked by pending weekly for this ISO week."""
    if weekly_due:
        return False

    dcfg = cfg.get("daily") or {}
    dh = int(dcfg.get("hour", 7))
    dm = int(dcfg.get("minute", 0))

    slot_today = daily_slot_today_local(now, dh, dm)
    if now < slot_today:
        return False

    today_s = now.date().isoformat()
    if state.get("last_daily_date") == today_s:
        return False
    return True


def run_skill(root: Path, label: str, skill: str) -> int:
    env = os.environ.copy()
    env["PATH"] = f"{root / '.venv' / 'bin'}:{env.get('PATH', '')}"
    return subprocess.run(
        ["/bin/bash", str(root / "scripts" / "JobFinderOS_run_skill.sh"), label, skill],
        cwd=str(root),
        env=env,
    ).returncode


def run_watch_guards(root: Path) -> None:
    """Invoke the priority-function watch guard; self-skips when not due."""
    env = os.environ.copy()
    env["PATH"] = f"{root / '.venv' / 'bin'}:{env.get('PATH', '')}"
    subprocess.run(
        [sys.executable, str(root / "scripts" / "jobfinderos_priority_watch.py")],
        cwd=str(root),
        env=env,
    )


def run_script(root: Path, script: str) -> int:
    path = root / "scripts" / script
    env = os.environ.copy()
    env["PATH"] = f"{root / '.venv' / 'bin'}:{env.get('PATH', '')}"
    return subprocess.run(
        ["/bin/bash", str(path)],
        cwd=str(root),
        env=env,
    ).returncode

def acquire_lock(lock_fp) -> bool:
    """Acquire a non-blocking scheduler lock on Windows or Unix."""
    if os.name == "nt":
        lock_fp.seek(0, os.SEEK_END)
        if lock_fp.tell() == 0:
            lock_fp.write(b"\0")
            lock_fp.flush()

        lock_fp.seek(0)
        try:
            msvcrt.locking(lock_fp.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    try:
        fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def release_lock(lock_fp) -> None:
    """Release the scheduler lock."""
    if os.name == "nt":
        lock_fp.seek(0)
        try:
            msvcrt.locking(lock_fp.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        return

    try:
        fcntl.flock(lock_fp.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass

def main() -> int:
    ap = argparse.ArgumentParser(description="JobFinderOS scheduler tick")
    ap.add_argument("--dry-run", action="store_true", help="Print decisions only; do not run jobs")
    ap.add_argument("--config", type=Path, help="Override path to scheduler.yaml (default: ROOT/config/scheduler.yaml)")
    args = ap.parse_args()

    root = project_root()
    cfg_path = args.config or (root / "config" / "scheduler.yaml")

    lock_fp = open(lock_path(), "a+b")
    if not acquire_lock(lock_fp):

        append_run_log(root, "scheduler-tick", "skipped (lock held)")
        print(
            "scheduler_tick: another instance is running; exit 0",
            file=sys.stderr,
        )
        lock_fp.close()
        return 0

    try:
        with open(cfg_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        if not cfg:
            cfg = {}
        state = load_state()
        now = datetime.now().astimezone()

        weekly_due = weekly_is_due(state, now, cfg)
        daily_due = daily_is_due(state, now, cfg, weekly_due)

        append_run_log(root, "scheduler-tick", "evaluated")

        if args.dry_run:
            print(
                json.dumps(
                    {
                        "now": now.isoformat(),
                        "weekly_due": weekly_due,
                        "daily_due": daily_due,
                        "priority_watch_guard": "invoked",
                        "state": state,
                    },
                    indent=2,
                )
            )
            return 0

        exit_rc = 0

        if weekly_due:
            append_run_log(root, "scheduler-tick", "running mark-weekly")
            rc = run_skill(root, "mark-weekly", "mark-weekly")
            if rc != 0:
                append_run_log(root, "scheduler-tick", f"mark-weekly failed (exit {rc})")
                exit_rc = rc
            else:
                y, w = iso_week_tuple(now.date())
                state["last_weekly_iso_year"] = y
                state["last_weekly_iso_week"] = w
                state["last_weekly_run_date"] = now.date().isoformat()
                state["last_daily_date"] = now.date().isoformat()
                save_state(state)
                append_run_log(root, "scheduler-tick", "mark-weekly completed (weekly wins: daily skipped today)")
        elif daily_due:
            append_run_log(root, "scheduler-tick", "running jobs-daily")
            rc = run_skill(root, "jobs-daily", "jobs-daily")
            if rc != 0:
                append_run_log(root, "scheduler-tick", f"jobs-daily failed (exit {rc})")
                exit_rc = rc
            else:
                state["last_daily_date"] = now.date().isoformat()
                save_state(state)
                append_run_log(root, "scheduler-tick", "jobs-daily completed")
        else:
            append_run_log(root, "scheduler-tick", "nothing due")

        run_watch_guards(root)
        return exit_rc
    finally:
        release_lock(lock_fp)
        lock_fp.close()

if __name__ == "__main__":
    raise SystemExit(main())
