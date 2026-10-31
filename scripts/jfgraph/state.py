"""The shared state that flows through a discovery graph, and how node updates merge.

Everything here is a plain dict at runtime (JSON-serializable, checkpointable). The
TypedDicts document the shape; they are not enforced.

Nodes return a *partial* update. ``merge_update`` replaces keys, except the keys in
``APPEND_KEYS`` whose lists are extended (a reducer, by hand).
"""

from __future__ import annotations

from typing import Any, TypedDict

Stage = str      # seen | title | near_miss | classify | location | comp | score | review
Outcome = str    # pass | drop | flag


class Posting(TypedDict, total=False):
    req_id: str            # "<slug>:<ats id>", globally unique (same scheme as the poller)
    company: str
    slug: str
    ats: str
    tier: str
    title: str
    location: str
    url: str
    comp_text: str
    description: str       # only after fetch_description
    posted_at: str         # ISO 8601 when the ATS gives one
    first_seen_run: str


class Decision(TypedDict):
    req_id: str
    stage: Stage
    outcome: Outcome
    reason: str
    node: str


class Candidate(TypedDict, total=False):
    posting: Posting
    lane: str
    location_class: str    # clean | hub_flag | drop
    location_reason: str
    comp_status: str       # clear | under | unverified
    comp_range: str
    match_mode: str        # title | responsibility | web_sweep | scout_baseline
    score: float
    score_rationale: str
    flags: list[str]
    human_path: str


class BoardRecord(TypedDict, total=False):
    company: str
    ats: str
    slug: str
    tier: str
    enabled: bool
    note: str
    source: str            # yaml | profile_table | handoff | digest | discover | web_sweep
    discovered_at: str
    confidence: float
    signal_score: float
    promoted_until: str


class Signal(TypedDict, total=False):
    date: str
    company: str
    type: str              # funding | exec_hire | buildout | rename | first_posting | layoff
    strength: float
    source_url: str
    note: str


class DiscoverResult(TypedDict, total=False):
    company: str
    status: str            # found | not_found | ambiguous | rate_limited
    ats: str | None
    slug: str | None
    confidence: float
    probes: list[dict]


class DiscoveryState(TypedDict, total=False):
    run_id: str
    run_date: str
    graph: str
    mode: str              # live | sim
    registry: list[BoardRecord]
    pending_discovery: list[str]
    discover_results: list[DiscoverResult]      # append
    handoff: dict
    raw_postings: list[Posting]
    seen_ids: list[str]
    new_postings: list[Posting]
    decisions: list[Decision]                   # append
    title_pass: list[Candidate]
    near_misses: list[Posting]
    resp_confirmed: list[Candidate]
    learned_terms: list[dict]                   # append
    location_pass: list[Candidate]
    comp_pass: list[Candidate]
    web_sweep: list[Candidate]
    scored: list[Candidate]
    surfaced: list[Candidate]
    act_now: list[Candidate]
    signals: list[Signal]
    signal_scores: dict[str, float]
    promoted: list[str]
    about_to_hire: list[dict]
    review_pending: list[dict]
    review_answers: list[dict]
    problems: list[str]                         # append
    node_metrics: list[dict]                    # append


APPEND_KEYS: frozenset[str] = frozenset(
    {"decisions", "discover_results", "learned_terms", "problems", "node_metrics"}
)


def new_state(run_id: str, graph: str, mode: str, run_date: str) -> dict[str, Any]:
    state: dict[str, Any] = {"run_id": run_id, "graph": graph, "mode": mode, "run_date": run_date}
    for key in APPEND_KEYS:
        state[key] = []
    return state


def merge_update(state: dict[str, Any], update: dict[str, Any], append_keys: frozenset[str] = APPEND_KEYS) -> dict[str, Any]:
    """Apply a node's partial update to the state in place and return it."""
    for key, value in update.items():
        if key in append_keys:
            if value is None:
                continue
            if not isinstance(value, list):
                raise TypeError(f"append key {key!r} must be a list, got {type(value).__name__}")
            state.setdefault(key, []).extend(value)
        else:
            state[key] = value
    return state


def decision(req_id: str, stage: Stage, outcome: Outcome, reason: str, node: str) -> Decision:
    return {"req_id": req_id, "stage": stage, "outcome": outcome, "reason": reason, "node": node}
