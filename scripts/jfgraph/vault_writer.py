"""Vault output for discovery: the ATS Inbox entries (format unchanged from the poller,
plus optional Score / Match lines once a graph scores) and the dry-run table."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .config import Filters
from .state import Candidate

INBOX_HEADER = """> **JobFinderOS:** ATS Poller · local direct-ATS sweep · [[Dashboard]] · [[Strategy]]

# ATS Inbox

Newly-discovered reqs found by the deterministic discovery run
(`scripts/jobfinderos_discover.py`, or the `jobfinderos_ats_poll.py` shim), one block per run.

**How this works:** the run is deterministic up to the score step. It fetches each
registered board, filters (leadership title, location, comp floor), and diffs against a
seen-set. Entries marked `**Status:** unscored` came from a run without a score node; the
scout reads entries at or above the log threshold and writes opportunity notes.

Location flags: `clean` = remote / home region / country-wide. `⚠️` = hub-city-only at a
tier-1 (relocation) or unclear, verify.

---

"""


def inbox_path(paths: Any) -> Path:
    return paths.vault_dir / "Market Intel" / "ATS Inbox.md"


def render_entry(c: Candidate, filters: Filters, now: datetime) -> str:
    p = c["posting"]
    loc_note = "clean" if c.get("location_class") == "clean" else f"⚠️ {c.get('location_reason', '')}"
    comp = c.get("comp_range") or "not posted"
    if c.get("comp_status") == "unverified":
        comp += " — **comp unverified**"
    labels = filters.lane_labels or {}
    lane = f"Lane {c.get('lane', '')} ({labels.get(c.get('lane', ''), '')})"
    status = "unscored" if c.get("score") is None else "scored"
    lines = [
        f"### {p['company']} — {p['title']}",
        f"- **Status:** {status}",
    ]
    if c.get("score") is not None:
        lines.append(f"- **Score:** {c['score']}/10" + (f" — {c['score_rationale']}" if c.get("score_rationale") else ""))
    if c.get("match_mode") and c["match_mode"] != "title":
        lines.append(f"- **Match:** {c['match_mode']}")
    lines += [
        f"- **Location:** {p.get('location') or '(none listed)'} — {loc_note}",
        f"- **Comp:** {comp}",
        f"- **Lane:** {lane}",
        f"- **ATS:** {p['ats']} · req `{p['req_id']}`",
        f"- **Posting:** {p.get('url', '')}",
        f"- **Found:** {now.strftime('%Y-%m-%d %H:%M %Z')}",
    ]
    return "\n".join(lines) + "\n\n"


def write_inbox(paths: Any, candidates: list[Candidate], filters: Filters, now: datetime, *, path: Path | None = None) -> int:
    """Create the inbox with its header if missing, append one dated block. Returns the count."""
    target = path or inbox_path(paths)
    target = paths.assert_writable(target)
    if not target.exists():
        with paths.open_write(target) as fh:
            fh.write(INBOX_HEADER)
    if not candidates:
        return 0
    body = f"## {now.strftime('%Y-%m-%d')} — {len(candidates)} new\n\n"
    body += "".join(render_entry(c, filters, now) for c in candidates)
    with paths.open_write(target, "a") as fh:
        fh.write(body)
    return len(candidates)


def render_dry_run_table(candidates: list[Candidate]) -> str:
    rows = []
    for c in candidates:
        p = c["posting"]
        alert = "🔔" if c.get("location_class") == "clean" else "  "
        rows.append(
            f"  [NEW ]{alert} {p['company']:<13} {p['title'][:58]:<58} "
            f"| {p.get('location', '')[:34]:<34} | {c.get('location_class', ''):<8} | "
            f"{c.get('comp_status', ''):<10} {c.get('comp_range', '')}"
        )
    return "\n".join(rows)
