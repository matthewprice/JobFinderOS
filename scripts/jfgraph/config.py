"""Discovery configuration: the board registry and filters from ``config/ats_boards.yaml``
plus the graph settings from ``config/discovery.yaml``, each falling back to its public
``*.example.yaml`` in the repo's ``config/``.

Nothing is compiled at import time. ``Filters`` compiles its regexes when built, so a
simulation can carry a different profile than the live run in the same process.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

DEFAULT_FILTERS: dict[str, Any] = {
    "timezone": "",                       # IANA name; empty = system local time
    "min_comp": 0,                        # total-comp floor; 0 = no floor
    "leadership_only": True,              # require a seniority token in the title
    "seniority_terms": r"head\s+of|head|vp|svp|evp|vice\s+president|chief|director|dir\.?|manager|mgr\.?|general\s+manager|gm",
    "function_terms": "",                 # regex alternatives; a title must match one. Empty = any function
    "priority_function_terms": "",        # subset that counts as the priority lane (Lane A)
    "ic_override_terms": r"staff|principal|individual\s+contributor",
    "home_region_terms": "",              # regex alternatives for the home city/state; empty = none
    "home_region_label": "home region",
    "lane_labels": {"A": "Priority function", "B": "Secondary function"},
    "user_agent": "JobFinderOS-ATS-Poller/1.0 (personal job search)",
}

DEFAULT_DISCOVERY: dict[str, Any] = {
    "graph": "B",
    "thresholds": {"log": 5, "act": 7},
    "gates": {"comp_rule": "top"},
    "discover": {"enabled": True, "ats": ["greenhouse", "ashby", "lever"], "max_slugs": 6, "auto_enroll_min": 0.8,
                 "ambiguous_min": 0.5, "default_tier": "2", "sources": ["profile_table", "handoff", "digests", "web_sweep"]},
    "web_sweep": {"enabled": True, "max_results": 15},
    "classifier": {"enabled": True, "max_per_run": 60, "batch": 20, "max_budget_usd": 1.00, "description_chars": 1500,
                   "min_companies": 2, "require_review": False, "hint_terms": "", "only_req_ids_file": ""},
    "signals": {"enabled": True, "half_life_days": 45, "promote_threshold": 5.0, "promote_ttl_days": 90,
                "weights": {"funding": 3, "exec_hire": 4, "buildout": 3, "rename": 2, "first_posting": 3, "layoff": -4}},
    "review": {"note": "Market Intel/Discovery Review.md", "block_on_pending": False},
    "concurrency": {"http_workers": 4, "llm_workers": 3, "throttle_seconds": 0.4},
    "llm": {"model": "sonnet", "max_budget_usd": 0.50, "timeout_seconds": 600, "max_live_calls": 0},
}


def _compile_alts(terms: str, *, word_boundary: bool) -> re.Pattern | None:
    if not terms:
        return None
    body = "(" + terms + ")"
    return re.compile((r"\b" + body + r"\b") if word_boundary else body, re.I)


@dataclass
class Filters:
    timezone: str = DEFAULT_FILTERS["timezone"]
    min_comp: int = 0
    leadership_only: bool = True
    seniority_terms: str = DEFAULT_FILTERS["seniority_terms"]
    function_terms: str = ""
    priority_function_terms: str = ""
    ic_override_terms: str = DEFAULT_FILTERS["ic_override_terms"]
    home_region_terms: str = ""
    home_region_label: str = DEFAULT_FILTERS["home_region_label"]
    lane_labels: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_FILTERS["lane_labels"]))
    user_agent: str = DEFAULT_FILTERS["user_agent"]
    extra_function_terms: tuple[str, ...] = ()     # learned terms, already regex-escaped

    def __post_init__(self) -> None:
        self.min_comp = int(self.min_comp or 0)
        self.leadership_re = _compile_alts(self.seniority_terms, word_boundary=True)
        fn = "|".join(t for t in [self.function_terms, *self.extra_function_terms] if t)
        self.function_re = _compile_alts(fn, word_boundary=False)
        self.lane_a_re = _compile_alts(self.priority_function_terms, word_boundary=False)
        self.ic_override_re = _compile_alts(self.ic_override_terms, word_boundary=True)
        self.home_region_re = _compile_alts(self.home_region_terms, word_boundary=True)

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "Filters":
        merged = {**DEFAULT_FILTERS, **(d or {})}
        known = {k: merged[k] for k in DEFAULT_FILTERS}
        return cls(**known)

    def with_learned(self, terms: list[str]) -> "Filters":
        return replace(self, extra_function_terms=tuple(re.escape(t) for t in terms if t))


@dataclass
class Thresholds:
    log: int = 5
    act: int = 7


@dataclass
class DiscoveryConfig:
    filters: Filters
    boards: list[dict[str, Any]]
    thresholds: Thresholds
    graph: str
    gates: dict[str, Any]
    discover: dict[str, Any]
    web_sweep: dict[str, Any]
    classifier: dict[str, Any]
    signals: dict[str, Any]
    review: dict[str, Any]
    concurrency: dict[str, Any]
    llm: dict[str, Any]
    boards_path: Path | None = None
    discovery_path: Path | None = None
    raw_boards: dict[str, Any] = field(default_factory=dict)
    raw_discovery: dict[str, Any] = field(default_factory=dict)

    @property
    def active_boards(self) -> list[dict[str, Any]]:
        return [b for b in self.boards if b.get("enabled") is not False]


def load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def resolve_config_file(config_dir: Path, root: Path, name: str) -> Path | None:
    """``<config_dir>/<name>`` if present, else the public example in ``<root>/config``."""
    private = Path(config_dir) / name
    if private.is_file():
        return private
    stem, suffix = name.rsplit(".", 1)
    example = Path(root) / "config" / f"{stem}.example.{suffix}"
    return example if example.is_file() else None


def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def apply_overrides(d: dict[str, Any], overrides: dict[str, Any] | None) -> dict[str, Any]:
    """``{"thresholds.log": 6}`` style overrides; values parsed with YAML rules."""
    out = copy.deepcopy(d)
    for dotted, value in (overrides or {}).items():
        node = out
        parts = dotted.split(".")
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = yaml.safe_load(str(value)) if isinstance(value, str) else value
    return out


def load_discovery_config(paths: Any, *, learned_terms: list[str] | None = None,
                          overrides: dict[str, Any] | None = None) -> DiscoveryConfig:
    boards_path = resolve_config_file(paths.config_dir, paths.root, "ats_boards.yaml")
    if boards_path is None:
        raise FileNotFoundError(f"no board registry: copy config/ats_boards.example.yaml to {paths.config_dir / 'ats_boards.yaml'}")
    raw_boards = load_yaml(boards_path)
    discovery_path = resolve_config_file(paths.config_dir, paths.root, "discovery.yaml")
    raw_discovery = load_yaml(discovery_path) if discovery_path else {}
    merged = apply_overrides(_deep_merge(DEFAULT_DISCOVERY, raw_discovery), overrides)

    filters = Filters.from_dict(raw_boards.get("filters"))
    if learned_terms:
        filters = filters.with_learned(learned_terms)
    th = merged.get("thresholds") or {}
    return DiscoveryConfig(
        filters=filters,
        boards=[{**b, "company": str(b.get("company", "")), "slug": str(b.get("slug", "")), "tier": str(b.get("tier", "2"))}
                for b in (raw_boards.get("boards") or []) if isinstance(b, dict)],   # str(): bare On/Off/Yes/No parse as YAML booleans
        thresholds=Thresholds(log=int(th.get("log", 5)), act=int(th.get("act", 7))),
        graph=str(merged.get("graph", "B")),
        gates=merged.get("gates") or {"comp_rule": "top"},
        discover=merged["discover"], web_sweep=merged["web_sweep"], classifier=merged["classifier"],
        signals=merged["signals"], review=merged["review"], concurrency=merged["concurrency"], llm=merged["llm"],
        boards_path=boards_path, discovery_path=discovery_path, raw_boards=raw_boards, raw_discovery=raw_discovery,
    )
