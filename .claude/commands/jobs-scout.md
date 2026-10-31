---
description: Turn today's discovery results into opportunity notes. Reads the scored ATS Inbox block written by the discovery graph, writes the notes and human-path stubs for roles at or above the thresholds, and surfaces the actionable ones. Fetches nothing itself. Works for any career. Claude Code, no API keys.
---

**Agent:** `scout` · delegated batch work.
- **Main session:** call the Agent tool with `subagent_type: "scout"` and the prompt: *"Read `.claude/commands/jobs-scout.md` and execute its Task section. Args: <any arguments given>. Return the report described in `.claude/agents/scout.md`."* Relay the report and the Recruiter's read.
- **Already the scout agent, or no Agent tool available:** read `.claude/agents/scout.md`, then execute the Task section here.

## Task

### Always read first
- `config/profile.md` (lanes, seniority filter, comp floor, location rules, exclusions), `config/scoring_rubric.md`, `config/discovery.yaml` (or `config/discovery.example.yaml`) for the `thresholds` (`log`, `act`)
- `config/recruiter_playbook.md` §2 and §6
- `vault/Market Intel/ATS Inbox.md`: today's block (`## <today> — N new`). Each entry carries Status, Score with its rationale, Match (title or responsibility), Location with its flag, Comp, Lane, ATS req id, and the posting URL
- `vault/Companies/` and `vault/Tracking/Companies.md` for what is already tracked

### Steps
1. If today's inbox block is missing, stop and report that discovery has not run today (the orchestrator runs `python3 scripts/jobfinderos_discover.py` first). **Fallback, only when asked:** fetch each Tier 1 board from the profile's URL table with `WebFetch` (JSON endpoints preferred), score against the rubric, and continue; label the report "fallback: LLM fetch".
2. For every entry scored at or above `thresholds.log`, create or update `vault/Companies/<Company>/<Company> — <Role>.md` per the opportunity template in `CLAUDE.md`. Carry over the score and rationale, the match mode, the location and comp flags, and the posting URL. Read the posting when the rationale leaves a question open; do not re-score unless the rubric clearly disagrees, and say why if you do.
3. For every entry at or above `thresholds.act`, stub the human path: check `vault/Tracking/Contacts.md` and alumni overlap from the profile's career history, and try to name the hiring manager (`WebSearch` "<Company> head of <function>"). Record it in Key Contacts. A recommendation without a human-path answer is incomplete. State the sequence: outreach first, apply 48 to 72h later (closing windows: both in parallel).
4. Entries marked `unscored` (the scorer was unavailable) get a note only if they are at a Tier 1 company; list the rest in the report.
5. Write the run summary to `vault/Archive/Daily Jobs Watch/Daily Jobs Watch — <today>.md` and surface the actionable roles with their human paths into `vault/Dashboard.md` `🎯 Now`.
6. End with the **Recruiter's read**: what today's batch says about the market and the pipeline, not a list. Mention any `Discovery Review` items waiting for the candidate.

### Rules
- No email, no outreach, no git. Never fetch boards the graph already covered.
- Stay inside the profile's lanes; be proactive about adjacent roles the classifier confirmed, within the seniority filter.
