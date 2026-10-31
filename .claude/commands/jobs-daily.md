---
description: Full daily routine. Runs the market pulse, then the deterministic discovery graph, then the scout as a writer, then email triage, then the digest, and writes one consolidated run record and Recruiter's read. The everyday driver. Claude Code, no API keys.
---

**Agent:** orchestrator · runs in this session as `coach` for the closing read. Read `.claude/agents/coach.md` first.

## Task

Five passes, in order. Delegate the agent passes via the Agent tool; if the Agent tool is unavailable, read the named agent file and run the pass inline.

1. **Market Pulse** → `subagent_type: "mark"`: *"Read `.claude/commands/mark-pulse.md` and execute its Task section. Return the report."* It writes `vault/Market Intel/Jobs Handoff.json` and appends to `Signals.jsonl`, which the next pass reads.
2. **Discovery** → in this session, with Bash: `python3 scripts/jobfinderos_discover.py` (add `--weekly` on the weekly day). This is deterministic: it fetches every registered board, enrolls boards for companies the handoff or the web sweep named, filters, classifies near misses, scores, and appends today's block to `vault/Market Intel/ATS Inbox.md`. Read the JSON summary it prints. If it reports `awaiting_human`, say so in the Recruiter's read and continue; the candidate answers in `Market Intel/Discovery Review.md`.
3. **Scout** → `subagent_type: "scout"`: *"Read `.claude/commands/jobs-scout.md` and execute its Task section. Today's ATS Inbox block is already written. Return the report."* The scout writes opportunity notes and human-path stubs from the inbox; it does not fetch boards.
4. **Email** → `subagent_type: "coach"`: *"Read `.claude/commands/jobs-email.md` and execute its Task section. Return the report, including every reply that is due."*
5. **Digest** → `subagent_type: "coach"`: *"Read `.claude/commands/jobs-digest.md` and execute its Task section. Today's Market Pulse, ATS Inbox block, and scout notes are already in the vault. Return the report."*

Then, in this session:
6. Consolidate into `vault/Archive/Daily Jobs Watch/Daily Jobs Watch — <today>.md`: market signals, new roles with human paths, pipeline changes, replies due, prep actions, and the discovery summary line (postings fetched, new, surfaced, act-now, estimated cost).
7. Append a run row to `vault/Automation/JobFinderOS — Schedule & Run Log.md`.
8. One consolidated **Recruiter's read** covering market, pipeline, and the single most important move today. If replies are due, list them and offer to draft; write nothing until the candidate says yes.

## Rules
- Honor every filter in `config/profile.md`. Direct sources, never aggregators.
- The vault root stays `Dashboard.md` + `Strategy.md`.
- If step 2 fails (no board registry, no network), say so and run the scout in its fallback mode described in `jobs-scout.md`; never silently skip discovery.
