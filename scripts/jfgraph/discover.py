"""Find a company's public job board from its name.

Probe the common slug spellings on each supported ATS, then decide:
    found         exactly one board answers and the name-match confidence clears the bar
    ambiguous     a weak match, or several boards answer -> human review
    not_found     every probe 404s
    rate_limited  any probe got a 429 -> try again next run, never enroll
    unprobed      the transport had no answer (a replayed snapshot without these URLs)

Enrolling a stranger's board under a target's name is the dangerous failure, so the
confidence bar is high and the benchmark treats a wrong board as a hard fail.
"""

from __future__ import annotations

import re
from typing import Any

from .ats_adapters import BOARD_URLS
from .state import DiscoverResult
from .transport import RateLimited, SnapshotMiss, Transport, TransportError

GREENHOUSE_BOARD_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}"
STOP_WORDS = {"inc", "llc", "ltd", "corp", "corporation", "co", "company", "the", "hq", "technologies", "technology",
              "software", "group", "holdings", "plc", "gmbh", "sa", "ag"}
GENERIC_SLUGS = {"jobs", "careers", "api", "app", "www", "home", "main", "test", "demo"}
TINY_BOARD_JOBS = 3                 # a board with fewer postings than this is treated as possibly abandoned
TINY_BOARD_MAX_CONFIDENCE = 0.7     # below auto_enroll_min: tiny boards go to review unless nothing else answers


def _tokens(company: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", (company or "").lower()) if t]


def slug_candidates(company: str, overrides: dict[str, list[str]] | None = None, max_slugs: int = 6) -> list[str]:
    """"Acme Labs, Inc." -> ["acmelabs", "acme-labs", "acme", "acmelabsinc", "acme_labs", ...]"""
    out: list[str] = []
    if overrides and company in overrides:
        out.extend(overrides[company])
    toks = _tokens(company)
    core = [t for t in toks if t not in STOP_WORDS] or toks
    forms = ["".join(core), "-".join(core)]
    if len(core) > 1:
        forms.append(core[0])
        forms.append("".join(core) + "ai" if core[-1] != "ai" else "".join(core[:-1]))
    if toks != core:
        forms.append("".join(toks))
    forms.append("_".join(core))
    if len(core) >= 3:
        forms.append("".join(t[0] for t in core))
    for f in forms:
        f = f.strip("-_")
        if f and len(f) >= 2 and f not in out and f not in GENERIC_SLUGS:
            out.append(f)
    return out[:max_slugs]


def probe_board(ats: str, slug: str, transport: Transport) -> dict[str, Any]:
    url = BOARD_URLS[ats].format(slug=slug)
    probe: dict[str, Any] = {"ats": ats, "slug": slug, "url": url, "status": "miss", "jobs": 0, "name_hint": None, "sample_text": ""}
    try:
        data = transport.get_json(url)
    except SnapshotMiss:
        probe["status"] = "unprobed"
        return probe
    except RateLimited:
        probe["status"] = "rate_limited"
        return probe
    except TransportError as exc:
        probe["status"] = "miss" if exc.status == 404 else "error"
        probe["error"] = str(exc)
        return probe

    if ats == "greenhouse" and isinstance(data, dict) and "jobs" in data:
        probe["status"], probe["jobs"] = "hit", len(data.get("jobs") or [])
        try:
            board = transport.get_json(GREENHOUSE_BOARD_URL.format(slug=slug))
            probe["name_hint"] = (board or {}).get("name")
        except TransportError:
            pass
        probe["sample_text"] = " ".join((j.get("title") or "") for j in (data.get("jobs") or [])[:5])
    elif ats == "ashby" and isinstance(data, dict) and "jobs" in data:
        jobs = data.get("jobs") or []
        probe["status"], probe["jobs"] = "hit", len(jobs)
        probe["sample_text"] = " ".join(((j.get("descriptionPlain") or "")[:400] + " " + (j.get("title") or "")) for j in jobs[:3])
    elif ats == "lever" and isinstance(data, list):
        probe["status"], probe["jobs"] = "hit", len(data)
        probe["sample_text"] = " ".join(((j.get("descriptionPlain") or "")[:400] + " " + (j.get("text") or "")) for j in data[:3])
    elif ats == "smartrecruiters" and isinstance(data, dict) and "content" in data:
        probe["status"], probe["jobs"] = "hit", len(data.get("content") or [])
    return probe


def name_match(company: str, slug: str, name_hint: str | None, sample_text: str = "") -> float:
    toks = [t for t in _tokens(company) if t not in STOP_WORDS] or _tokens(company)
    if not toks:
        return 0.0
    if name_hint:
        hint = set(_tokens(name_hint)) - STOP_WORDS or set(_tokens(name_hint))
        union = hint | set(toks)
        overlap = len(hint & set(toks)) / len(union) if union else 0.0   # Jaccard: extra words in the board name count against it
        if overlap >= 0.99:
            return 1.0
        return round(0.4 + 0.5 * overlap, 3)
    joined, hyph = "".join(toks), "-".join(toks)
    if slug in (joined, hyph, "_".join(toks)):
        score = 0.85
    elif len(toks) == 1 and slug.startswith(toks[0]) and len(slug) - len(toks[0]) <= 3:
        score = 0.8
    elif slug.startswith(toks[0]) or toks[0] in slug:
        score = 0.6
    else:
        score = 0.3
    text = (sample_text or "").lower()
    if text and " ".join(toks) in text:
        score += 0.1
    return round(min(score, 1.0), 3)


def discover_board(company: str, transport: Transport, cfg: dict[str, Any], *, overrides: dict[str, list[str]] | None = None) -> DiscoverResult:
    ats_list = list(cfg.get("ats") or ["greenhouse", "ashby", "lever"])
    auto_min = float(cfg.get("auto_enroll_min", 0.8))
    amb_min = float(cfg.get("ambiguous_min", 0.5))
    probes: list[dict] = []
    hits: list[dict] = []
    statuses: set[str] = set()
    for slug in slug_candidates(company, overrides, int(cfg.get("max_slugs", 6))):
        for ats in ats_list:
            if ats not in BOARD_URLS:
                continue
            pr = probe_board(ats, slug, transport)
            statuses.add(pr["status"])
            if pr["status"] == "hit":
                conf = name_match(company, slug, pr.get("name_hint"), pr.get("sample_text", ""))
                pr["tiny"] = pr["jobs"] < TINY_BOARD_JOBS
                # An abandoned or test board can carry the right name and one stale posting.
                # It may still be the answer when nothing else exists, but it never settles it.
                pr["confidence"] = min(conf, TINY_BOARD_MAX_CONFIDENCE) if pr["tiny"] else conf
                hits.append(pr)
            probes.append({k: v for k, v in pr.items() if k != "sample_text"})
            if pr["status"] == "hit" and pr["confidence"] >= 0.99:
                break                                   # a live board whose own name matches exactly needs no more probing
        if hits and max(h["confidence"] for h in hits) >= auto_min:
            break

    result: DiscoverResult = {"company": company, "status": "not_found", "ats": None, "slug": None, "confidence": 0.0, "probes": probes}
    if hits:
        hits.sort(key=lambda h: (-h["confidence"], -h["jobs"]))
        best = hits[0]
        rivals = [h for h in hits[1:] if not h.get("tiny")] if not best.get("tiny") else hits[1:]
        second = rivals[0]["confidence"] if rivals else 0.0
        result.update({"ats": best["ats"], "slug": best["slug"], "confidence": best["confidence"], "jobs": best["jobs"]})
        if best["confidence"] >= auto_min and second < amb_min:
            result["status"] = "found"
        elif best["confidence"] >= amb_min:
            result["status"] = "ambiguous"
        else:
            result["status"] = "not_found"
    elif "rate_limited" in statuses:
        result["status"] = "rate_limited"
    elif statuses and statuses <= {"unprobed"}:
        result["status"] = "unprobed"
    return result
