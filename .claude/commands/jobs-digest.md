---
description: Compile the morning briefing (new opportunities, recruiter emails to review, pipeline updates, action items) into Daily Digests/, with a scoped Dashboard refresh and a warm-path coverage check. Claude Code, no API keys.
---

**Agent:** `coach` · delegated batch work (needs Gmail read access, which the coach agent inherits).
- **Main session:** call the Agent tool with `subagent_type: "coach"` and the prompt: *"Read `.claude/commands/jobs-digest.md` and execute its Task section. Return the report described in `.claude/agents/coach.md`."* Relay the top actions and the Recruiter's read.
- **Already the coach agent, or no Agent tool available:** read `.claude/agents/coach.md`, then execute the Task section here.

## Task

### Always read first
- `config/recruiter_playbook.md` (§1, §2, §4), `config/profile.md`, `config/scoring_rubric.md`
- `vault/Dashboard.md`, `vault/Strategy.md`, `vault/Tracking/Companies.md`, `vault/Tracking/Email Follow-ups Queue.md`, `vault/Tracking/Contacts.md`
- Recent `vault/Archive/Daily Jobs Watch/` entries and today's `vault/Market Intel/` notes, including today's block in `ATS Inbox.md` (scored by the discovery graph) and any open items in `Market Intel/Discovery Review.md`
- `config/discovery.yaml` (or the example) for the `thresholds`
- Gmail (`search_threads`) for activity since the last digest

### Output
1. Write `vault/Daily Digests/<today>.md` using the Daily Digest template in `CLAUDE.md`: New Opportunities (inbox entries scored at or above `thresholds.act`, with the scout's human-path answer where one exists), Recruiter Emails to Review, Application Updates, Action Items (prioritized checkboxes; include one for each unanswered `Discovery Review` item). Reference today's Market Pulse with a wikilink at the top when it affects priorities. Lead the candidate to their **highest-value 1 to 3 actions**, and close with the **Recruiter's read**.
2. **Warm-path coverage check.** For each active application, check Contacts.md for a logged human at that company. Add a **"Warm-path gaps"** line listing active applications with none, and point to `/warm-path <Company>` for each. Goal: every meaningful application has a human attached or an explicit note that none exists.
3. Update `vault/Archive/Daily Jobs Watch/Daily Jobs Watch — <today>.md` with the executive summary.
4. **Scoped Dashboard refresh.** Edit only the mechanical parts of `vault/Dashboard.md`: the `> Updated:` line and funnel pulse counts, `🗓️ Scheduled`, `🎯 Now` (insert today's top actions, remove done ones), `🔴 Live human threads` states, `📬 Applications` ages and statuses per silence norms §4. Do not rewrite `👀 Priority watch` rationale or anything hand-written. Never touch `Strategy.md`. If nothing changed, bump the date only.

### Rules
- Scannable: tables and short action lines, not prose.
- Drafts are not composed here; anything due is flagged as an action item.
