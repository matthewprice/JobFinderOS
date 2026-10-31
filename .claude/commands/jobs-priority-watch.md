---
description: Priority-function watch. Reads today's discovery results for the function the profile marks as priority at the companies it marks as priority, honoring the seniority filter. Vault-only, silent when nothing is new. Weekday scheduled watch. No fetching, no email.
---

**Agent:** `scout` · delegated batch work.
- **Main session:** call the Agent tool with `subagent_type: "scout"` and the prompt: *"Read `.claude/commands/jobs-priority-watch.md` and execute its Task section. Return the report described in `.claude/agents/scout.md`, or the single line 'nothing new' if the watch found nothing."*
- **Already the scout agent, or no Agent tool available:** read `.claude/agents/scout.md`, then execute the Task section here.

## Task

A narrow watch for the function the profile marks as priority, at the companies the profile marks as priority. It runs every weekday tick, so it must be quiet when there is nothing to say. The fetching is already done: `scripts/jobfinderos_discover.py` runs before the daily routine and again at midday, and every posting it surfaces carries a Lane (Lane A is the priority function) and the company's tier.

### Always read first
- `config/profile.md`: the priority function, the seniority filter, the **priority companies** and **watch companies** sections, the most-wanted product category if one is named, exclusions
- `config/discovery.yaml` (or the example) for the `thresholds`
- `vault/Market Intel/ATS Inbox.md`: the blocks dated today and yesterday
- `vault/Companies/` so nothing is re-alerted

### Steps
1. If there is no inbox block for today, run `python3 scripts/jobfinderos_discover.py --shadow` with Bash (it is deterministic and quick when nothing is new), then read the inbox again. If that fails, stop and report the failure; do not fetch boards by hand.
2. From today's block, keep entries that are **Lane A** (the priority function) **and** at a priority or watch company (match the company name against the profile's lists), or in the most-wanted product category. The graph already applied the seniority filter; if a Manager title is marked borderline on scope, read the posting: a founding or team-build mandate counts as leadership.
3. Drop anything that already has an opportunity note.
4. **Nothing left:** stop. Write no files, no summary; return "nothing new".
5. **New roles:** create or update opportunity notes per the template in `CLAUDE.md` (Stage `🔍 Spotted`, the inbox's score and rationale, flags). For roles at or above the act-on-it threshold, stub the human path in Key Contacts (Contacts.md check, likely hiring manager). Append a line to `vault/Archive/Daily Jobs Watch/Daily Jobs Watch — <today>.md` and surface any top-scoring find into `vault/Dashboard.md` `🎯 Now`.

### Rules
- No email, no outreach, no git. Honor every exclusion.
- Never crawl: the watch reads what discovery found. The only fetch allowed is the discovery script itself.
