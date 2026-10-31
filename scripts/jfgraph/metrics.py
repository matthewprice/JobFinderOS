"""Per-node run metrics. The runtime records these; node authors never touch them.

Pipeline-level metrics (recall, precision, cost per true positive, repeatability) live
in ``report.py`` once there is a labeled ground truth to compare against.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

LLM_FIELDS = ("calls", "cached", "input_tokens", "output_tokens", "cache_read_tokens", "cache_create_tokens", "cost_usd")


def empty_llm() -> dict[str, float]:
    return {k: 0 for k in LLM_FIELDS}


@dataclass
class NodeMetric:
    node: str
    kind: str
    seq: int
    started_at: str
    wall_ms: int = 0
    items_in: int | None = None
    items_out: int | None = None
    drops: dict[str, int] = field(default_factory=dict)
    llm: dict[str, float] = field(default_factory=empty_llm)
    errors: list[str] = field(default_factory=list)
    status: str = "ok"      # ok | awaiting_human | budget_exceeded | error

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class MetricsCollector:
    """Accumulates one ``NodeMetric`` at a time. The LLM client reports into it."""

    def __init__(self, clock=None) -> None:
        self._clock = clock or datetime.now
        self.current: NodeMetric | None = None
        self.completed: list[NodeMetric] = []
        self._t0: float = 0.0

    @property
    def current_node(self) -> str | None:
        return self.current.node if self.current else None

    def start_node(self, node: str, kind: str, seq: int, items_in: int | None) -> None:
        import time
        self.current = NodeMetric(node=node, kind=kind, seq=seq, started_at=self._clock().isoformat(timespec="seconds"), items_in=items_in)
        self._t0 = time.monotonic()

    def record_llm(self, result: Any) -> None:
        """Called by ``ClaudeCLI`` after every call (cached or not)."""
        if self.current is None:
            return
        llm = self.current.llm
        llm["calls"] += 1
        if getattr(result, "cached", False):
            llm["cached"] += 1
        llm["input_tokens"] += getattr(result, "input_tokens", 0)
        llm["output_tokens"] += getattr(result, "output_tokens", 0)
        llm["cache_read_tokens"] += getattr(result, "cache_read_tokens", 0)
        llm["cache_create_tokens"] += getattr(result, "cache_create_tokens", 0)
        llm["cost_usd"] = round(llm["cost_usd"] + float(getattr(result, "cost_usd", 0.0)), 6)

    def finish_node(self, *, items_out: int | None, drops: dict[str, int], errors: list[str] | None = None, status: str = "ok") -> NodeMetric:
        import time
        assert self.current is not None, "finish_node without start_node"
        m = self.current
        m.wall_ms = int((time.monotonic() - self._t0) * 1000)
        m.items_out = items_out
        m.drops = dict(drops)
        m.errors = list(errors or [])
        m.status = status
        self.completed.append(m)
        self.current = None
        return m


def drops_by_reason(decisions: list[dict], node: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for d in decisions:
        if d.get("node") == node and d.get("outcome") == "drop":
            out[d.get("reason", "")] = out.get(d.get("reason", ""), 0) + 1
    return dict(sorted(out.items()))


def _safe_div(n: float, d: float) -> float | None:
    return round(n / d, 4) if d else None


def pipeline_metrics(surfaced: list[str] | set[str], act_now: list[str] | set[str], labels: dict[str, str]) -> dict[str, Any]:
    """Recall / precision / F1 of ``surfaced`` against ``labels`` (req_id -> fit|not_fit|borderline).

    Lenient precision leaves borderline postings out of the denominator; strict counts them
    as misses. Surfaced postings with no label are reported separately, never as FP.
    """
    s = set(surfaced)
    a = set(act_now)
    fit = {k for k, v in labels.items() if v == "fit"}
    border = {k for k, v in labels.items() if v == "borderline"}
    labeled_s = {x for x in s if x in labels}
    tp = len(s & fit)
    fp = len(labeled_s - fit - border)
    fn = len(fit - s)
    recall = _safe_div(tp, len(fit))
    precision = _safe_div(tp, len(labeled_s - border))
    precision_strict = _safe_div(tp, len(labeled_s))
    f1 = _safe_div(2 * recall * precision, recall + precision) if recall is not None and precision is not None and (recall + precision) else None
    labeled_a = {x for x in a if x in labels}
    return {
        "surfaced": len(s), "act_now": len(a), "tp": tp, "fp": fp, "fn": fn,
        "borderline_surfaced": len(s & border), "unlabeled_surfaced": len(s - set(labels)),
        "recall": recall, "precision": precision, "precision_strict": precision_strict, "f1": f1,
        "act_precision": _safe_div(len(a & fit), len(labeled_a - border)),
        "fit_total": len(fit),
    }


def repeatability(sets: list[set[str]]) -> dict[str, Any]:
    """Mean pairwise Jaccard of surfaced sets plus how often each posting appeared."""
    sets = [set(s) for s in sets]
    pairs, total = 0, 0.0
    for i in range(len(sets)):
        for j in range(i + 1, len(sets)):
            u = sets[i] | sets[j]
            total += (len(sets[i] & sets[j]) / len(u)) if u else 1.0
            pairs += 1
    freq: dict[str, int] = {}
    for s in sets:
        for x in s:
            freq[x] = freq.get(x, 0) + 1
    return {"runs": len(sets), "mean_jaccard": round(total / pairs, 4) if pairs else None,
            "always": sorted(k for k, v in freq.items() if v == len(sets)), "sometimes": sorted(k for k, v in freq.items() if v < len(sets)),
            "frequency": dict(sorted(freq.items()))}


def funnel(node_metrics: list[dict]) -> list[dict]:
    """Rows for a per-node funnel table, in execution order."""
    rows = []
    for m in sorted(node_metrics, key=lambda x: x.get("seq", 0)):
        llm = m.get("llm") or {}
        rows.append({
            "node": m["node"], "kind": m.get("kind"), "in": m.get("items_in"), "out": m.get("items_out"),
            "drops": m.get("drops") or {}, "wall_ms": m.get("wall_ms"),
            "llm_calls": llm.get("calls", 0), "cached": llm.get("cached", 0),
            "tokens": (llm.get("input_tokens", 0) + llm.get("output_tokens", 0)),
            "cost_usd": llm.get("cost_usd", 0.0), "status": m.get("status", "ok"),
        })
    return rows
