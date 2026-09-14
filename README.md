<p align="center">
  <img src="assets/hero.svg" width="352" alt="Clawd, the Claude Code mascot, in a prospector's hat with a pickaxe and a gold nugget">
</p>

<h1 align="center">JobFinderOS</h1>

<p align="center"><b>Find your dream job with Claude Code agents. No coffee breaks, no doomscrolling, no sleep till you're hired!</b></p>

This project capitalizes on one simple fact:  a career search is a numbers game. More real contacts lead to more listings you hear about early, more of those become applications with a person attached, and more of those become interviews. A single human running that chain alone has limited hours in the week. Agents do not. JobFinderOS is a Claude code project that puts agents to work on your search, profiling markets, following companies, crawling job postings, identifying new strategic contacts and preparing you for every interview. Each stage of the job search funnel gets more nurturing than you could feed it yourself, and the judgment stays with you.

This manual covers four things:

1. [What it is](#1-what-it-is)
2. [Getting up and running](#2-getting-up-and-running)
3. [Using it](#3-using-it)
4. [The methodology: how agentic job search works here](#4-the-methodology)

Plus a short [reference](#5-reference) and a note on [contributing](#contributing) at the end.

---

## 1. What it is

JobFinderOS is a set of agent personas and skills that run inside [Claude Code](https://claude.com/claude-code) and work for you to identify career opportunities based on your criteria. They watch the market, find roles, keep your pipeline up to date and honest, and get you ready for every interview. They write everything to a folder of Markdown notes, which you can open in [Obsidian](https://obsidian.md) or any text editor.

**Three agents, each with one job.**

<p align="center">
  <img src="assets/coach.png" width="114" alt="Coach: Clawd in a ball cap holding a clipboard">&nbsp;&nbsp;&nbsp;
  <img src="assets/scout.png" width="114" alt="Scout: Clawd in a bucket hat with binoculars">&nbsp;&nbsp;&nbsp;
  <img src="assets/mark.png" width="114" alt="Mark: Clawd in a green eyeshade reading ticker tape">
  <br>
  <sub><b>Coach</b>, the recruiter &nbsp;·&nbsp; <b>Scout</b>, the crawler &nbsp;·&nbsp; <b>Mark</b>, the market analyst</sub>
</p>

| Agent | What it does | What it can touch |
|---|---|---|
| **Coach** | The recruiter brain. Judges the pipeline, preps you for interviews, runs mock interviews, keeps your career stories, drafts outreach and cover letters in your voice, checks drafts for AI tells, writes a postmortem on every loss, and produces the morning digest. | Everything, including reading your Gmail. Never sends. |
| **Scout** | The crawler. Scans your target companies' own careers pages, scores each role against your rubric, and logs the good ones as opportunity notes. | Web and the vault. No email. |
| **Mark** | The market analyst. Tracks funding, leadership moves, new team build-outs, and job-title renames at the companies you care about, and tells Scout and Coach where to look next. | Web and the vault. No email. |

> [!IMPORTANT]
> **🔒 Privacy by Design**
>
> A job search is sensitive, so nothing about you is meant to leave your machine. Your profile, scoring rubric, wins, stories, voice notes, target list, and ATS board list are all `.gitignore`d. So is the whole vault, apart from a few empty skeleton files (the Dashboard, Strategy, and Tracking templates) that ship blank; once they fill in, leave them uncommitted. The agents never commit, push, send, or upload anything. The only place your data goes is into the Claude Code session you start yourself, and the only remote it ever reaches is one you add by hand.

**Skills are the commands you run.** Each is a short Markdown prompt in `.claude/commands/`. You type `/jobs-daily` or `/mock-interview` in Claude Code and the right agent picks it up. There are 25. Section 3 lists them by when you would use them.

**Your config is what they read first.** `config/profile.md` says who you are and what you want. `config/scoring_rubric.md` says how to score a role. `config/recruiter_playbook.md` says how the agents behave. Section 4 explains that playbook in plain English.

**The vault is where everything lands.** `vault/Dashboard.md` is the daily read. `vault/Strategy.md` is the weekly one. Every company gets a folder under `vault/Companies/` with a profile and one note per role. Digests, market briefs, outreach drafts, and interview prep all have their own folders.

**What it never does.** It never sends an email or a message. Drafts go to your clipboard and the vault, and you send them from your own client. It never mass-applies. It never sits in the interview.

**What it needs.** A Claude Code subscription. No API keys. Python is only used by the optional scheduler.

---

## 2. Getting up and running

Four steps. The first two happen in your terminal, the last two inside Claude Code.

### Step 1. Get Claude Code

You need [Claude Code](https://claude.com/claude-code) installed and logged in with a subscription. No API keys.

```bash
claude auth login
```

### Step 2. Clone and install

**macOS / Linux**

```bash
git clone https://github.com/matthewprice/JobFinderOS.git
cd JobFinderOS
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
claude
```

**Windows PowerShell**

```powershell
git clone https://github.com/matthewprice/JobFinderOS.git
cd JobFinderOS
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
claude
```

The Python setup needs Python 3.11 or newer. Python is only required for the optional scheduler and helper scripts, so if you never plan to schedule anything you can skip the virtual environment and dependency installation and go straight to `claude`.

### Step 3. Teach it who you are

Inside Claude Code:

```
/onboard
```

This is a 15-minute conversation. It asks about your current role, what you want next, your salary floor, where you will and will not work, companies to avoid, the exact job titles recruiters use for your target, and two or three real wins with numbers. Any career works. It does not assume you are technical.

When it finishes you have four private files, all gitignored:

| File | What it holds |
|---|---|
| `config/profile.md` | Who you are, targets, comp floor, location rules, exclusions, target companies with their careers-page URLs |
| `config/scoring_rubric.md` | How to score a role from 1 to 10, weighted by what you said matters |
| `config/wins.md` | Your wins in situation-task-action-result form, used in prep and cover letters |
| `config/voice.md` | How you write, so drafts sound like you |

### Step 4. Run the first scan

```
/jobs-scout
```

Scout reads your target companies' careers pages, scores what it finds, and writes an opportunity note for anything that clears your bar. Open `vault/` and read `Dashboard.md`. You are running.

### Optional extras

Each of these is independent. Add them when you want them.

- **Obsidian.** Open `vault/` as a vault in [Obsidian](https://obsidian.md) to read the notes with working links. Any text editor works too.
- **Gmail.** Connect Gmail as an MCP connector in claude.ai. Coach can then triage recruiter email, spot interview invitations, and detect rejections. It is instructed to read only; Section 5 explains what that rests on.
- **A schedule.** The master scheduler supports macOS `launchd` and Windows Task Scheduler. Both run `scripts/scheduler_tick.py` on a repeating interval, every 30 minutes by default. The scheduler reads `config/scheduler.yaml` and decides whether `/jobs-daily`, `/mark-weekly`, or the weekday priority watch is due.

  **macOS**

  ```bash
  bash scripts/JobFinderOS_install_launchd.sh
  ```

  **Windows PowerShell**

  ```powershell
  .\scripts\JobFinderOS_install_windows_task.ps1
  ```

  On Windows, the task runs under the logged-in user's session. The computer must be awake for scheduled work to run. Re-running the installer refreshes the task definition.

  A missed run catches up on the next scheduler tick. Runs are logged to `logs/` and mirrored to `vault/Automation/`.

  Check the scheduler without running a Claude Code skill:

  **macOS / Linux**

  ```bash
  python3 scripts/scheduler_tick.py --dry-run
  ```

  **Windows PowerShell**

  ```powershell
  python .\scripts\scheduler_tick.py --dry-run
  Get-ScheduledTask -TaskName "JobFinderOS Scheduler"
  ```

  On macOS, you can also verify the local automation setup:

  ```bash
  bash scripts/verify_local_automation.sh
  ```

---

## 3. Using it

Every skill run ends with a **Recruiter's read**: two to five sentences of candid advice about what today's state means and what you are avoiding. It is never a recap. Read it.

### Every day

<img src="assets/coach.png" width="40" alt="Coach"> **Coach** runs these. `/jobs-daily` also sends Scout and Mark out first, then hands what they found to Coach.

```
/jobs-daily     Market pulse and scout run in parallel, then email triage, then the digest.
/checkin        Tick boxes on the Dashboard, tell it what happened, it ages every thread.
/whats-next     "What should we do next?" It sweeps the pipeline and offers the 3 to 5 best moves.
```

If you run one thing a day, run `/whats-next`. It is a conversation: pick a move, do it together, the vault updates, the list re-ranks.

### Before you apply

<img src="assets/coach.png" width="40" alt="Coach"> **Coach** runs these.

```
/warm-path <Company>       Who do you know, or can reach, there? Returns a verdict and dates.
/network-outreach <Co>     Finds 2 or 3 peer-level people and drafts a research-backed note.
/draft-message             A reply, nudge, thank-you, or cold note. Copies to clipboard.
/voice-check               Scans any draft for AI tells. PASS, REWRITE, or DO NOT SEND.
```

The rule behind this is in Section 4.2. Short version: find a human first, apply two or three days later.

### Researching a company

<img src="assets/mark.png" width="40" alt="Mark"> **Mark** runs these.

```
/jobs-research <Company>   Business, funding, leadership, product, how they sell, open roles, an angle.
/mark-profiler <Company>   A deeper evaluation for a real decision. Scored, with a verdict.
/title-audit               Reads live postings and tells you what your job is called right now.
```

### When interviews start

<img src="assets/coach.png" width="40" alt="Coach"> **Coach** runs these.

```
/jobs-prep <Company> <Role> <Stage>    Full prep brief: company, round, and every person in the room.
/mock-interview <Company> <Round>      Plays the interviewer in character. Scores each answer.
/day-of-card <Company> <Round>         One page to have open: beats, lead stories, questions, traps.
/jobs-cover <Company> <Role>           A cover letter built from research, in your voice.
/story add | find | update             Your career story library. Prep and covers draw from it.
```

### After a loss

<img src="assets/coach.png" width="40" alt="Coach"> **Coach** runs these.

```
/postmortem <Company>      Classifies the objection, logs it in Strategy.md, names the fix.
/profile                   Deepens your wins and voice files over time.
```

### The rest

`/mark-pulse`, `/jobs-scout`, `/jobs-email`, and `/jobs-digest` are the pieces `/jobs-daily` is made of. Run them alone when you want one piece. `/mark-weekly` is the weekly brief and Strategy pass, `/email-watch` is a read-only inbox briefing, and `/jobs-priority-watch` is the narrow weekday scan the scheduler runs.

### Reading the vault

| Note | When to read it |
|---|---|
| `vault/Dashboard.md` | Every morning. Funnel pulse, plays for today, live threads, aging applications. |
| `vault/Strategy.md` | Weekly. Positioning, the objection log, funnel history, proof assets, deadline math. |
| `vault/Daily Digests/` | The morning briefing for each day. |
| `vault/Companies/<Company>/` | One profile per company plus one note per role, with contacts and a timeline. |
| `vault/Tracking/` | Contacts, the email follow-up queue, the company index. |
| `vault/Outreach Drafts/` | Every draft the coach has written. Nothing here has been sent. |
| `vault/Market Intel/` | Weekly briefs, the market pulse, and the handoff file Mark writes for Scout. |

---

## 4. The methodology

This is the operating doctrine the agents follow, written for a person. The source is `config/recruiter_playbook.md`. Everything here came out of running one real search for five months and studying what worked.

### 4.1 Act like a recruiter, not a clerk

A job board shows you listings. A recruiter tells you what your pipeline means, which thread is dying, which play you are avoiding, and when a pattern has formed. That is the coach's job. The Recruiter's read at the end of every run is the mechanism. If it is uncomfortable, it is working.

### 4.2 Attach a human before you apply

An application with no human attached is the last resort, not the default. Before anything is queued, the agents walk a ladder: people you already know at the company, former colleagues who moved there, the hiring manager and their boss by name, a named recruiter. Outreach goes first. The application follows 48 to 72 hours later, so a mention inside the company lands before your resume hits the pile.

When the ladder comes up empty, the application is logged as cold. The system tracks the ratio and aims for 70 percent warm. In the search this was built on, every single rejection was a cold application. Not most. All.

### 4.3 Never sound like a machine

Everyone on the hiring side reads AI-written messages all day and is pattern-matching for them. One detected template can quietly end a conversation, and the people in your field talk to each other. So:

- **Low volume by design.** At most 3 new people a day, 10 a week. At most 2 follow-ups per thread, then park it for 30 days. Never two similar messages to two people at one company.
- **The two-fact rule.** Every outbound message carries one fact that took real work to find, with the source cited so you can read it first, and one thing only you could say. Missing either, it does not go. Silence beats generic.
- **You send everything.** Drafts land on your clipboard with a "before you send" checklist and slots you have to fill in your own words. The agents never send, never schedule, never create a Gmail draft.
- **The voice gate.** Nothing drafted for you may read as AI-written. No em dashes, no "I hope this finds you well," no perfectly balanced three-part sentences, no flattery openers. `/voice-check` enforces it.

### 4.4 Silence is data

Every thread is aged against a table of norms. An application at a small company with no reply after 14 days is presumed dead. After a recruiter screen, 7 days of silence means you are the backup candidate, so open a second thread at a comparable company now. A warm contact who has not answered in a week is busy, not hostile, so one bump with a new angle, then park. Presumed-dead threads stop getting your energy. If one revives, that is a bonus, not a plan.

### 4.5 Every loss becomes intelligence

After any rejection, withdrawal, or 30-day ghost, `/postmortem` classifies the objection, both what they said and what they probably meant: domain-proof gap, level mismatch, location, comp, slate, culture, unknown. It goes into the objection log in `Strategy.md`. Three of the same objection is a positioning problem, not bad luck, and the fix becomes a strategy action. Nine postmortems in the original search surfaced the pattern that changed its whole back half.

### 4.6 Check what your job is called now

Job titles move, and postings are the trailing indicator. Mark watches for renames at your target companies. `/title-audit` reads their live postings, tallies the vocabulary, and hands you the exact terms to put in your profile and saved searches. In the original search the target role had been renamed at the companies that mattered. The winning listing arrived three weeks after the search terms changed, under the new name. The crawler never found it. The rename did.

### 4.7 Spend AI on depth, not volume

Mass-applying stopped working for everyone at about the same time, because everyone can do it now. The edge moved to the rooms. AI makes serious preparation cheap: a research brief per round, a profile on every interviewer, mock interviews, cover letters that start from research, a story library you know cold. That is where the time saved by the agents should go.

### 4.8 Run the pipeline like a funnel

Stages are Applied, Screen, Hiring-manager round, Final, Offer, tracked separately for warm and cold. The weekly Strategy pass does the math out loud. If cold applications are not converting to screens, more cold applications are not the answer. If screens are not converting to hiring-manager rounds, the leak is your narrative. Two more rules from the same section: every serious opportunity gets multi-threaded (recruiter, hiring manager, their boss, a peer) so one silence cannot kill it, and when any process reaches the hiring-manager stage, accelerate the two best comparable ones so offers land in the same ten days. Never bluff an offer that does not exist.

### 4.9 Work backward from a date

Set a deadline for having an offer in hand. Finals two weeks before that, hiring-manager rounds a month before, screens six weeks before, warm plays now. Every weekly read says in plain terms whether the current pace hits the date and what would change it.

---

## 5. Reference

### Layout

```
.claude/agents/      coach.md · scout.md · mark.md         the three agents
.claude/commands/    25 skills                             each names its agent in the first line
config/              profile, rubric, wins, voice, targets  your copies are gitignored; templates ship
config/recruiter_playbook.md                               the doctrine in Section 4, in full
scripts/             scheduler, guards, ATS poller, pruner
vault/               the Obsidian vault
CLAUDE.md            project instructions the agents read every run
```

### Email safety

No code in this repository calls a send or draft API. Gmail is reached only through the claude.ai MCP connector, and that connector does expose send and draft tools. What keeps the agents read-only is doctrine: `CLAUDE.md`, the agent files, and the playbook all forbid sending and drafting, and every draft goes to the clipboard and the vault instead. Read those instructions before you trust the system with your inbox; there is no separate technical lock.

### Privacy

Your profile, rubric, wins, stories, voice notes, and vault contents are gitignored. Nothing about you ships in this repo, and nothing the agents write goes anywhere you did not put it. If a company you are applying to has guidance on AI use in applications, follow it: you draft first, the coach refines.

### Retention

Daily digests, run summaries, and daily briefs keep 30 days. Weekly briefs keep 90. On macOS, a weekly launchd helper can prune the rest. The vault is gitignored here, so if you want a permanent archive, back it up to a private repository of your own. Anything worth keeping lives in `Strategy.md`, `Tracking/`, or `Companies/`, never in an old digest.

### Manual runs and logs

**macOS / Linux**

```bash
python3 scripts/scheduler_tick.py --dry-run                   # what would run right now
python3 scripts/jobfinderos_run_skill.py jobs-daily jobs-daily # run one skill through the portable runner
bash scripts/JobFinderOS_check_local_runner.sh                # is Claude Code reachable from launchd?
tail -f logs/launchd-runs.log                                 # watch runs
```

**Windows PowerShell**

```powershell
python .\scripts\scheduler_tick.py --dry-run
python .\scripts\jobfinderos_run_skill.py jobs-daily jobs-daily
Get-ScheduledTask -TaskName "JobFinderOS Scheduler"
Get-Content .\logs\launchd-runs.log -Tail 20
```

### Credits

The pixel character is Clawd, the Claude Code mascot. He belongs to Anthropic and is redrawn here in work clothes, with affection and no affiliation.

The three agents are the same character in different gear. Coach wears the ball cap and carries the clipboard. Scout has the bucket hat and binoculars. Mark wears the green eyeshade and reads the ticker tape. The drawings live in `assets/` as SVG sources; the three agent icons also ship as PNGs because GitHub collapses SVGs inside Markdown tables.

### Writing your own skill

Open any file in `.claude/commands/`. The first line names the agent. The rest is the task. Copy one, change the task, save it under a new name, and it is a command.

---

## Contributing

Issues and pull requests are welcome. Bug reports, wording fixes, and "this claim does not match the code" are all useful.

The most valuable contribution is a new skill. Each file in `.claude/commands/` is a short Markdown prompt: a `description` line in the frontmatter, an **Agent** line saying which of Coach, Scout, or Mark runs it, and a Task section. If you have built one that helped your own search, open a pull request with it. A few things to keep in mind:

- **Keep it career-neutral.** Skills read `config/profile.md` for everything about the candidate. Nothing about a specific person, industry, or company belongs in a skill.
- **Follow the playbook.** Warm path first, low outreach volume, a human in the loop on every message, and a Recruiter's read at the end. `config/recruiter_playbook.md` is the doctrine; a skill that fights it will not be merged.
- **Never send.** Drafts go to the clipboard and the vault. No skill may send email, create Gmail drafts, or post anywhere on the candidate's behalf.
- **Scrub before you push.** Check the diff for your own profile, wins, contacts, or vault notes. The `.gitignore` covers the usual paths, but a copied example can slip through.

Say in the pull request what the skill is for, which agent runs it, and what it wrote to the vault when you ran it. MIT licensed, so contributions are too.
