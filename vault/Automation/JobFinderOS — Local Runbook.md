# JobFinderOS — Local Runbook

> **JobFinderOS:** system · local-first automation (macOS launchd + Claude Code CLI)

Scheduled work runs **on your Mac**, writes directly to **`vault/`** on disk (Obsidian). No cloud routines, no API keys, no `git push` required for automation to work.

---

## Architecture

```
launchd (com.jobfinderos.scheduler)
  → scheduler_tick.py (every ~30 min while awake)
    → jobfinderos_run_skill.py (daily / weekly when due)
    → jobfinderos_priority_watch.py (each tick)
      → claude -p /skill (explicit tools, acceptEdits, JSON output) → vault/
          /jobs-daily runs `python3 scripts/jobfinderos_discover.py` (the discovery graph)
          before the scout; com.jobfinderos.atspoll runs it early as insurance
```

Logs: `logs/launchd-runs.log`, `logs/scheduler-cron/*.last-success`, mirror in [[JobFinderOS — Schedule & Run Log]] when allowed.

## Scheduled jobs

| Job | Entrypoint | Skill | Window (timezone in `config/scheduler.yaml`) |
|-----|------------|-------|----------------------------------------------|
| Daily Jobs | `scheduler_tick` → `jobfinderos_run_skill.py` | `/jobs-daily` | After `daily.hour` (default 7:00) |
| Weekly Mark | `scheduler_tick` → `jobfinderos_run_skill.py` | `/mark-weekly` | `weekly.weekday` after `weekly.hour` (default Monday 8:00). Weekly wins: daily is skipped that day |
| Priority watch | `jobfinderos_priority_watch.py` each tick | `/jobs-priority-watch` | Weekdays after `watch.hour` (default 9:00) |

Install or refresh:

```bash
bash scripts/JobFinderOS_install_launchd.sh
JOBS_DAILY_HOUR=7 MARK_WEEKLY_WEEKDAY=1 WATCH_HOUR=9 JOBFINDEROS_TZ=America/New_York bash scripts/JobFinderOS_install_launchd.sh
```

## Optional helpers

`bash scripts/JobFinderOS_install_helpers.sh [latest|prune|atspoll]` renders `scripts/launchd/*.plist.template` and installs:

| Label | What | Cadence |
|-------|------|---------|
| `com.jobfinderos.latest` | `jobfinderos_update_latest.py`: rewrites the dated pointer notes at the vault root | On any write to the digest folders |
| `com.jobfinderos.prune` | `jobfinderos_prune_history.py`: 30-day retention for digests/pulses/watches, 90 for weekly briefs | Weekly |
| `com.jobfinderos.atspoll` | `jobfinderos_discover.py --shadow`: the discovery graph (fetch, discover boards, filter, classify, score) → `Market Intel/ATS Inbox.md`, no notifications | Weekdays 06:30 and 12:20 |

Discovery needs `config/ats_boards.yaml` (copy the example, edit `filters:`; `python3 scripts/jobfinderos_ats_poll.py --seed` once if you do not want the current backlog surfaced) and reads `config/discovery.yaml` (copy the example; graph preset, thresholds, classifier, signals). State (seen-set, learned terms, discovery cache, run checkpoints, LLM cache) lives in `$JOBFINDEROS_STATE_DIR` (default `~/.jobfinderos`). Its stdout/stderr go to `~/.jobfinderos/logs/`, not the repo, because launchd cannot write under `~/Documents` without Full Disk Access. Items that need a human (an ambiguous board, a learned title term) land in `Market Intel/Discovery Review.md`; tick the box and the next run picks it up.

## Prerequisites

1. Claude Code CLI on PATH and logged in (`claude auth login`)
2. Gmail MCP connected in claude.ai (for the email pass; everything else works without it)
3. The Mac awake during the windows
4. PyYAML in the project venv (`pip install -r requirements.txt`)

## Manual commands

```bash
bash scripts/JobFinderOS_check_local_runner.sh
python3 scripts/jobfinderos_run_skill.py jobs-daily jobs-daily
python3 scripts/scheduler_tick.py --dry-run
python3 scripts/scheduler_tick.py
bash scripts/verify_local_automation.sh
bash scripts/JobFinderOS_test_launchd.sh
python3 scripts/jobfinderos_priority_watch.py     # standalone guard; JSON on stdout
python3 scripts/jobfinderos_discover.py --dry-run # the discovery graph, decisions only
python3 scripts/jobfinderos_discover.py --weekly  # also writes Market Intel/About to Hire — <date>.md
python3 scripts/jobfinderos_ats_poll.py --dry-run # the poller preset alone
python3 -m unittest discover -s tests -t .        # the test suite (no tokens)
```

## Failure triage

| Symptom | Check |
|---------|-------|
| No digest on disk | `tail logs/launchd-runs.log`; run the skill manually |
| `claude: command not found` | Install Claude Code CLI; set `CLAUDE_BIN` |
| Auth errors | `claude auth login` |
| Guard always `skip` | Window not reached, weekend, or marker already set; see the JSON `reason` |
| Stuck lock | Remove `logs/scheduler-cron/*.lock` if no claude process is running |
| launchd `Operation not permitted` | The repo is under `~/Documents`/`Desktop`/`Downloads`. Move it (e.g. `~/Developer/`) or grant Full Disk Access to the venv Python |
| Session limit from the CLI | Wait for the reset; the tick logs `failed (exit 1)` without updating markers |

## Backup (optional, by hand)

Automation never commits or pushes. The vault is gitignored in this repo. If you want it backed up, keep it in a **private** repository of your own.
