"""ATS adapters: one function per applicant-tracking system, each turning a board's
public JSON feed into ``Posting`` dicts. Every request goes through the given transport.

Supported: Greenhouse, Ashby, Lever (clean public JSON APIs), SmartRecruiters.
Workday is not: its endpoint is a POST with a three-part tenant/site slug.
"""

from __future__ import annotations

import html as _html
import re
from datetime import datetime, timezone
from typing import Any, Callable

from .state import Posting
from .transport import Transport

BOARD_URLS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true",
    "lever": "https://api.lever.co/v0/postings/{slug}?mode=json",
    "smartrecruiters": "https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100",
}

GREENHOUSE_DETAIL_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{jid}"


def strip_html(raw: str) -> str:
    return _html.unescape(re.sub(r"<[^>]+>", " ", raw or ""))


def _iso_from_ms(ms: Any) -> str:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError):
        return ""


def _posting(board: dict, ats: str, req_id: str, title: str, location: str, url: str, *,
             comp_text: str = "", description: str = "", posted_at: str = "") -> Posting:
    return {
        "req_id": req_id, "company": board["company"], "slug": board["slug"], "ats": ats,
        "tier": str(board.get("tier", "")), "title": (title or "").strip(), "location": (location or "").strip(),
        "url": url or "", "comp_text": comp_text or "", "description": description or "", "posted_at": posted_at or "",
    }


def fetch_greenhouse(board: dict, transport: Transport) -> list[Posting]:
    slug = board["slug"]
    data = transport.get_json(BOARD_URLS["greenhouse"].format(slug=slug))
    out = []
    for j in data.get("jobs", []):
        out.append(_posting(
            board, "greenhouse", f"{slug}:{j.get('id')}", j.get("title") or "",
            (j.get("location") or {}).get("name") or "",
            j.get("absolute_url") or f"https://boards.greenhouse.io/{slug}/jobs/{j.get('id')}",
            posted_at=j.get("first_published") or j.get("updated_at") or "",
        ))
    return out


def fetch_ashby(board: dict, transport: Transport) -> list[Posting]:
    slug = board["slug"]
    data = transport.get_json(BOARD_URLS["ashby"].format(slug=slug))
    out = []
    for j in data.get("jobs", []):
        comp = ""
        c = j.get("compensation") or {}
        if isinstance(c, dict):
            comp = c.get("compensationTierSummary") or ""
            if not comp:
                tiers = c.get("summaryComponents") or []
                if isinstance(tiers, list) and tiers:
                    comp = " ".join(str(t.get("summary", "")) for t in tiers if isinstance(t, dict))
        loc = j.get("location") or ""
        if j.get("isRemote") and "remote" not in loc.lower():
            loc = f"{loc} (Remote)".strip()
        out.append(_posting(
            board, "ashby", f"{slug}:{j.get('id') or j.get('jobId') or j.get('title')}", j.get("title") or "", loc,
            j.get("jobUrl") or j.get("applyUrl") or f"https://jobs.ashbyhq.com/{slug}",
            comp_text=comp or "", description=j.get("descriptionPlain") or strip_html(j.get("descriptionHtml") or ""),
            posted_at=j.get("publishedAt") or "",
        ))
    return out


def fetch_lever(board: dict, transport: Transport) -> list[Posting]:
    slug = board["slug"]
    data = transport.get_json(BOARD_URLS["lever"].format(slug=slug))
    out = []
    for j in data if isinstance(data, list) else []:
        cats = j.get("categories") or {}
        desc = j.get("descriptionPlain") or ""
        out.append(_posting(
            board, "lever", f"{slug}:{j.get('id')}", j.get("text") or "", cats.get("location") or "",
            j.get("hostedUrl") or "", comp_text=desc[:6000], description=desc, posted_at=_iso_from_ms(j.get("createdAt")),
        ))
    return out


def fetch_smartrecruiters(board: dict, transport: Transport) -> list[Posting]:
    slug = board["slug"]
    data = transport.get_json(BOARD_URLS["smartrecruiters"].format(slug=slug))
    out = []
    for j in data.get("content", []):
        loc = j.get("location") or {}
        loc_text = ", ".join(x for x in [loc.get("city"), loc.get("region"), loc.get("country")] if x)
        if loc.get("remote") and "remote" not in loc_text.lower():
            loc_text = f"{loc_text} (Remote)".strip()
        out.append(_posting(
            board, "smartrecruiters", f"{slug}:{j.get('id')}", j.get("name") or "", loc_text,
            f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}", posted_at=j.get("releasedDate") or "",
        ))
    return out


FETCHERS: dict[str, Callable[[dict, Transport], list[Posting]]] = {
    "greenhouse": fetch_greenhouse,
    "ashby": fetch_ashby,
    "lever": fetch_lever,
    "smartrecruiters": fetch_smartrecruiters,
}


def fetch_board(board: dict, transport: Transport) -> list[Posting]:
    fn = FETCHERS.get(board.get("ats", ""))
    if fn is None:
        raise ValueError(f"no fetcher for ats={board.get('ats')}")
    return fn(board, transport)


def fetch_detail(posting: Posting, transport: Transport) -> str:
    """Second-pass description fetch. Greenhouse needs a per-job call; the others
    already carried their description in the list response."""
    if posting.get("ats") == "greenhouse":
        slug, jid = posting["req_id"].split(":", 1)
        j = transport.get_json(GREENHOUSE_DETAIL_URL.format(slug=slug, jid=jid))
        return strip_html(j.get("content") or "")
    return posting.get("description") or posting.get("comp_text") or ""


def comp_source_text(posting: Posting) -> str:
    """The text the comp gate parses. Greenhouse: the fetched description. Ashby: only the
    structured compensation summary (its description is for the classifier, not for comp
    parsing, which keeps the poller preset's decisions identical to the original script).
    Lever: the description text."""
    if posting.get("ats") == "greenhouse":
        return posting.get("description") or posting.get("comp_text") or ""
    return posting.get("comp_text") or ""
