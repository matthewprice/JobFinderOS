# AGENTS.md — JobFinderOS

Guidance for coding agents and IDE copilots working on this repository (the humans' instructions for the *job-search* agents are in `CLAUDE.md`).

## What this is

An agentic job-search system on Claude Code: three personas in `.claude/agents/`, skills in `.claude/commands/`, doctrine in `config/recruiter_playbook.md`, output in an Obsidian vault at `vault/`. Scheduled runs go launchd → `scripts/scheduler_tick.py` → `scripts/jobfinderos_run_skill.py` → `claude -p /skill` (explicit tools, a permission mode, JSON output; never `--dangerously-skip-permissions`). Discovery is deterministic code: `scripts/jobfinderos_discover.py` over the `scripts/jfgraph/` package (a small state-graph runtime: nodes, routed edges, checkpoints, record/replay for HTTP and LLM calls). `tests/` runs token-free with `tests/fake_claude.py`; `sim/` holds the benchmark protocol.

## Read first

`README.md`, `CLAUDE.md`, `config/recruiter_playbook.md`. When touching automation: `config/scheduler.yaml`, `scripts/`, `vault/Automation/`.

## Rules

1. Nothing about a specific candidate goes into this repo. Personal data lives only in gitignored paths (`config/profile.md` and friends, `vault/` content). If you need an example, use a fictional one.
2. Skills refer to "the candidate" and read every specific (name, employer, comp floor, targets, location) from `config/profile.md`.
3. Never add a code path that sends email or creates Gmail drafts. Never make automation commit or push.
4. Prefer existing entrypoints over new wrappers. One scheduler; no overlapping jobs.
5. Keep skills thin. A skill names its agent in the first line and describes the task; the persona and doctrine live in the agent file.
6. Small, verifiable edits. After an automation change, run `python3 -m unittest discover -s tests -t .`, `python3 scripts/scheduler_tick.py --dry-run`, and `bash scripts/verify_local_automation.sh`.
7. Anything that writes to disk in `scripts/jfgraph/` goes through `jfgraph.paths.Paths` (`open_write`, `assert_writable`); state lives in `$JOBFINDEROS_STATE_DIR`, never a hardcoded home path. Test harnesses never delete outside their scratch directory.
8. A change to a gate or a prompt is a change to measured behavior: re-run the relevant simulation (`sim/README.md`) and update `docs/discovery-benchmark.md` before claiming an improvement.

## Quick commands

```bash
python3 -m unittest discover -s tests -t .              # token-free test suite
bash scripts/JobFinderOS_check_local_runner.sh
python3 scripts/jobfinderos_run_skill.py jobs-daily jobs-daily
python3 scripts/scheduler_tick.py --dry-run
bash scripts/JobFinderOS_install_launchd.sh
python3 scripts/jobfinderos_discover.py --dry-run       # the discovery graph, decisions only
python3 scripts/jobfinderos_ats_poll.py --dry-run       # the poller preset alone
```
