"""Graph presets. One runner, several wirings.

    poller  today's deterministic script: fetch, diff, gates, inbox   (golden-tested)
    A       baseline: the LLM scout as it exists today                (Phase 3)
    B       poller-first + board auto-discovery + web sweep + score   (Phase 4)
    C       B + responsibility classifier + learned terms             (Phase 5)
    D       C + hiring signals and tier promotion                     (Phase 6)
"""

from __future__ import annotations

from . import nodes as N
from .runtime import END, Graph, parallel

VARIANTS = ("poller", "A", "B", "C", "D")
MAX_SYNC_PASSES = 2


def _after_learn_terms(state: dict) -> str:
    """Terms that need a human go to the review node; otherwise straight on to the location gate."""
    if any(i.get("kind") == "term" for i in (state.get("review_pending") or [])):
        return "review_terms"
    return "gate_location"


def _after_sync_registry(state: dict) -> str:
    """Discover boards for unregistered companies, at most twice, then fetch."""
    if state.get("pending_discovery") and int(state.get("sync_passes", 0)) < MAX_SYNC_PASSES:
        return "discover_boards"
    return "parallel_fetch"


def graph_flags(variant: str) -> dict[str, bool]:
    """Per-variant switches the nodes read from ``ctx.extra``."""
    return {"near_miss_enabled": variant in ("C", "D")}


def build_graph(variant: str) -> Graph:
    if variant == "poller":
        g = Graph("poller").set_entry("load_context")
        g.add_node("load_context", N.load_context, out_key="registry")
        g.add_node("fetch_boards", N.fetch_boards, kind="fanout", in_key="registry", out_key="raw_postings")
        g.add_node("diff_seen", N.diff_seen, in_key="raw_postings", out_key="new_postings")
        g.add_node("gate_title", N.gate_title, in_key="new_postings", out_key="title_pass")
        g.add_node("gate_location", N.gate_location, in_key="title_pass", out_key="location_pass")
        g.add_node("fetch_description", N.fetch_description, kind="fanout", in_key="location_pass", out_key="location_pass")
        g.add_node("gate_comp", N.gate_comp, in_key="location_pass", out_key="comp_pass")
        g.add_node("finalize_unscored", N.finalize_unscored, in_key="comp_pass", out_key="surfaced")
        g.add_node("write_vault", N.write_vault, in_key="surfaced")
        g.add_node("persist", N.persist)
        order = ["load_context", "fetch_boards", "diff_seen", "gate_title", "gate_location", "fetch_description",
                 "gate_comp", "finalize_unscored", "write_vault", "persist"]
        for a, b in zip(order, order[1:] + [END]):
            g.add_edge(a, b)
        g.validate()
        return g
    if variant == "A":
        g = Graph("A").set_entry("load_context")
        g.add_node("load_context", N.load_context, out_key="registry")
        g.add_node("fetch_boards", N.fetch_boards, kind="fanout", in_key="registry", out_key="raw_postings")
        g.add_node("scout_baseline", N.scout_baseline, kind="llm", out_key="scout_notes")
        g.add_node("parse_scout_output", N.parse_scout_output, in_key="scout_notes", out_key="surfaced")
        for a, b in zip(["load_context", "fetch_boards", "scout_baseline", "parse_scout_output"],
                        ["fetch_boards", "scout_baseline", "parse_scout_output", END]):
            g.add_edge(a, b)
        g.validate()
        return g
    if variant in ("B", "C", "D"):
        g = Graph(variant).set_entry("load_context")
        g.add_node("load_context", N.load_context, out_key="registry")
        g.add_node("sync_registry", N.sync_registry, in_key="registry", out_key="registry")
        g.add_node("discover_boards", N.discover_boards, kind="fanout", in_key="pending_discovery", out_key="registry")
        g.add_node("review_boards", N.review_boards, kind="human", in_key="review_pending", out_key="registry")
        g.add_node("parallel_fetch", parallel({"fetch_boards": N.fetch_boards, "web_sweep": N.web_sweep}), kind="parallel", in_key="registry", out_key="raw_postings")
        g.add_node("diff_seen", N.diff_seen, in_key="raw_postings", out_key="new_postings")
        g.add_node("gate_title", N.gate_title, in_key="new_postings", out_key="title_pass")
        g.add_node("gate_location", N.gate_location, in_key="title_pass", out_key="location_pass")
        g.add_node("fetch_description", N.fetch_description, kind="fanout", in_key="location_pass", out_key="location_pass")
        g.add_node("gate_comp", N.gate_comp, in_key="location_pass", out_key="comp_pass")
        g.add_node("score", N.score, kind="llm", in_key="comp_pass", out_key="scored")
        g.add_node("route_by_score", N.route_by_score, in_key="scored", out_key="surfaced")
        g.add_node("write_vault", N.write_vault, in_key="surfaced")
        g.add_node("persist", N.persist)
        g.add_edge("load_context", "sync_registry")
        g.add_conditional_edge("sync_registry", _after_sync_registry, targets=("discover_boards", "parallel_fetch"))
        g.add_edge("discover_boards", "review_boards")
        g.add_edge("review_boards", "sync_registry")
        head = ["parallel_fetch", "diff_seen"]
        if variant == "D":
            g.add_node("score_signals", N.score_signals, in_key="signals", out_key="promoted")
            head.append("score_signals")                 # promotions retag tiers before the location gate reads them
        if variant in ("C", "D"):
            g.add_node("classify_responsibility", N.classify_responsibility, kind="llm", in_key="near_misses", out_key="resp_confirmed")
            g.add_node("learn_terms", N.learn_terms_node, in_key="resp_confirmed", out_key="learned_terms")
            g.add_node("review_terms", N.review_terms, kind="human", in_key="review_pending")
            chain = head + ["gate_title", "classify_responsibility", "learn_terms"]
            for a, b in zip(chain, chain[1:]):
                g.add_edge(a, b)
            g.add_conditional_edge("learn_terms", _after_learn_terms, targets=("review_terms", "gate_location"))
            g.add_edge("review_terms", "gate_location")
            chain = ["gate_location", "fetch_description", "gate_comp", "score", "route_by_score", "write_vault", "persist"]
        else:
            chain = head + ["gate_title", "gate_location", "fetch_description", "gate_comp", "score", "route_by_score", "write_vault", "persist"]
        for a, b in zip(chain, chain[1:] + [END]):
            g.add_edge(a, b)
        g.validate()
        return g
    if variant in VARIANTS:
        raise NotImplementedError(f"graph {variant} arrives in a later phase")
    raise ValueError(f"unknown graph variant {variant!r}; choose from {VARIANTS}")
