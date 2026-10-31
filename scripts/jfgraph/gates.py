"""The deterministic gates: title (seniority + function), location, and comp.

Lifted from scripts/jobfinderos_ats_poll.py with one change: every function takes the
``Filters`` it needs instead of reading module globals. The profile-independent regexes
(US hubs, non-US places, money) stay module constants. The decisions are unchanged;
tests/test_gates.py checks that against a golden produced by the original script.
"""

from __future__ import annotations

import re

from .config import Filters

REMOTE_US_RE = re.compile(
    r"(remote\s*[-,/]?\s*(us|u\.s\.|usa|united\s+states|america|anywhere\s+in\s+the\s+us)"
    r"|(us|u\.s\.|usa|united\s+states)[\s,()-]*remote"
    r"|remote\s*[-,/]?\s*(north\s+america|nam)"
    r"|^remote$|\(remote\)|remote\s+-\s+[a-z\s]*(california|colorado|oregon|washington|"
    r"texas|new\s+york|illinois|georgia|utah|massachusetts|florida|arizona|virginia|"
    r"north\s+carolina|d\.?c\.?))",
    re.I,
)
US_GENERIC_RE = re.compile(r"^\s*(united\s+states|usa|u\.s\.a?\.?|us)\s*$", re.I)
US_HUB_RE = re.compile(
    r"\b(san\s+francisco|sf\b|new\s+york|nyc\b|brooklyn|seattle|bellevue|boston|"
    r"cambridge|palo\s+alto|mountain\s+view|menlo\s+park|sunnyvale|santa\s+clara|"
    r"los\s+angeles|san\s+jose|chicago|denver|washington|d\.?c\.?|atlanta|miami|"
    r"chapel\s+hill|redwood\s+city|foster\s+city|san\s+mateo)\b",
    re.I,
)
NON_US_RE = re.compile(
    r"\b(london|uk|united\s+kingdom|england|scotland|ireland|dublin|paris|france|"
    r"berlin|munich|germany|amsterdam|netherlands|zurich|geneva|switzerland|"
    r"stockholm|sweden|oslo|norway|copenhagen|denmark|helsinki|finland|iceland|"
    r"brussels|belgium|vienna|austria|prague|czech|warsaw|poland|budapest|hungary|"
    r"bucharest|romania|athens|greece|istanbul|turkey|lisbon|portugal|madrid|"
    r"barcelona|spain|milan|rome|italy|luxembourg|estonia|latvia|lithuania|"
    r"tokyo|japan|singapore|seoul|korea|china|shanghai|beijing|hong\s+kong|taiwan|"
    r"sydney|melbourne|australia|auckland|new\s+zealand|manila|philippines|"
    r"hanoi|vietnam|bangkok|thailand|jakarta|indonesia|kuala\s+lumpur|malaysia|"
    r"toronto|canada|vancouver|montreal|ottawa|"
    r"bangalore|bengaluru|india|mumbai|delhi|hyderabad|chennai|pune|"
    r"thiruvananthapuram|pakistan|bangladesh|"
    r"tel\s+aviv|israel|dubai|uae|abu\s+dhabi|qatar|doha|riyadh|saudi|kuwait|"
    r"bahrain|oman|jordan|lebanon|egypt|cairo|morocco|tunisia|nigeria|kenya|"
    r"ghana|south\s+africa|africa|mena|"
    r"sao\s+paulo|brazil|mexico|argentina|buenos\s+aires|chile|santiago|colombia|"
    r"bogota|peru|lima|uruguay|costa\s+rica|panama|"
    r"apac|emea|latam|anz|noram|international)\b",
    re.I,
)
# A leading "Remote" ("Remote - California") is the company declaring the role
# remote-first. A trailing "(Remote)" bolted onto a city ("Foster City, CA (Remote)")
# is ambiguous - it is often a hub-based seat wearing a remote label.
LEADING_REMOTE_RE = re.compile(r"^\s*(fully\s+)?remote\b", re.I)
REMOTE_WORD_RE = re.compile(r"\bremote\b", re.I)
SENIOR_LEADER_RE = re.compile(r"\b(head|vp|vice\s+president|director|chief)\b", re.I)

MONEY_RE = re.compile(r"\$\s?([\d][\d,\.]*)\s?([kK])?\b")
# "$500 million in funding" must never be read as a $500,000 salary.
SCALE_WORD_RE = re.compile(r"^\s*(million|billion|bn\b|mm\b|m\b|b\b)", re.I)


# ------------------------------------------------------------------ title gate


def passes_seniority(title: str, f: Filters) -> tuple[bool, str]:
    """Seniority token present (when leadership_only) and no IC override. -> (ok, reason)."""
    if not title:
        return False, "empty title"
    if f.leadership_only and not f.leadership_re.search(title):
        return False, "no seniority token"
    if f.leadership_only and f.ic_override_re and f.ic_override_re.search(title) and not SENIOR_LEADER_RE.search(title):
        return False, "ic override"
    return True, ""


def matches_function(title: str, f: Filters) -> bool:
    return True if f.function_re is None else bool(f.function_re.search(title or ""))


def lane_for(title: str, f: Filters) -> str:
    return "A" if (f.lane_a_re and f.lane_a_re.search(title or "")) else "B"


def passes_leadership(title: str, f: Filters) -> tuple[bool, str]:
    """The original combined gate: -> (ok, lane). Kept for callers and the golden test."""
    ok, _ = passes_seniority(title, f)
    if not ok or not matches_function(title, f):
        return False, ""
    return True, lane_for(title, f)


# --------------------------------------------------------------- location gate


def _strip_remote_words(part: str) -> str:
    return re.sub(r"[\s,()/-]+", " ", re.sub(r"\b(fully\s+)?remote\b", " ", part, flags=re.I)).strip()


def classify_location(loc: str, tier: object, f: Filters) -> tuple[str, str]:
    """-> (clean | hub_flag | drop, human reason)"""
    if not loc:
        return "hub_flag", "no location listed - verify"

    parts = [p.strip() for p in re.split(r"[;|/]|\s+or\s+", loc) if p.strip()]
    blob = " ; ".join(parts)

    has_home = bool(f.home_region_re.search(blob)) if f.home_region_re else False
    # A part like "Remote, US" or "US-Remote" names the country once the remote words are
    # stripped; without this, "Remote, Canada; Remote, US" was dropped as international.
    has_us_generic = (any(US_GENERIC_RE.match(p) for p in parts) or bool(re.search(r"united\s+states", blob, re.I))
                      or any(US_GENERIC_RE.match(_strip_remote_words(p)) for p in parts))
    has_hub = bool(US_HUB_RE.search(blob))
    has_non_us = bool(NON_US_RE.search(blob))
    has_remote_word = bool(REMOTE_WORD_RE.search(blob))
    leading_remote = bool(LEADING_REMOTE_RE.match(blob))

    if has_non_us and not (has_home or has_us_generic or has_hub):
        return "drop", "international only"
    if has_home:
        return "clean", f"{f.home_region_label} listed"
    if leading_remote:
        return "clean", "remote-first listing"
    if REMOTE_US_RE.search(blob) and not has_hub:
        return "clean", "remote-US"
    if has_us_generic and not has_hub:
        return "clean", "US, no city constraint"
    if has_hub:
        if has_remote_word:
            return "hub_flag", "remote flag on a hub city - verify remote scope"
        if str(tier) == "1":
            return "hub_flag", "hub-based at a Tier-1 - relocation, deprioritized"
        return "drop", "hub-only at a non-Tier-1"
    if has_us_generic:
        return "clean", "US-listed"
    if has_remote_word:
        return "hub_flag", "remote but region unclear - verify"
    return "hub_flag", "location unclear - verify"


# ------------------------------------------------------------------- comp gate


def parse_comp(text: str, min_comp: int, rule: str = "midpoint") -> tuple[str, str]:
    """-> (clear | under | unverified, human range). Fails OPEN to 'unverified'.

    ``rule``: "midpoint" (the original: a posted range must have its midpoint at or above
    the floor) or "top" (drop only when even the top of the range is below the floor)."""
    if not text:
        return "unverified", ""
    vals = []
    for m in MONEY_RE.finditer(text):
        amt, k = m.group(1), m.group(2)
        if SCALE_WORD_RE.match(text[m.end(): m.end() + 12]):
            continue
        try:
            v = float(amt.replace(",", "").rstrip("."))
        except ValueError:
            continue
        if k:
            v *= 1000
        if 50_000 <= v <= 2_000_000:
            vals.append(v)
    if not vals:
        return "unverified", ""

    vals = sorted(set(vals))
    if len(vals) >= 2:
        lo, hi = vals[0], vals[-1]
        mid = (lo + hi) / 2
        rng = f"${lo:,.0f}-${hi:,.0f} (mid ${mid:,.0f})"
        if rule == "top":
            return ("clear" if hi >= min_comp else "under"), rng
        return ("clear" if mid >= min_comp else "under"), rng
    v = vals[0]
    return ("clear" if v >= min_comp else "unverified"), f"${v:,.0f}"
