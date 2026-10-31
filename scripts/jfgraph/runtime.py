"""A small graph runner: nodes are functions over a state dict, edges are static or
routed, and every node execution leaves a checkpoint behind.

    g = Graph("demo").set_entry("a").add_node("a", fa).add_node("b", fb)
    g.add_edge("a", "b").add_edge("b", END)
    state = g.run(ctx, new_state(...))

Node contract: ``fn(ctx, state) -> update`` where ``update`` is a partial dict merged by
``state.merge_update``. A node that needs a human raises ``AwaitHuman``; the run stops
with status ``awaiting_human`` and can be resumed at that node. An LLM budget trip
(``llm.LiveBudgetExceeded``) stops the run the same way with status ``budget_exceeded``.

Helpers: ``fan_out`` runs one function per item in a list with a thread pool and merges
the results in input order; ``parallel`` runs several node functions concurrently.
"""

from __future__ import annotations

import json
import subprocess
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Protocol

from .metrics import MetricsCollector, drops_by_reason
from .state import APPEND_KEYS, merge_update

END = "__end__"
State = dict[str, Any]
Update = dict[str, Any]
NodeFn = Callable[["RunContext", State], Update]
Router = Callable[[State], "str | list[str]"]


class GraphError(RuntimeError):
    pass


class AwaitHuman(Exception):
    """A human node found no answers yet. The run pauses here."""

    def __init__(self, review_id: str, message: str = "") -> None:
        super().__init__(message or f"awaiting human review: {review_id}")
        self.review_id = review_id


class Notifier(Protocol):
    def notify(self, title: str, body: str) -> None: ...


class NullNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def notify(self, title: str, body: str) -> None:
        self.sent.append((title, body))


class MacNotifier:
    """osascript desktop notification (live mode only)."""

    def notify(self, title: str, body: str) -> None:
        script = f'display notification "{body[:200]}" with title "{title[:80]}"'
        subprocess.run(["osascript", "-e", script], check=False, capture_output=True)


@dataclass
class RunContext:
    run_id: str
    paths: Any                       # jfgraph.paths.Paths
    cfg: Any = None                  # jfgraph.config.DiscoveryConfig (later phases)
    transport: Any = None            # jfgraph.transport.Transport
    llm: Any = None                  # jfgraph.llm.ClaudeCLI
    notifier: Any = field(default_factory=NullNotifier)
    review: Any = None               # jfgraph.review.ReviewPolicy
    clock: Callable[[], datetime] = datetime.now
    sim: bool = True
    metrics: MetricsCollector = field(default_factory=MetricsCollector)
    log: Callable[[str], None] = print
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def run_dir(self) -> Path:
        return self.paths.runs_dir / self.run_id


@dataclass
class NodeSpec:
    name: str
    fn: NodeFn
    kind: str = "py"                 # py | llm | fanout | human | parallel
    in_key: str | None = None
    out_key: str | None = None


class Graph:
    def __init__(self, name: str, append_keys: frozenset[str] = APPEND_KEYS) -> None:
        self.name = name
        self.append_keys = append_keys
        self.nodes: dict[str, NodeSpec] = {}
        self.edges: dict[str, str] = {}
        self.routers: dict[str, tuple[Router, tuple[str, ...]]] = {}
        self.entry: str | None = None

    # ----------------------------------------------------------- construction

    def add_node(self, name: str, fn: NodeFn, *, kind: str = "py", in_key: str | None = None, out_key: str | None = None) -> "Graph":
        if name in self.nodes or name == END:
            raise GraphError(f"duplicate or reserved node name: {name}")
        self.nodes[name] = NodeSpec(name, fn, kind, in_key, out_key)
        return self

    def add_edge(self, src: str, dst: str) -> "Graph":
        if src in self.routers:
            raise GraphError(f"{src} already has a conditional edge")
        self.edges[src] = dst
        return self

    def add_conditional_edge(self, src: str, router: Router, targets: tuple[str, ...] | list[str] = ()) -> "Graph":
        """``router(state)`` returns the next node name, or a list of names to run in
        sequence before continuing from the last one's own edge. ``targets`` is only
        for the diagram and validation."""
        if src in self.edges:
            raise GraphError(f"{src} already has a static edge")
        self.routers[src] = (router, tuple(targets))
        return self

    def set_entry(self, name: str) -> "Graph":
        self.entry = name
        return self

    def validate(self) -> None:
        if not self.entry or self.entry not in self.nodes:
            raise GraphError(f"entry node not set or unknown: {self.entry!r}")
        for name in self.nodes:
            if name not in self.edges and name not in self.routers:
                raise GraphError(f"node {name} has no outgoing edge")
        for src, dst in self.edges.items():
            if dst != END and dst not in self.nodes:
                raise GraphError(f"edge {src} -> {dst}: unknown target")
        for src, (_, targets) in self.routers.items():
            for t in targets:
                if t != END and t not in self.nodes:
                    raise GraphError(f"router on {src} names unknown target {t}")

    def next_after(self, name: str, state: State) -> str | list[str]:
        if name in self.edges:
            return self.edges[name]
        router, _ = self.routers[name]
        nxt = router(state)
        if isinstance(nxt, list):
            if not nxt:
                raise GraphError(f"router on {name} returned an empty list")
            for n in nxt:
                if n != END and n not in self.nodes:
                    raise GraphError(f"router on {name} returned unknown node {n}")
            return nxt
        if nxt != END and nxt not in self.nodes:
            raise GraphError(f"router on {name} returned unknown node {nxt}")
        return nxt

    # ---------------------------------------------------------------- diagram

    def mermaid(self) -> str:
        shapes = {"py": ("[", "]"), "llm": ("{{", "}}"), "fanout": ("[[", "]]"), "human": ("(", ")"), "parallel": ("[/", "/]")}
        lines = ["flowchart TD"]
        for spec in self.nodes.values():
            l, r = shapes.get(spec.kind, ("[", "]"))
            lines.append(f"    {spec.name}{l}{spec.name}{r}")
        lines.append(f"    {END}((end))")
        for src, dst in self.edges.items():
            lines.append(f"    {src} --> {dst}")
        for src, (_, targets) in self.routers.items():
            for t in targets:
                lines.append(f"    {src} -.-> {t}")
        return "\n".join(lines)

    # -------------------------------------------------------------------- run

    def run(self, ctx: RunContext, state: State, *, resume_from: str | None = None, max_steps: int = 200) -> State:
        from .llm import LiveBudgetExceeded  # local import: llm depends on metrics, not on runtime

        self.validate()
        current: str = resume_from or self.entry  # type: ignore[assignment]
        if current not in self.nodes:
            raise GraphError(f"cannot resume at unknown node {current}")
        pending: deque[str] = deque()
        seq = len(state.get("node_metrics", []))
        steps = 0
        state.setdefault("graph", self.name)
        self._write_run_json(ctx, state, status="running", resume_node=current)

        while current != END:
            steps += 1
            if steps > max_steps:
                self._write_run_json(ctx, state, status="error", resume_node=current, error=f"max_steps {max_steps} exceeded")
                raise GraphError(f"graph {self.name} exceeded max_steps={max_steps} at {current}")
            spec = self.nodes[current]
            seq += 1
            items_in = len(state.get(spec.in_key) or []) if spec.in_key else None
            ctx.metrics.start_node(spec.name, spec.kind, seq, items_in)
            before = len(state.get("decisions", []))
            try:
                update = spec.fn(ctx, state) or {}
            except AwaitHuman as exc:
                self._finish(ctx, state, spec, None, before, status="awaiting_human", errors=[str(exc)])
                self._save_state(ctx, state)
                self._write_run_json(ctx, state, status="awaiting_human", resume_node=current, review_id=exc.review_id)
                return state
            except LiveBudgetExceeded as exc:
                self._finish(ctx, state, spec, None, before, status="budget_exceeded", errors=[str(exc)])
                self._save_state(ctx, state)
                self._write_run_json(ctx, state, status="budget_exceeded", resume_node=current, error=str(exc))
                return state
            except Exception as exc:  # noqa: BLE001  (recorded, then re-raised)
                self._finish(ctx, state, spec, None, before, status="error", errors=[f"{type(exc).__name__}: {exc}"])
                self._save_state(ctx, state)
                self._write_run_json(ctx, state, status="error", resume_node=current, error=f"{type(exc).__name__}: {exc}")
                raise
            merge_update(state, update, self.append_keys)
            items_out = len(update.get(spec.out_key) or []) if spec.out_key else None
            metric = self._finish(ctx, state, spec, items_out, before)
            self._checkpoint(ctx, state, spec, seq, metric, update)

            if pending:
                current = pending.popleft()
                continue
            nxt = self.next_after(current, state)
            if isinstance(nxt, list):
                current, rest = nxt[0], nxt[1:]
                pending.extend(rest)
            else:
                current = nxt

        self._write_run_json(ctx, state, status="ok", resume_node=None)
        return state

    # ---------------------------------------------------------------- helpers

    def _finish(self, ctx: RunContext, state: State, spec: NodeSpec, items_out: int | None, before: int, *, status: str = "ok", errors: list[str] | None = None):
        new_decisions = state.get("decisions", [])[before:]
        drops = drops_by_reason(new_decisions, spec.name)
        metric = ctx.metrics.finish_node(items_out=items_out, drops=drops, errors=errors, status=status)
        state.setdefault("node_metrics", []).append(metric.as_dict())
        return metric

    def _save_state(self, ctx: RunContext, state: State) -> None:
        with ctx.paths.open_write(ctx.run_dir / "state.json") as fh:
            json.dump(state, fh, indent=1, default=str)

    def _checkpoint(self, ctx: RunContext, state: State, spec: NodeSpec, seq: int, metric, update: Update) -> None:
        cp_dir = ctx.run_dir / "checkpoints"
        payload = {"node": spec.name, "seq": seq, "metric": metric.as_dict(), "update": update}
        with ctx.paths.open_write(cp_dir / f"{seq:03d}-{spec.name}.json") as fh:
            json.dump(payload, fh, indent=1, default=str)
        self._save_state(ctx, state)

    def _write_run_json(self, ctx: RunContext, state: State, *, status: str, resume_node: str | None, **extra: Any) -> None:
        run_path = ctx.run_dir / "run.json"
        existing: dict[str, Any] = {}
        if run_path.exists():
            try:
                existing = json.loads(run_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                existing = {}
        payload = {
            **existing,
            "run_id": ctx.run_id, "graph": self.name, "mode": state.get("mode"),
            "config_hash": ctx.extra.get("config_hash"), "config_label": ctx.extra.get("config_label"),
            "started": existing.get("started") or ctx.clock().isoformat(timespec="seconds"),
            "updated": ctx.clock().isoformat(timespec="seconds"),
            "status": status, "resume_node": resume_node, **extra,
        }
        with ctx.paths.open_write(run_path) as fh:
            json.dump(payload, fh, indent=1, default=str)


def load_state(ctx: RunContext) -> State:
    return json.loads((ctx.run_dir / "state.json").read_text(encoding="utf-8"))


def load_run_json(ctx: RunContext) -> dict[str, Any]:
    return json.loads((ctx.run_dir / "run.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ combinators


def _merge_many(updates: list[Update], append_keys: frozenset[str]) -> Update:
    """Merge per-item updates in order. Lists concatenate, dicts merge, scalars last-wins."""
    out: Update = {}
    for u in updates:
        for key, value in (u or {}).items():
            if key not in out:
                out[key] = list(value) if isinstance(value, list) else (dict(value) if isinstance(value, dict) else value)
            elif isinstance(value, list) and isinstance(out[key], list):
                out[key].extend(value)
            elif isinstance(value, dict) and isinstance(out[key], dict):
                out[key].update(value)
            else:
                out[key] = value
    return out


def fan_out(items_key: str, per_item: Callable[[RunContext, Any], Update], *, out_key: str | None = None,
            max_workers: int = 4, append_keys: frozenset[str] = APPEND_KEYS) -> NodeFn:
    """Run ``per_item(ctx, item)`` for each item in ``state[items_key]`` with a thread pool.

    Results merge in *input* order, so output never depends on completion order. An
    exception in one item becomes a ``problems`` entry and never kills the run.
    """

    def node(ctx: RunContext, state: State) -> Update:
        items = list(state.get(items_key) or [])
        if not items:
            return {out_key: []} if out_key else {}
        results: list[Update] = [{} for _ in items]
        problems: list[str] = []
        fatal: list[BaseException] = []

        def work(i: int) -> None:
            try:
                results[i] = per_item(ctx, items[i]) or {}
            except Exception as exc:  # noqa: BLE001
                results[i] = {}
                if type(exc).__name__ == "LiveBudgetExceeded":   # must stop the run, never be swallowed
                    fatal.append(exc)
                    return
                problems.append(f"{ctx.metrics.current_node or 'fan_out'}: item {i}: {type(exc).__name__}: {exc}")

        with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
            list(pool.map(work, range(len(items))))
        if fatal:
            raise fatal[0]
        merged = _merge_many(results, append_keys)
        if out_key:
            merged.setdefault(out_key, [])
        if problems:
            merged.setdefault("problems", []).extend(sorted(problems))
        return merged

    return node


def parallel(branches: dict[str, NodeFn], *, append_keys: frozenset[str] = APPEND_KEYS) -> NodeFn:
    """Run several node functions concurrently and merge their updates.

    Two branches writing the same non-list, non-dict key is a bug and raises.
    LLM usage from all branches is attributed to the parallel node.
    """

    def node(ctx: RunContext, state: State) -> Update:
        names = list(branches)
        with ThreadPoolExecutor(max_workers=len(names)) as pool:
            futures = {n: pool.submit(branches[n], ctx, dict(state)) for n in names}
        updates: list[Update] = []
        for n in names:
            updates.append(futures[n].result() or {})
        seen_scalars: dict[str, str] = {}
        for n, u in zip(names, updates):
            for key, value in u.items():
                if isinstance(value, (list, dict)):
                    continue
                if key in seen_scalars:
                    raise GraphError(f"parallel branches {seen_scalars[key]} and {n} both set {key!r}")
                seen_scalars[key] = n
        return _merge_many(updates, append_keys)

    return node
