"""The node functions. Each takes ``(ctx, state)`` and returns a partial update.

Phase 2 holds the nodes the ``poller`` preset needs; discovery, classification,
scoring, and signals arrive with Graphs B, C, D.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import hashlib

from .ats_adapters import BOARD_URLS, FETCHERS, comp_source_text, fetch_board, fetch_detail
from .classify import (CLASSIFY_SCHEMA, LEARNED_FILENAME, NEAR_MISS_LOG, append_near_misses, learn_terms, load_learned,
                       load_near_miss_verdicts, save_learned)
from .discover import discover_board
from .gates import classify_location, lane_for, matches_function, parse_comp, passes_leadership, passes_seniority
from .llm import LLMCall, PromptLibrary
from .profile import board_from_url, candidate_brief, role_titles
from .registry import handoff_companies, merge_sources, norm_company, registry_changed, write_boards_yaml
from .review import review_note_path, write_review_note
from .signals import about_to_hire_rows, derived_signals, function_leadership_counts, handoff_signals, promote, render_about_to_hire, score_all
from .runtime import AwaitHuman, RunContext, State, Update, fan_out
from .state import Candidate, decision
from .vault_writer import render_dry_run_table, write_inbox

SEEN_FILENAME = "ats_seen.json"
SEEN_VERSION = 2
DISCOVER_CACHE_FILENAME = "discover_cache.json"
FUNCTION_COUNTS_FILENAME = "function_counts.json"
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


# ------------------------------------------------------------------ seen-set

def seen_path(paths: Any) -> Path:
    return paths.state_dir / SEEN_FILENAME


def load_seen(path: Path) -> dict[str, dict[str, Any]]:
    """v2: ``{"seen": {req_id: {...}}}``. v1 (a list of survivor ids) migrates on read."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    seen = data.get("seen")
    if isinstance(seen, list):
        return {rid: {"first_seen": None, "last_stage": "legacy", "last_outcome": "pass", "last_run": data.get("last_run")} for rid in seen}
    return dict(seen or {})


def save_seen(paths: Any, index: dict[str, dict[str, Any]], *, now: datetime, note: str = "") -> None:
    payload = {"version": SEEN_VERSION, "count": len(index), "last_run": now.isoformat(), "note": note, "seen": dict(sorted(index.items()))}
    with paths.open_write(seen_path(paths)) as fh:
        json.dump(payload, fh, indent=1)


def read_seen_summary(paths: Any) -> dict[str, Any] | None:
    path = seen_path(paths)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    seen = data.get("seen")
    return {"count": len(seen) if isinstance(seen, (list, dict)) else data.get("count", 0), "last_run": data.get("last_run", "never"), "note": data.get("note", "")}


# ------------------------------------------------------------------ helpers

def _now(ctx: RunContext) -> datetime:
    return ctx.clock()


def _f(ctx: RunContext):
    return ctx.cfg.filters


def _dry(ctx: RunContext) -> bool:
    return bool(ctx.extra.get("dry_run"))


def _candidate(posting: dict, lane: str, match_mode: str) -> Candidate:
    return {"posting": posting, "lane": lane, "match_mode": match_mode, "flags": []}


# -------------------------------------------------------------------- nodes

def load_context(ctx: RunContext, state: State) -> Update:
    registry = [{**b, "source": b.get("source", "yaml")} for b in ctx.cfg.boards]
    seen_index = load_seen(seen_path(ctx.paths))
    handoff: dict = {}
    hpath = ctx.paths.vault_dir / "Market Intel" / "Jobs Handoff.json"
    if hpath.is_file():
        try:
            handoff = json.loads(hpath.read_text(encoding="utf-8")) or {}
        except (OSError, ValueError) as exc:
            ctx.log(f"WARN: could not read Jobs Handoff.json ({exc})")
    signals: list[dict] = []
    spath = ctx.paths.vault_dir / "Market Intel" / "Signals.jsonl"
    if spath.is_file():
        for line in spath.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    signals.append(json.loads(line))
                except ValueError:
                    continue
    cache: dict = {}
    cpath = ctx.paths.state_dir / DISCOVER_CACHE_FILENAME
    if cpath.is_file():
        try:
            cache = json.loads(cpath.read_text(encoding="utf-8")) or {}
        except (OSError, ValueError):
            cache = {}
    learned = load_learned(ctx.paths.state_dir / LEARNED_FILENAME)
    verdicts = load_near_miss_verdicts(ctx.paths.state_dir / NEAR_MISS_LOG)
    prev_counts: dict = {}
    fpath = ctx.paths.state_dir / FUNCTION_COUNTS_FILENAME
    if fpath.is_file():
        try:
            prev_counts = (json.loads(fpath.read_text(encoding="utf-8")) or {}).get("counts") or {}
        except (OSError, ValueError):
            prev_counts = {}
    active = [b for b in registry if b.get("enabled") is not False]
    ctx.log(f"sweeping {len(active)} boards ({len(registry) - len(active)} disabled)"
            + (f"; {len([t for t, r in learned.items() if r.get('status') == 'accepted'])} learned term(s) active" if learned else ""))
    return {"registry": registry, "seen_index": seen_index, "seen_ids": sorted(seen_index), "handoff": handoff,
            "signals": signals, "review_answers": [], "discover_cache": cache, "sync_passes": 0,
            "learned_records": learned, "near_miss_verdicts": verdicts, "prev_function_counts": prev_counts}


def _fetch_one_board(ctx: RunContext, board: dict) -> Update:
    if board.get("enabled") is False:
        return {}
    if board.get("ats") not in FETCHERS:
        return {"problems": [f"{board['company']}: no fetcher for ats={board.get('ats')}"]}
    try:
        postings = fetch_board(board, ctx.transport)
    except Exception as exc:  # noqa: BLE001  (a bad board never kills the run)
        return {"problems": [f"{board['company']}: fetch failed ({exc})"]}
    ctx.log(f"  {board['company']:<14} {len(postings):>4} reqs")
    return {"raw_postings": postings}


def fetch_boards(ctx: RunContext, state: State) -> Update:
    workers = int((ctx.cfg.concurrency or {}).get("http_workers", 4))
    return fan_out("registry", _fetch_one_board, out_key="raw_postings", max_workers=workers)(ctx, state)


def diff_seen(ctx: RunContext, state: State) -> Update:
    seen_index = state.get("seen_index") or {}
    reconsider = bool(ctx.extra.get("reconsider_dropped"))
    new, decisions = [], []
    for p in state.get("raw_postings") or []:
        info = seen_index.get(p["req_id"])
        if info is None or (reconsider and info.get("last_outcome") == "drop"):
            new.append(p)
        else:
            decisions.append(decision(p["req_id"], "seen", "drop", "already seen", "diff_seen"))
    return {"new_postings": new, "decisions": decisions}


def gate_title(ctx: RunContext, state: State) -> Update:
    f = _f(ctx)
    near_enabled = bool(ctx.extra.get("near_miss_enabled"))
    title_pass, near, decisions = [], [], []
    for p in state.get("new_postings") or []:
        ok, reason = passes_seniority(p["title"], f)
        if not ok:
            decisions.append(decision(p["req_id"], "title", "drop", reason, "gate_title"))
            continue
        if not matches_function(p["title"], f):
            if near_enabled:
                near.append(p)
                decisions.append(decision(p["req_id"], "near_miss", "flag", "function mismatch", "gate_title"))
            else:
                decisions.append(decision(p["req_id"], "title", "drop", "function mismatch", "gate_title"))
            continue
        title_pass.append(_candidate(p, lane_for(p["title"], f), "title"))
        decisions.append(decision(p["req_id"], "title", "pass", "", "gate_title"))
    return {"title_pass": title_pass, "near_misses": near, "decisions": decisions}


def gate_location(ctx: RunContext, state: State) -> Update:
    f = _f(ctx)
    out, decisions = [], []
    for c in list(state.get("title_pass") or []) + list(state.get("resp_confirmed") or []):
        p = c["posting"]
        cls, reason = classify_location(p.get("location", ""), p.get("tier"), f)
        if cls == "drop":
            decisions.append(decision(p["req_id"], "location", "drop", reason, "gate_location"))
            continue
        c = {**c, "location_class": cls, "location_reason": reason}
        out.append(c)
        decisions.append(decision(p["req_id"], "location", "pass" if cls == "clean" else "flag", reason, "gate_location"))
    return {"location_pass": out, "decisions": decisions}


def _fetch_detail_for(ctx: RunContext, c: Candidate) -> Update:
    p = c["posting"]
    if p.get("ats") == "greenhouse" and not p.get("description"):
        try:
            desc = fetch_detail(p, ctx.transport)
            c = {**c, "posting": {**p, "description": desc}}
        except Exception as exc:  # noqa: BLE001  (keep the candidate; the comp gate fails open)
            c = {**c, "flags": [*c.get("flags", []), f"detail fetch failed: {exc}"]}
    return {"location_pass": [c]}


def fetch_description(ctx: RunContext, state: State) -> Update:
    workers = int((ctx.cfg.concurrency or {}).get("http_workers", 4))
    return fan_out("location_pass", _fetch_detail_for, out_key="location_pass", max_workers=workers)(ctx, state)


def gate_comp(ctx: RunContext, state: State) -> Update:
    f = _f(ctx)
    rule = str((getattr(ctx.cfg, "gates", None) or {}).get("comp_rule", "midpoint"))
    out, decisions = [], []
    for c in state.get("location_pass") or []:
        p = c["posting"]
        status, rng = parse_comp(comp_source_text(p), f.min_comp, rule)
        c = {**c, "comp_status": status, "comp_range": rng}
        if status == "under":
            decisions.append(decision(p["req_id"], "comp", "drop", "below comp floor", "gate_comp"))
            continue
        out.append(c)
        decisions.append(decision(p["req_id"], "comp", "pass" if status == "clear" else "flag", status, "gate_comp"))
    return {"comp_pass": out, "decisions": decisions}


def finalize_unscored(ctx: RunContext, state: State) -> Update:
    """The poller preset has no score node: everything that cleared the gates surfaces."""
    return {"surfaced": list(state.get("comp_pass") or []), "act_now": []}


def write_vault(ctx: RunContext, state: State) -> Update:
    surfaced = list(state.get("surfaced") or [])
    for p in state.get("problems") or []:
        ctx.log(f"PROBLEM: {p}")
    pending = state.get("review_pending") or []
    if pending and not _dry(ctx):
        n = write_review_note(ctx.paths, review_note_path(ctx.paths, ctx.cfg.review), pending, now_text=_now(ctx).strftime("%Y-%m-%d %H:%M"))
        ctx.log(f"{n} item(s) waiting in the Discovery Review note")
    if state.get("about_to_hire") is not None and (ctx.extra.get("weekly") or ctx.sim) and not _dry(ctx):
        today = state.get("run_date") or _now(ctx).strftime("%Y-%m-%d")
        text = render_about_to_hire(state.get("about_to_hire") or [], today, promoted=state.get("promoted") or [],
                                    threshold=float((ctx.cfg.signals or {}).get("promote_threshold", 5.0)))
        with ctx.paths.open_write(ctx.paths.vault_dir / "Market Intel" / f"About to Hire — {today}.md") as fh:
            fh.write(text)
    if ctx.extra.get("seed"):
        ctx.log(f"SEEDED: {len(state.get('raw_postings') or [])} currently-live reqs will be marked seen; nothing written to the inbox.")
        if not _dry(ctx):
            write_inbox(ctx.paths, [], _f(ctx), _now(ctx))
        return {"inbox_written": 0}
    if _dry(ctx):
        ctx.log(f"DRY RUN - {len(surfaced)} would be NEW")
        if surfaced:
            ctx.log(render_dry_run_table(surfaced))
        return {"inbox_written": 0}
    if not surfaced:
        ctx.log("no new reqs")
        write_inbox(ctx.paths, [], _f(ctx), _now(ctx))
        return {"inbox_written": 0}
    tight = [c for c in surfaced if c.get("location_class") == "clean"]
    ctx.log(f"{len(surfaced)} NEW req(s); {len(tight)} tight-match (clean location)")
    for c in surfaced:
        p = c["posting"]
        ctx.log(f"  NEW: {p['company']} - {p['title']} [{c.get('location_class')}] {c.get('comp_status')} {c.get('comp_range', '')}")
    n = write_inbox(ctx.paths, surfaced, _f(ctx), _now(ctx))
    if tight:
        head = tight[0]["posting"]
        title = f"{len(tight)} new matching req" + ("s" if len(tight) > 1 else "")
        body = f"{head['company']} — {head['title']}" + (f"  (+{len(tight) - 1} more)" if len(tight) > 1 else "")
        try:
            ctx.notifier.notify(title, body)
        except Exception as exc:  # noqa: BLE001
            ctx.log(f"WARN: notification failed ({exc})")
    return {"inbox_written": n}


# ----------------------------------------------------- Graph A: the LLM scout baseline

SCOUT_ALLOWED_TOOLS = ("Read", "Write", "Edit", "Glob", "Grep", "WebFetch", "WebSearch", "Agent")
URL_PATTERNS = [
    ("greenhouse", re.compile(r"(?:job-)?boards(?:-api)?\.greenhouse\.io/(?:v1/boards/)?(?P<slug>[^/?#]+)/jobs/(?P<id>\d+)", re.I)),
    ("greenhouse", re.compile(r"gh_jid=(?P<id>\d+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/(?P<slug>[^/?#]+)/(?P<id>[0-9a-f-]{36})", re.I)),
    ("lever", re.compile(r"jobs\.lever\.co/(?P<slug>[^/?#]+)/(?P<id>[0-9a-f-]{36})", re.I)),
]


def collect_opportunity_notes(vault_dir: Path) -> list[dict[str, str]]:
    notes = []
    companies = vault_dir / "Companies"
    if not companies.is_dir():
        return notes
    for path in sorted(companies.rglob("*.md")):
        if path.name == "README.md" or " — " not in path.stem:
            continue
        try:
            notes.append({"path": str(path.relative_to(vault_dir)), "text": path.read_text(encoding="utf-8")})
        except OSError:
            continue
    return notes


def scout_baseline(ctx: RunContext, state: State) -> Update:
    """Run today's `/jobs-scout` skill unchanged, in the simulation workspace, through the
    LLM client (so tokens are captured and the run is replayable). The opportunity notes it
    writes are captured into the state, and a sidecar next to the cache entry keeps them
    for replays."""
    from .llm import LLMCall

    workspace = Path(ctx.extra.get("workspace") or ctx.paths.scratch or ctx.paths.root)
    call = LLMCall(
        node="scout_baseline", prompt_name="jobs-scout", prompt="/jobs-scout", prompt_via_stdin=False, schema=None,
        model=str((ctx.cfg.llm or {}).get("model", "sonnet")), tools=None, allowed_tools=SCOUT_ALLOWED_TOOLS,
        permission_mode="acceptEdits", cwd=str(workspace),
        max_budget_usd=float((ctx.cfg.llm or {}).get("scout_budget_usd", 4.0)),
        timeout_s=int((ctx.cfg.llm or {}).get("scout_timeout_seconds", 1800)), sample=int(ctx.extra.get("sample", 0)),
    )
    sidecar = ctx.llm.cache_path(call).with_suffix(".vault.json")
    result = ctx.llm.call(call)
    if result.cached and sidecar.is_file():
        notes = json.loads(sidecar.read_text(encoding="utf-8"))
    else:
        notes = collect_opportunity_notes(ctx.paths.vault_dir)
        with ctx.paths.open_write(sidecar) as fh:
            json.dump(notes, fh)
    ctx.log(f"scout baseline: {len(notes)} opportunity notes, est. ${result.cost_usd:.2f}, {result.num_turns} turns"
            + (" (replayed)" if result.cached else "") + (" ERROR: " + result.text[:200] if result.is_error else ""))
    return {"scout_notes": notes, "scout_result": {"is_error": result.is_error, "cost_usd": result.cost_usd, "num_turns": result.num_turns,
                                                   "cached": result.cached, "text": result.text[-2000:]}}


def _note_field(text: str, name: str) -> str:
    m = re.search(r"\*\*" + re.escape(name) + r":\*\*\s*(.+)", text)
    return m.group(1).strip() if m else ""


def _urls_in(text: str) -> list[str]:
    return re.findall(r"https?://[^\s)\]>\"']+", text)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def map_url_to_posting(urls: list[str], postings: list[dict], *, company: str = "", title: str = "") -> dict | None:
    """Exact URL, then ATS id parsed from any URL, then company + title (the scout often cites
    the employer's own careers page instead of the ATS link)."""
    by_url = {p.get("url", ""): p for p in postings if p.get("url")}
    by_key = {(p["ats"], p["req_id"].split(":", 1)[1]): p for p in postings}
    for u in urls:
        if u in by_url:
            return by_url[u]
    for u in urls:
        for ats, pat in URL_PATTERNS:
            m = pat.search(u)
            if m and (ats, m.group("id")) in by_key:
                return by_key[(ats, m.group("id"))]
    if title:
        nt, nc = _norm(title), _norm(company)
        for p in postings:
            if _norm(p.get("title", "")) == nt and (not nc or _norm(p.get("company", "")) in nc or nc in _norm(p.get("company", ""))):
                return p
    return None


def parse_scout_output(ctx: RunContext, state: State) -> Update:
    """Turn the scout's opportunity notes into Candidates keyed by req_id."""
    postings = list(state.get("raw_postings") or [])
    act_floor = float(ctx.cfg.thresholds.act)
    surfaced, act, outside, decisions = [], [], [], []
    for note in state.get("scout_notes") or []:
        text = note["text"]
        score_m = re.search(r"\*\*Score:\*\*\s*([\d.]+)", text)
        score = float(score_m.group(1)) if score_m else None
        urls = _urls_in(_note_field(text, "Job URL")) + _urls_in(_note_field(text, "Posting")) + _urls_in(text)
        posting = map_url_to_posting(urls, postings, company=_note_field(text, "Company"), title=_note_field(text, "Role"))
        cand = {"posting": posting or {"company": _note_field(text, "Company"), "title": _note_field(text, "Role"), "url": urls[0] if urls else "", "req_id": ""},
                "score": score, "match_mode": "scout_baseline", "flags": [], "note_path": note["path"]}
        if posting is None:
            outside.append(cand)
            continue
        surfaced.append(cand)
        decisions.append(decision(posting["req_id"], "score", "pass", f"scout note {score}", "parse_scout_output"))
        if score is not None and score >= act_floor:
            act.append(cand)
    ctx.log(f"scout notes mapped: {len(surfaced)} in registry, {len(outside)} outside, {len(act)} at/above act threshold")
    return {"surfaced": surfaced, "act_now": act, "surfaced_outside_registry": outside, "decisions": decisions}


def persist(ctx: RunContext, state: State) -> Update:
    if _dry(ctx):
        return {}
    last: dict[str, dict] = {}
    for d in state.get("decisions") or []:
        last[d["req_id"]] = d
    index = dict(state.get("seen_index") or {})
    today = state.get("run_date") or _now(ctx).strftime("%Y-%m-%d")
    for p in state.get("raw_postings") or []:
        rid = p["req_id"]
        d = last.get(rid)
        if d is not None and d["stage"] == "seen":
            continue                                  # unchanged: keep the existing record
        prev = index.get(rid) or {}
        index[rid] = {
            "first_seen": prev.get("first_seen") or today,
            "last_stage": d["stage"] if d else "unknown",
            "last_outcome": d["outcome"] if d else "unknown",
            "last_run": state.get("run_id"),
        }
    surfaced = len(state.get("surfaced") or [])
    note = "seeded from live boards at install" if ctx.extra.get("seed") else (f"{surfaced} new on {today}" if surfaced else "no new reqs")
    save_seen(ctx.paths, index, now=_now(ctx), note=note)

    # registry: write back when discovery or promotion changed it
    registry = list(state.get("registry") or [])
    if registry and registry_changed(ctx.cfg.boards, registry):
        write_boards_yaml(ctx.paths, ctx.cfg.raw_boards, registry)
        ctx.log(f"registry written: {len(registry)} boards ({sum(1 for b in registry if b.get('source') == 'discover')} discovered)")
    # discovery cache: results plus web-sweep companies still waiting for a board
    cache = dict(state.get("discover_cache") or {})
    for row in state.get("web_sweep_companies") or []:
        key = norm_company(row.get("company", ""))
        if key and key not in cache and not any(norm_company(b["company"]) == key for b in registry):
            cache[key] = {"company": row["company"], "status": "pending", "ats": row.get("ats"), "slug": row.get("slug"), "checked": today}
    if cache:
        with ctx.paths.open_write(ctx.paths.state_dir / DISCOVER_CACHE_FILENAME) as fh:
            json.dump(cache, fh, indent=1)
    # learned terms and the near-miss log (Graph C)
    if state.get("learned_records"):
        save_learned(ctx.paths, ctx.paths.state_dir / LEARNED_FILENAME, state["learned_records"])
    append_near_misses(ctx.paths, ctx.paths.state_dir / NEAR_MISS_LOG, state.get("near_miss_log") or [])
    # function counts for next run's derived signals (Graph D)
    if state.get("raw_postings"):
        counts = function_leadership_counts(state["raw_postings"], _f(ctx))
        with ctx.paths.open_write(ctx.paths.state_dir / FUNCTION_COUNTS_FILENAME) as fh:
            json.dump({"date": today, "run_id": state.get("run_id"), "counts": counts}, fh, indent=1)
    return {"seen_index": index, "seen_ids": sorted(index), "discover_cache": cache}


# ------------------------------------------------------ Graph D: hiring signals and promotion

def score_signals(ctx: RunContext, state: State) -> Update:
    cfg_s = ctx.cfg.signals or {}
    if not cfg_s.get("enabled", True):
        return {"signal_scores": {}, "promoted": [], "about_to_hire": []}
    f = _f(ctx)
    today_s = state.get("run_date") or _now(ctx).strftime("%Y-%m-%d")
    today = datetime.fromisoformat(today_s).date()
    signals = list(state.get("signals") or []) + handoff_signals(state.get("handoff")) \
        + derived_signals(state.get("prev_function_counts") or {}, state.get("raw_postings") or [], state.get("new_postings") or [], f, today_s)
    registry = list(state.get("registry") or [])
    scores = score_all(signals, [b["company"] for b in registry], today, cfg_s)
    registry, promoted, demoted = promote(registry, scores, threshold=float(cfg_s.get("promote_threshold", 5.0)),
                                          ttl_days=int(cfg_s.get("promote_ttl_days", 90)), today=today)
    tier_by = {b["company"]: str(b.get("tier", "2")) for b in registry}
    retag = lambda ps: [{**p, "tier": tier_by.get(p["company"], p.get("tier", ""))} for p in ps]  # noqa: E731
    rows = about_to_hire_rows(registry, scores, signals, state.get("discover_cache") or {})
    ctx.log(f"signals: {len(signals)} ({sum(1 for s in signals if s.get('derived'))} derived); "
            f"{len([v for v in scores.values() if v >= float(cfg_s.get('promote_threshold', 5.0))])} at/above the promotion threshold; "
            f"promoted {promoted or 'none'}" + (f"; demoted {demoted}" if demoted else ""))
    return {"signals": signals, "signal_scores": scores, "registry": registry, "promoted": promoted, "about_to_hire": rows,
            "raw_postings": retag(state.get("raw_postings") or []), "new_postings": retag(state.get("new_postings") or [])}


# --------------------------------------- Graph C: responsibility classifier + learning loop

def _verdict_to_update(ctx: RunContext, p: dict, verdict: dict, *, replayed: bool) -> Update:
    out: Update = {"near_miss_log": [] if replayed else [verdict],
                   "decisions": [decision(p["req_id"], "classify", "pass" if verdict["fit"] == "yes" else ("flag" if verdict["fit"] == "unsure" else "drop"),
                                          f"responsibility {verdict['fit']}", "classify_responsibility")]}
    if verdict["fit"] == "yes":
        out["resp_confirmed"] = [{"posting": p, "lane": lane_for(verdict.get("function_noun") or p.get("title", ""), _f(ctx)),
                                  "match_mode": "responsibility", "title_term": verdict.get("title_term", ""), "evidence": verdict.get("evidence", ""),
                                  "flags": ["title outside the function terms; matched on responsibilities"]}]
    return out


def _classify_batch(ctx: RunContext, batch: list[dict]) -> Update:
    """One LLM call for a batch of near misses. Descriptions are fetched first (Greenhouse)."""
    cfg_c = ctx.cfg.classifier or {}
    desc_chars = int(cfg_c.get("description_chars", 3000))
    enriched = []
    for p in batch:
        desc = p.get("description") or ""
        if p.get("ats") == "greenhouse" and not desc:
            try:
                desc = fetch_detail(p, ctx.transport)
            except Exception as exc:  # noqa: BLE001
                desc = ""
                ctx.log(f"  classify: no description for {p['req_id']} ({exc})")
        enriched.append({**p, "description": desc})
    template, version = PromptLibrary(PROMPTS_DIR).load("classify_responsibility")
    blocks = [f"### {p['req_id']}\n- Title: {p.get('title', '')}\n- Company: {p.get('company', '')}\n- Location: {p.get('location') or '(none)'}\n"
              f"- Description: {(p.get('description') or '(no description available)')[:desc_chars]}\n" for p in enriched]
    prompt = template.replace("{candidate_brief}", candidate_brief(_profile_text(ctx))).replace("{postings}", "\n".join(blocks))
    call = LLMCall(node="classify_responsibility", prompt_name="classify_responsibility", prompt_version=version, prompt=prompt, schema=CLASSIFY_SCHEMA,
                   model=str((ctx.cfg.llm or {}).get("model", "sonnet")), tools=(), max_budget_usd=float(cfg_c.get("max_budget_usd", 0.40)),
                   timeout_s=600, sample=int(ctx.extra.get("sample", 0)))
    failure = None
    try:
        result = ctx.llm.call(call)
        if result.is_error:
            failure = f"claude error: {(result.raw or {}).get('terminal_reason') or result.text[:120]}"
        got = {v["req_id"]: v for v in ((result.structured or {}).get("verdicts") or []) if isinstance(v, dict)}
        if not got and failure is None:
            failure = "no verdicts in the structured output"
        cached = result.cached
    except Exception as exc:  # noqa: BLE001
        if type(exc).__name__ == "LiveBudgetExceeded":
            raise
        got, cached, failure = {}, False, f"{type(exc).__name__}: {exc}"
    if failure:
        # a failed call leaves no verdicts on record, so these postings are retried next run
        ctx.log(f"  classify batch of {len(enriched)} failed ({failure}); will retry")
        return {"decisions": [decision(p["req_id"], "classify", "flag", "classifier call failed", "classify_responsibility") for p in enriched],
                "problems": [f"classify_responsibility: {failure}"]}
    merged: Update = {}
    for p in enriched:
        s = got.get(p["req_id"]) or {}
        verdict = {"req_id": p["req_id"], "company": p.get("company"), "title": p.get("title"), "fit": s.get("fit", "unsure"),
                   "function_noun": s.get("function_noun", ""), "title_term": s.get("title_term", ""),
                   "confidence": float(s.get("confidence", 0) or 0), "evidence": str(s.get("evidence", ""))[:300], "run": ctx.run_id, "cached": cached}
        for k, v in _verdict_to_update(ctx, p, verdict, replayed=False).items():
            merged.setdefault(k, []).extend(v)
    return merged


def classify_responsibility(ctx: RunContext, state: State) -> Update:
    cfg_c = ctx.cfg.classifier or {}
    near = list(state.get("near_misses") or [])
    if not near or not cfg_c.get("enabled", True) or ctx.llm is None or _dry(ctx):
        return {"resp_confirmed": [], "near_miss_log": []}
    verdicts = state.get("near_miss_verdicts") or {}
    # replay verdicts already on record
    up: Update = {"resp_confirmed": [], "near_miss_log": [], "decisions": []}
    fresh = []
    replayed = 0
    for p in near:
        prior = verdicts.get(p["req_id"])
        if prior and prior.get("fit") in ("yes", "no"):
            replayed += 1
            for k, v in _verdict_to_update(ctx, p, prior, replayed=True).items():
                up.setdefault(k, []).extend(v)
        else:
            fresh.append(p)
    # restrict / prioritize what the LLM sees, then cap
    only_file = cfg_c.get("only_req_ids_file")
    if only_file and Path(only_file).is_file():
        allowed = {json.loads(l).get("req_id") for l in Path(only_file).read_text(encoding="utf-8").splitlines() if l.strip()}
        fresh = [p for p in fresh if p["req_id"] in allowed]
    hint = cfg_c.get("hint_terms")
    if hint:
        hint_re = re.compile("(" + str(hint) + ")", re.I)
        fresh.sort(key=lambda p: 0 if hint_re.search(p.get("title", "")) else 1)
    cap = int(cfg_c.get("max_per_run", 60))
    todo, deferred = fresh[:cap], fresh[cap:]
    size = max(1, int(cfg_c.get("batch", 20)))
    batches = [todo[i:i + size] for i in range(0, len(todo), size)]
    workers = int((ctx.cfg.concurrency or {}).get("llm_workers", 3))
    if batches:
        got = fan_out("classify_batches", _classify_batch, max_workers=workers)(ctx, {**state, "classify_batches": batches})
        for k, v in got.items():
            if isinstance(v, list):
                up.setdefault(k, []).extend(v)
    for p in deferred:
        up["decisions"].append(decision(p["req_id"], "classify", "flag", "deferred: classifier cap", "classify_responsibility"))
    ctx.log(f"classifier: {len(todo)} near miss(es) in {len(batches)} call(s), {replayed} replayed verdicts, "
            f"{len(up['resp_confirmed'])} confirmed, {len(deferred)} deferred")
    return up


def learn_terms_node(ctx: RunContext, state: State) -> Update:
    cfg_c = ctx.cfg.classifier or {}
    records, changed, review = learn_terms(
        state.get("resp_confirmed") or [], state.get("learned_records") or {}, _f(ctx),
        min_companies=int(cfg_c.get("min_companies", 2)), require_review=bool(cfg_c.get("require_review", False)),
        today=state.get("run_date", ""), run_id=ctx.run_id,
    )
    if changed:
        ctx.log("learned terms: " + ", ".join(f"{r['term']} ({r['status']}, {len(r['companies'])} co)" for r in changed))
    return {"learned_records": records, "learned_terms": changed, "review_pending": list(state.get("review_pending") or []) + review}


def review_terms(ctx: RunContext, state: State) -> Update:
    pending = list(state.get("review_pending") or [])
    terms = [i for i in pending if i.get("kind") == "term"]
    if not terms:
        return {}
    records = {k: dict(v) for k, v in (state.get("learned_records") or {}).items()}
    remaining = []
    for item in terms:
        term = item["payload"]["term"]
        ans = ctx.review.answer(item) if ctx.review is not None else None
        if ans == "accept" and term in records:
            records[term]["status"] = "accepted"
            records[term]["accepted_on"] = state.get("run_date", "")
        elif ans == "reject" and term in records:
            records[term]["status"] = "rejected"
        else:
            remaining.append(item)
    others = [i for i in pending if i.get("kind") != "term"]
    if remaining and (ctx.cfg.review or {}).get("block_on_pending"):
        write_review_note(ctx.paths, review_note_path(ctx.paths, ctx.cfg.review), others + remaining)
        raise AwaitHuman("terms", f"{len(remaining)} learned term(s) waiting for review")
    return {"learned_records": records, "review_pending": others + remaining}


# ------------------------------------------- Graph B: registry, discovery, web sweep, score

def _profile_text(ctx: RunContext) -> str:
    path = ctx.paths.config_dir / "profile.md"
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _rubric_text(ctx: RunContext) -> str:
    path = ctx.paths.config_dir / "scoring_rubric.md"
    return path.read_text(encoding="utf-8") if path.is_file() else "(no rubric; score role fit, company fit, comp, skills, growth, location equally)"


def sync_registry(ctx: RunContext, state: State) -> Update:
    cfg_d = ctx.cfg.discover or {}
    passes = int(state.get("sync_passes", 0)) + 1
    cache = state.get("discover_cache") or {}
    extra = list(state.get("web_sweep_companies") or [])
    for key, info in cache.items():
        if info.get("status") == "pending":
            extra.append({"company": info.get("company", key), "ats": info.get("ats"), "slug": info.get("slug")})
    reg, pending = merge_sources(
        state.get("registry") or [], profile_text=_profile_text(ctx), handoff=state.get("handoff"), vault_dir=ctx.paths.vault_dir,
        extra_companies=extra, sources=cfg_d.get("sources"), default_tier=str(cfg_d.get("default_tier", "2")), known_missing=cache,
    )
    # extra companies that already carry a board (a web-sweep posting URL told us) enroll directly
    for row in extra:
        if row.get("ats") and row.get("slug") and row.get("company") in pending:
            reg.append({"company": row["company"], "ats": row["ats"], "slug": row["slug"], "tier": str(cfg_d.get("default_tier", "2")), "enabled": True, "source": "web_sweep"})
            pending.remove(row["company"])
    if not cfg_d.get("enabled", True):
        pending = []
    ctx.log(f"registry: {len(reg)} boards, {len(pending)} compan{'y' if len(pending) == 1 else 'ies'} need a board (pass {passes})")
    return {"registry": reg, "pending_discovery": pending, "sync_passes": passes}


def _discover_one(ctx: RunContext, company: str) -> Update:
    res = discover_board(company, ctx.transport, ctx.cfg.discover or {}, overrides=(ctx.cfg.discover or {}).get("slug_overrides"))
    return {"discover_results": [res]}


def discover_boards(ctx: RunContext, state: State) -> Update:
    workers = int((ctx.cfg.concurrency or {}).get("http_workers", 4))
    up = fan_out("pending_discovery", _discover_one, max_workers=workers)(ctx, state)
    results = up.get("discover_results") or []
    cfg_d = ctx.cfg.discover or {}
    reg = list(state.get("registry") or [])
    names = {norm_company(b["company"]) for b in reg}
    pending_review = list(state.get("review_pending") or [])
    cache = dict(state.get("discover_cache") or {})
    today = state.get("run_date") or ""
    tier_by = {norm_company(r["company"]): r["tier"] for r in handoff_companies(state.get("handoff") or {})}
    counts: dict[str, int] = {}
    for r in results:
        key = norm_company(r["company"])
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        cache[key] = {"company": r["company"], "status": r["status"], "ats": r.get("ats"), "slug": r.get("slug"), "confidence": r.get("confidence"), "checked": today}
        if r["status"] == "found" and key not in names:
            reg.append({"company": r["company"], "ats": r["ats"], "slug": r["slug"], "tier": tier_by.get(key, str(cfg_d.get("default_tier", "2"))),
                        "enabled": True, "source": "discover", "confidence": r["confidence"], "discovered_at": today})
            names.add(key)
        elif r["status"] == "ambiguous":
            url = BOARD_URLS[r["ats"]].format(slug=r["slug"]) if r.get("ats") in BOARD_URLS else ""
            pending_review.append({"id": f"board:{key.replace(' ', '-')}", "kind": "board",
                                   "text": f"**{r['company']}** → {r['ats']}/{r['slug']} (confidence {float(r['confidence']):.2f}) · {url}", "payload": r})
    ctx.log("discovery: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) if counts else "discovery: nothing to probe")
    return {**up, "registry": reg, "review_pending": pending_review, "discover_cache": cache, "pending_discovery": []}


def review_boards(ctx: RunContext, state: State) -> Update:
    pending = list(state.get("review_pending") or [])
    boards = [i for i in pending if i.get("kind") == "board"]
    if not boards:
        return {}
    reg = list(state.get("registry") or [])
    names = {norm_company(b["company"]) for b in reg}
    cache = dict(state.get("discover_cache") or {})
    remaining = []
    for item in boards:
        r = item["payload"]
        key = norm_company(r["company"])
        ans = ctx.review.answer(item) if ctx.review is not None else None
        if ans == "accept" and key not in names:
            reg.append({"company": r["company"], "ats": r["ats"], "slug": r["slug"], "tier": str((ctx.cfg.discover or {}).get("default_tier", "2")),
                        "enabled": True, "source": "review", "confidence": r.get("confidence"), "discovered_at": state.get("run_date", "")})
            names.add(key)
            cache[key] = {**cache.get(key, {}), "status": "found", "ats": r["ats"], "slug": r["slug"]}
        elif ans == "reject":
            cache[key] = {**cache.get(key, {}), "status": "rejected"}
        else:
            remaining.append(item)
    others = [i for i in pending if i.get("kind") != "board"]
    if remaining and (ctx.cfg.review or {}).get("block_on_pending"):
        write_review_note(ctx.paths, review_note_path(ctx.paths, ctx.cfg.review), others + remaining)
        raise AwaitHuman("boards", f"{len(remaining)} board(s) waiting for review")
    return {"registry": reg, "review_pending": others + remaining, "discover_cache": cache}


WEB_SWEEP_SCHEMA = {
    "type": "object",
    "properties": {"companies": {"type": "array", "items": {
        "type": "object",
        "properties": {"company": {"type": "string"}, "careers_url": {"type": "string"}, "title": {"type": "string"},
                       "posting_url": {"type": "string"}, "location": {"type": "string"}},
        "required": ["company", "title", "posting_url"]}}},
    "required": ["companies"],
}


def web_sweep(ctx: RunContext, state: State) -> Update:
    cfg_w = ctx.cfg.web_sweep or {}
    if not cfg_w.get("enabled", True) or ctx.llm is None or _dry(ctx):
        return {"web_sweep": [], "web_sweep_companies": []}
    profile = _profile_text(ctx)
    template, version = PromptLibrary(PROMPTS_DIR).load("web_sweep")
    known = sorted({b["company"] for b in state.get("registry") or []})
    prompt = (template.replace("{max_results}", str(cfg_w.get("max_results", 15))).replace("{candidate_brief}", candidate_brief(profile))
              .replace("{role_titles}", "\n".join(role_titles(profile)) or "(derive from the candidate section)").replace("{known_companies}", ", ".join(known) or "(none)"))
    call = LLMCall(node="web_sweep", prompt_name="web_sweep", prompt_version=version, prompt=prompt, schema=WEB_SWEEP_SCHEMA,
                   model=str((ctx.cfg.llm or {}).get("model", "sonnet")), tools=("WebSearch", "WebFetch"), allowed_tools=("WebSearch", "WebFetch"),
                   max_budget_usd=float(cfg_w.get("max_budget_usd", 1.0)), timeout_s=900, sample=int(ctx.extra.get("sample", 0)))
    result = ctx.llm.call(call)
    f = _f(ctx)
    known_norm = {norm_company(k) for k in known}
    companies, cands, dropped = [], [], 0
    default_tier = str((ctx.cfg.discover or {}).get("default_tier", "2"))
    for item in (result.structured or {}).get("companies") or []:
        if not isinstance(item, dict) or norm_company(item.get("company", "")) in known_norm:
            continue
        row = {"company": item.get("company", ""), "careers_url": item.get("careers_url", ""), "tier": default_tier}
        found = board_from_url(item.get("posting_url", "")) or board_from_url(item.get("careers_url", ""))
        if found:
            row.update({"ats": found[0], "slug": found[1]})
        companies.append(row)
        title = item.get("title", "")
        ok, lane = passes_leadership(title, f)
        cls, reason = classify_location(item.get("location", ""), default_tier, f)
        if not ok or cls == "drop":
            dropped += 1
            continue
        rid = "web:" + hashlib.sha1(item.get("posting_url", "").encode("utf-8")).hexdigest()[:12]
        cands.append({"posting": {"req_id": rid, "company": row["company"], "slug": "", "ats": "web", "tier": default_tier, "title": title,
                                  "location": item.get("location", ""), "url": item.get("posting_url", ""), "comp_text": "", "description": "", "posted_at": ""},
                      "lane": lane, "location_class": cls, "location_reason": reason, "comp_status": "unverified", "comp_range": "",
                      "match_mode": "web_sweep", "flags": ["found by web sweep; board not yet registered"]})
    ctx.log(f"web sweep: {len(companies)} new compan{'y' if len(companies) == 1 else 'ies'}, {len(cands)} candidate(s) kept, {dropped} failed the gates"
            + (" (replayed)" if result.cached else f", est. ${result.cost_usd:.2f}"))
    return {"web_sweep": cands, "web_sweep_companies": companies}


SCORE_SCHEMA = {
    "type": "object",
    "properties": {"scores": {"type": "array", "items": {
        "type": "object",
        "properties": {"req_id": {"type": "string"}, "score": {"type": "number"}, "rationale": {"type": "string"},
                       "flags": {"type": "array", "items": {"type": "string"}}},
        "required": ["req_id", "score", "rationale", "flags"]}}},
    "required": ["scores"],
}


def _score_batch(ctx: RunContext, batch: list[Candidate]) -> Update:
    template, version = PromptLibrary(PROMPTS_DIR).load("score_posting")
    blocks = []
    for c in batch:
        p = c["posting"]
        comp = c.get("comp_range") or ("not posted" if not p.get("comp_text") else p.get("comp_text", "")[:200])
        blocks.append(f"### {p['req_id']}\n- Title: {p['title']}\n- Company: {p['company']}\n- Location: {p.get('location') or '(none)'} ({c.get('location_class', '')})\n"
                      f"- Comp: {comp}\n- Found via: {c.get('match_mode', 'title')}\n- Description: {(p.get('description') or '(none)')[:1500]}\n")
    prompt = template.replace("{candidate_brief}", candidate_brief(_profile_text(ctx))).replace("{rubric}", _rubric_text(ctx)).replace("{postings}", "\n".join(blocks))
    call = LLMCall(node="score", prompt_name="score_posting", prompt_version=version, prompt=prompt, schema=SCORE_SCHEMA,
                   model=str((ctx.cfg.llm or {}).get("model", "sonnet")), tools=(), max_budget_usd=float((ctx.cfg.llm or {}).get("max_budget_usd", 0.5)),
                   timeout_s=int((ctx.cfg.llm or {}).get("timeout_seconds", 600)), sample=int(ctx.extra.get("sample", 0)))
    try:
        result = ctx.llm.call(call)
        got = {s["req_id"]: s for s in ((result.structured or {}).get("scores") or []) if isinstance(s, dict)}
        err = None
    except Exception as exc:  # noqa: BLE001
        if type(exc).__name__ == "LiveBudgetExceeded":
            raise
        got, err = {}, f"{type(exc).__name__}: {exc}"
    out = []
    for c in batch:
        s = got.get(c["posting"]["req_id"])
        if s is None:
            out.append({**c, "score": None, "flags": [*c.get("flags", []), "unscored" + (f": {err}" if err else "")]})
            continue
        try:
            val = round(float(s.get("score")), 1)
        except (TypeError, ValueError):
            val = None
        out.append({**c, "score": val, "score_rationale": str(s.get("rationale", ""))[:300], "flags": [*c.get("flags", []), *[str(x) for x in (s.get("flags") or [])]]})
    return {"scored": out}


def score(ctx: RunContext, state: State) -> Update:
    items = list(state.get("comp_pass") or []) + list(state.get("web_sweep") or [])
    if not items:
        return {"scored": []}
    if ctx.llm is None or _dry(ctx):
        return {"scored": [{**c, "score": None, "flags": [*c.get("flags", []), "unscored: no LLM"]} for c in items]}
    size = max(1, int((ctx.cfg.llm or {}).get("score_batch", 20)))
    batches = [items[i:i + size] for i in range(0, len(items), size)]
    workers = int((ctx.cfg.concurrency or {}).get("llm_workers", 3))
    up = fan_out("score_batches", _score_batch, out_key="scored", max_workers=workers)(ctx, {**state, "score_batches": batches})
    scored = up.get("scored") or []
    ctx.log(f"scored {sum(1 for c in scored if c.get('score') is not None)}/{len(items)} candidates in {len(batches)} call(s)")
    return up


def route_by_score(ctx: RunContext, state: State) -> Update:
    th = ctx.cfg.thresholds
    surfaced, act_now, decisions = [], [], []
    for c in state.get("scored") or []:
        rid = c["posting"]["req_id"]
        s = c.get("score")
        if s is None:
            surfaced.append(c)
            decisions.append(decision(rid, "score", "flag", "unscored", "route_by_score"))
            continue
        if s >= th.log:
            surfaced.append(c)
            decisions.append(decision(rid, "score", "pass", f"{s}", "route_by_score"))
        else:
            decisions.append(decision(rid, "score", "drop", "below log threshold", "route_by_score"))
        if s >= th.act:
            act_now.append(c)
    surfaced.sort(key=lambda c: -(c.get("score") if c.get("score") is not None else -1))
    return {"surfaced": surfaced, "act_now": act_now, "decisions": decisions}
