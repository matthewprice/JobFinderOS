---
description: Weekly brief. Market pulse, the weekly brief, the Jobs Handoff, and the weekly Strategy pass (funnel snapshot, objection-log patterns, proof assets, pre-posting plays, deadline math). Local vault only. Claude Code, no API keys.
---

**Agent:** `mark` · delegated batch work.
- **Main session:** call the Agent tool with `subagent_type: "mark"` and the prompt: *"Read `.claude/commands/mark-weekly.md` and execute its Task section. Return the report described in `.claude/agents/mark.md`."* Relay the week's verdict and the Recruiter's read.
- **Already the mark agent, or no Agent tool available:** read `.claude/agents/mark.md`, then execute the Task section here.

## Task

### Always read first
`config/recruiter_playbook.md` (§5, §6, §9, §10), `config/profile.md`, `config/scoring_rubric.md`, recent `vault/Market Intel/`, `vault/Dashboard.md`, `vault/Strategy.md`, `vault/Tracking/Email Follow-ups Queue.md`.

### Deliverables (all required)
1. **Market Pulse.** Execute the Task section of `.claude/commands/mark-pulse.md` (you are already the right agent).
2. **Weekly Brief** → `vault/Market Intel/Weekly Brief — <today>.md`, tagged `> **JobFinderOS:** Mark · <today> <time> TZ`, sections aligned to prior briefs: Executive Summary · Market Context: Signals & Insights · Tracked Target Company Updates · Emerging Role Watch (including any rename of the candidate's function) · Fresh Companies/Discovery (past 30 days) · Hiring Cycle Assessment · Action Checklist. The single highest-conviction move at the top.
3. **Jobs Handoff** → write or replace `vault/Market Intel/Jobs Handoff.json`: `generated_at`, `source`, `written_by_agent: "Mark"`, `priority_now[] {company, why_now, role_types[]}`, `new_to_evaluate[] {company, why_now, action}`. The discovery graph reads it: `priority_now` companies enroll as tier 1, `new_to_evaluate` companies get their board found automatically. Then read the newest `vault/Market Intel/About to Hire — <date>.md` (written by `scripts/jobfinderos_discover.py --weekly`); every company at or above the promotion threshold needs a named human play in the Weekly Brief's Action Checklist.
4. **Strategy pass** in `vault/Strategy.md`:
   1. Funnel snapshot: append the weekly row (apps, warm-attached, screens, HM, finals, offers, live threads). Recount from the Dashboard and queue; never extrapolate.
   2. Funnel read: if a conversion rate moved or a leak shifted, update the diagnosis line in plain words.
   3. Objection log: scan for patterns crossing three of a kind; if one did, add or adjust the fix under Proof assets or the narrative section.
   4. Proof assets: update statuses; anything unshipped 2+ weeks gets escalated in the read (it outranks new cold applications).
   5. Pre-posting triggers (§6): every meaningful signal this week becomes a named human play in `vault/Dashboard.md` `🎯 Now`.
   6. Deadline math (§10): does the current pace hit the deadline, and what changes the answer.
5. End the Weekly Brief with the **Recruiter's read**: the week-over-week verdict, what is working, what the candidate is avoiding, the one move that matters next week.
