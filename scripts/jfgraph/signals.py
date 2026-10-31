"""Hiring signals (Graph D): score each company's likelihood of opening the candidate's
seat soon, promote hot companies to tier 1 so their postings are caught the day they
land, and write the weekly "About to Hire" list.

Signal sources, in order of structure:
  1. ``vault/Market Intel/Signals.jsonl``: one JSON line per signal, written by Mark
     (``mark-pulse`` step 5): {date, company, type, strength, source_url, note}.
  2. Derived from posting deltas: ``first_posting`` (a company had no leadership posting
     in the function last run and has one now) and ``exec_hire`` (a new Head/VP/Director
     posting in the function: a leader is being hired, team reqs usually follow).
  3. ``Jobs Handoff.json`` ``priority_now`` entries count as a ``buildout`` signal.

score = Σ weight[type] × strength × 0.5 ** (age_days / half_life_days)
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from .config import Filters
from .gates import SENIOR_LEADER_RE, matches_function, passes_seniority

DEFAULT_WEIGHTS = {"funding": 3, "exec_hire": 4, "buildout": 3, "rename": 2, "first_posting": 3, "layoff": -4}


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(str(s)[:10])
        except ValueError:
            return None


def function_leadership_counts(postings: list[dict], f: Filters) -> dict[str, int]:
    counts: dict[str, int] = {}
    for p in postings:
        t = p.get("title", "")
        if matches_function(t, f) and passes_seniority(t, f)[0]:
            counts[p["company"]] = counts.get(p["company"], 0) + 1
    return counts


def derived_signals(prev_counts: dict[str, int], raw_postings: list[dict], new_postings: list[dict], f: Filters, today: str) -> list[dict]:
    out: list[dict] = []
    now_counts = function_leadership_counts(raw_postings, f)
    for company, n in now_counts.items():
        if n > 0 and prev_counts.get(company, 0) == 0 and prev_counts:
            out.append({"date": today, "company": company, "type": "first_posting", "strength": 1.0, "source_url": "", "note": f"first leadership posting in the function ({n})", "derived": True})
    seen_exec = set()
    for p in new_postings:
        t = p.get("title", "")
        if p["company"] not in seen_exec and matches_function(t, f) and SENIOR_LEADER_RE.search(t):
            seen_exec.add(p["company"])
            out.append({"date": p.get("posted_at", "")[:10] or today, "company": p["company"], "type": "exec_hire", "strength": 1.0,
                        "source_url": p.get("url", ""), "note": f"new leader posting: {t}", "derived": True})
    return out


def handoff_signals(handoff: dict | None) -> list[dict]:
    out = []
    when = (handoff or {}).get("generated_at", "")[:10]
    for item in (handoff or {}).get("priority_now") or []:
        if isinstance(item, dict) and item.get("company"):
            out.append({"date": when, "company": item["company"], "type": "buildout", "strength": 1.0, "source_url": "", "note": item.get("why_now", ""), "derived": True})
    return out


def hiring_signal_score(company: str, signals: list[dict], today: date, *, weights: dict[str, float] | None = None, half_life_days: float = 45) -> float:
    weights = weights or DEFAULT_WEIGHTS
    total = 0.0
    for s in signals:
        if (s.get("company") or "").strip().lower() != company.strip().lower():
            continue
        w = float(weights.get(s.get("type", ""), 0))
        if not w:
            continue
        d = _parse_date(s.get("date")) or today
        age = max(0, (today - d).days)
        total += w * float(s.get("strength", 1) or 1) * (0.5 ** (age / max(half_life_days, 1)))
    return round(total, 3)


def score_all(signals: list[dict], companies: list[str], today: date, cfg: dict[str, Any]) -> dict[str, float]:
    names = {c for c in companies} | {s.get("company", "") for s in signals if s.get("company")}
    weights = {**DEFAULT_WEIGHTS, **(cfg.get("weights") or {})}
    return {c: hiring_signal_score(c, signals, today, weights=weights, half_life_days=float(cfg.get("half_life_days", 45))) for c in sorted(names) if c}


def promote(registry: list[dict], scores: dict[str, float], *, threshold: float, ttl_days: int, today: date) -> tuple[list[dict], list[str], list[str]]:
    """Set tier "1" and ``promoted_until`` for hot companies; restore the original tier once
    the promotion expires and the score has cooled below half the threshold."""
    out, promoted, demoted = [], [], []
    from datetime import timedelta
    for b in registry:
        b = dict(b)
        s = scores.get(b["company"], 0.0)
        until = _parse_date(b.get("promoted_until"))
        if s >= threshold:
            if str(b.get("tier")) != "1" or not until:
                if str(b.get("tier")) != "1":
                    b["original_tier"] = str(b.get("tier", "2"))
                b["tier"] = "1"
                promoted.append(b["company"])
            b["promoted_until"] = (today + timedelta(days=ttl_days)).isoformat()
            b["signal_score"] = s
        elif until and today > until and s < threshold / 2:
            b["tier"] = str(b.pop("original_tier", "2"))
            b.pop("promoted_until", None)
            b["signal_score"] = s
            demoted.append(b["company"])
        elif until:
            b["signal_score"] = s
        out.append(b)
    return out, promoted, demoted


def about_to_hire_rows(registry: list[dict], scores: dict[str, float], signals: list[dict], discover_cache: dict[str, dict] | None = None,
                       *, min_score: float = 1.0, limit: int = 15) -> list[dict]:
    by_board = {b["company"].strip().lower(): b for b in registry}
    cache = discover_cache or {}
    rows = []
    for company, s in sorted(scores.items(), key=lambda kv: -kv[1]):
        if s < min_score:
            continue
        top = sorted([x for x in signals if (x.get("company") or "").strip().lower() == company.strip().lower()],
                     key=lambda x: (x.get("date") or ""), reverse=True)[:3]
        b = by_board.get(company.strip().lower())
        if b:
            board = f"registered ({b.get('ats')}/{b.get('slug')}, tier {b.get('tier')})"
        else:
            c = cache.get(company.strip().lower().replace("  ", " "), {}) or next((v for k, v in cache.items() if v.get("company", "").lower() == company.lower()), {})
            board = f"discovery: {c.get('status')}" if c else "no board registered"
        rows.append({"company": company, "score": s, "signals": top, "board": board,
                     "play": "warm path now: name the hiring leader before the req posts" if s >= 5 else "watch; re-check after the next signal"})
        if len(rows) >= limit:
            break
    return rows


def render_about_to_hire(rows: list[dict], today: str, *, promoted: list[str], threshold: float) -> str:
    lines = [f"> **JobFinderOS:** Discovery · About to Hire · {today} · [[Dashboard]] · [[Strategy]]", "", f"# About to Hire — {today}", "",
             f"Hiring-signal score per company (funding, leader hires, build-outs, renames, first postings; decays with a 45-day half-life). "
             f"Companies at or above {threshold:g} are promoted to tier 1 in the board registry so their postings surface the day they land.", ""]
    if promoted:
        lines += [f"**Promoted this run:** {', '.join(promoted)}", ""]
    lines += ["| Company | Score | Latest signals | Board | Play |", "|---|---|---|---|---|"]
    for r in rows:
        sig = "; ".join(f"{s.get('type')} {s.get('date', '')[:10]}" + (f" ([src]({s['source_url']}))" if s.get("source_url") else "") for s in r["signals"]) or "–"
        lines.append(f"| {r['company']} | {r['score']:.1f} | {sig} | {r['board']} | {r['play']} |")
    if not rows:
        lines.append("| – | – | no signals above the floor | – | – |")
    return "\n".join(lines) + "\n"
