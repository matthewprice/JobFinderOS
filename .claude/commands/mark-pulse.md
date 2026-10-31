---
description: Market pulse. Scan the target cohort for funding, leadership moves, GTM build-outs, and renames relevant to the search. Writes the Market Pulse, the Daily Marketing Brief, and the Jobs Handoff. Claude Code, no API keys.
---

**Agent:** `mark` · delegated batch work.
- **Main session:** call the Agent tool with `subagent_type: "mark"` and the prompt: *"Read `.claude/commands/mark-pulse.md` and execute its Task section. Return the report described in `.claude/agents/mark.md`."* Relay the top signal and the Recruiter's read.
- **Already the mark agent, or no Agent tool available:** read `.claude/agents/mark.md`, then execute the Task section here.

## Task

### Steps
1. Read `config/profile.md` (target tiers, priority companies, watch list, ATS table), `config/scoring_rubric.md`, and `vault/Market Intel/Jobs Handoff.json`.
2. Scan primary sources (`WebSearch`/`WebFetch`, never aggregators) across the target set for: Series B+ funding, new revenue leadership (field hiring follows in 60 to 90 days), enterprise GTM build-outs, function renames, and any company worth adding to the target set.
3. Write `vault/Market Intel/Daily Marketing Brief — <today>.md`:
   - `> **JobFinderOS:** Mark · <today> <time> TZ`
   - `# Daily Marketing Brief — <today>`
   - `## Market Context and Top Signals` with inline source links
   - `## Tracked Companies — Current Status`: every Tier 1/2 company, one status line plus its careers link; flag live roles
   - `## New Company Spotlight`: one newly relevant company not yet in the standing scan
   - `## Action Recommendations — Week of <today>`: 4 to 6 numbered actions with wikilinks. Pre-posting trigger rule (§6): every raise, GTM-exec hire, or field-org build-out at a target produces a named human play this week, written into `vault/Dashboard.md` `🎯 Now`.
   - `### Companies tracked this cycle`
4. Write `vault/Market Intel/Market Pulse — <today>.md`: 6 to 8 ranked signals, each with sources. The brief is the summary; the pulse is the research backing.
5. Update `vault/Market Intel/Jobs Handoff.json` (`priority_now`, `new_to_evaluate`, `apply_queue_status`).
6. Append one JSON line per signal found in step 2 to `vault/Market Intel/Signals.jsonl` (create the file if missing; never rewrite it). Shape: `{"date": "YYYY-MM-DD", "company": "<name as the employer writes it>", "type": "funding|exec_hire|buildout|rename|layoff", "strength": 0.5-1.0, "source_url": "<primary source>", "note": "<one line>"}`. The discovery graph (`scripts/jobfinderos_discover.py`) scores these per company, promotes hot companies to tier 1 in the board registry, and writes the weekly `About to Hire` note. A signal you do not log here does not move the registry.
7. End with the **Recruiter's read**.

### Rules
- Inline clickable URLs, not footnotes. Honor the excluded-profile filter and the profile's flagged exceptions.
- Today's brief replaces yesterday's by filename pattern; old ones stay in `Market Intel/` until pruned.
