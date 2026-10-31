"""One board registry, several feeders.

``ats_boards.yaml`` is the source of truth the poller reads. ``merge_sources`` folds in
the profile's ATS URL table, Mark's Jobs Handoff, companies named in recent digests, and
companies the web sweep met, and reports which of them still need a board found.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from .profile import parse_profile_table


def norm_company(name: str) -> str:
    toks = [t for t in re.split(r"[^a-z0-9]+", (name or "").lower()) if t and t not in {"inc", "llc", "ltd", "corp", "the", "co"}]
    return " ".join(toks)


def handoff_companies(handoff: dict[str, Any], default_tier: str = "2") -> list[dict[str, str]]:
    out = []
    for item in (handoff or {}).get("priority_now") or []:
        if isinstance(item, dict) and item.get("company"):
            out.append({"company": item["company"], "tier": "1", "source": "handoff"})
    for item in (handoff or {}).get("new_to_evaluate") or []:
        if isinstance(item, dict) and item.get("company"):
            out.append({"company": item["company"], "tier": default_tier, "source": "handoff"})
    return out


def digest_companies(vault_dir: Path, days: int = 7) -> list[str]:
    """First column of the "New Opportunities" table in the newest digests."""
    d = Path(vault_dir) / "Daily Digests"
    if not d.is_dir():
        return []
    out: list[str] = []
    for path in sorted(d.glob("*.md"), reverse=True)[:days]:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        m = re.search(r"## New Opportunities.*?\n((?:\|.*\n)+)", text)
        if not m:
            continue
        for line in m.group(1).splitlines()[2:]:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if cells and cells[0] and not cells[0].startswith("<"):
                out.append(re.sub(r"[\[\]]", "", cells[0]).split("|")[0])
    return out


def merge_sources(registry: list[dict], *, profile_text: str = "", handoff: dict | None = None, vault_dir: Path | None = None,
                  extra_companies: list[dict] | None = None, sources: list[str] | None = None, default_tier: str = "2",
                  known_missing: dict[str, dict] | None = None) -> tuple[list[dict], list[str]]:
    """Return (registry with any newly resolvable boards appended, companies needing discovery)."""
    sources = list(sources or ["profile_table", "handoff", "digests", "web_sweep"])
    reg = [dict(b) for b in registry]
    by_board = {(b.get("ats"), b.get("slug")) for b in reg if b.get("ats") and b.get("slug")}
    by_name = {norm_company(b.get("company", "")) for b in reg}
    pending: list[str] = []

    def consider(company: str, tier: str, source: str, ats: str | None = None, slug: str | None = None) -> None:
        key = norm_company(company)
        if not key or key in by_name:
            return
        if ats and slug:
            if (ats, slug) in by_board:
                return
            reg.append({"company": company, "ats": ats, "slug": slug, "tier": str(tier or default_tier), "enabled": True, "source": source})
            by_board.add((ats, slug))
            by_name.add(key)
            return
        prior = (known_missing or {}).get(key)
        if prior and prior.get("status") in ("not_found", "rejected"):
            return
        if company not in pending:
            pending.append(company)

    if "profile_table" in sources and profile_text:
        for row in parse_profile_table(profile_text):
            consider(row["company"], row.get("tier", default_tier), "profile_table", row.get("ats"), row.get("slug"))
    if "handoff" in sources and handoff:
        for row in handoff_companies(handoff, default_tier):
            consider(row["company"], row["tier"], "handoff")
    if "digests" in sources and vault_dir is not None:
        for company in digest_companies(vault_dir):
            consider(company, default_tier, "digest")
    if "web_sweep" in sources:
        for row in extra_companies or []:
            consider(row.get("company", ""), row.get("tier", default_tier), "web_sweep")
    return reg, pending


def registry_changed(old: list[dict], new: list[dict]) -> bool:
    key = lambda b: (b.get("company"), b.get("ats"), b.get("slug"), str(b.get("tier")), b.get("enabled", True), b.get("promoted_until"))  # noqa: E731
    return sorted(map(key, old), key=str) != sorted(map(key, new), key=str)


def write_boards_yaml(paths: Any, raw_boards_cfg: dict[str, Any], registry: list[dict], *, path: Path | None = None) -> Path:
    """Rewrite ``ats_boards.yaml`` with the merged registry, keeping filters and sim blocks."""
    target = path or (paths.config_dir / "ats_boards.yaml")
    cfg = dict(raw_boards_cfg or {})
    cfg["boards"] = [{k: v for k, v in b.items() if k in ("company", "ats", "slug", "tier", "enabled", "note", "source", "confidence", "discovered_at", "promoted_until")}
                     for b in registry]
    with paths.open_write(target) as fh:
        fh.write("# Board registry. `source: discover` rows were enrolled automatically (see confidence); edit or disable freely.\n")
        yaml.safe_dump(cfg, fh, allow_unicode=True, sort_keys=False, width=120)
    return target
