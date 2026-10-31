"""Read what the graph needs from ``config/profile.md``: the ATS URL table and the
sections a labeler or scorer may see (goals, not the search vocabulary)."""

from __future__ import annotations

import re

BRIEF_SECTIONS = ("Professional Identity", "Target Roles", "Compensation", "Location & Work Arrangement", "Industries & Domains", "Excluded Companies")

BOARD_URL_RES = [
    ("greenhouse", re.compile(r"(?:job-)?boards\.greenhouse\.io/(?P<slug>[A-Za-z0-9_-]+)", re.I)),
    ("greenhouse", re.compile(r"boards-api\.greenhouse\.io/v1/boards/(?P<slug>[A-Za-z0-9_-]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/(?P<slug>[A-Za-z0-9_.-]+)", re.I)),
    ("ashby", re.compile(r"api\.ashbyhq\.com/posting-api/job-board/(?P<slug>[A-Za-z0-9_.-]+)", re.I)),
    ("lever", re.compile(r"jobs\.lever\.co/(?P<slug>[A-Za-z0-9_-]+)", re.I)),
    ("lever", re.compile(r"api\.lever\.co/v0/postings/(?P<slug>[A-Za-z0-9_-]+)", re.I)),
    ("smartrecruiters", re.compile(r"jobs\.smartrecruiters\.com/(?P<slug>[A-Za-z0-9_-]+)", re.I)),
]


def board_from_url(url: str) -> tuple[str, str] | None:
    for ats, pat in BOARD_URL_RES:
        m = pat.search(url or "")
        if m:
            return ats, m.group("slug")
    return None


def parse_profile_table(profile_text: str) -> list[dict]:
    """Rows of the profile's ``| Company | Tier | Careers / ATS URL | ATS |`` table as
    BoardRecords (``ats``/``slug`` None when the URL is not a known board host)."""
    rows = []
    in_table = False
    for line in (profile_text or "").splitlines():
        if re.match(r"^\|\s*Company\s*\|\s*Tier\s*\|", line, re.I):
            in_table = True
            continue
        if in_table:
            if not line.startswith("|"):
                if line.strip():
                    in_table = False
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 3 or set(cells[0]) <= {"-", " "} or cells[0].startswith("<"):
                continue
            company, tier, url = cells[0], cells[1] or "2", cells[2]
            m = re.search(r"https?://\S+", url)
            url = m.group(0).rstrip(">)") if m else url
            found = board_from_url(url)
            rows.append({"company": company, "tier": str(tier), "url": url, "ats": found[0] if found else None,
                         "slug": found[1] if found else None, "source": "profile_table", "enabled": True})
    return rows


def candidate_brief(profile_text: str) -> str:
    parts = re.split(r"^## ", profile_text or "", flags=re.M)
    keep = []
    for part in parts[1:]:
        heading = part.split("\n", 1)[0].strip()
        if any(heading.startswith(s) for s in BRIEF_SECTIONS):
            keep.append("## " + part.strip())
    return "\n\n".join(keep)


def role_titles(profile_text: str) -> list[str]:
    m = re.search(r"\*\*Role titles to search:\*\*\s*```(.*?)```", profile_text or "", re.S)
    if not m:
        return []
    return [l.strip() for l in m.group(1).splitlines() if l.strip() and not l.strip().startswith("<")]


def location_line(profile_text: str) -> str:
    m = re.search(r"\*\*Preferred:\*\*\s*(.+)", profile_text or "")
    return m.group(1).strip() if m else ""
