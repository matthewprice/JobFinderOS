#!/usr/bin/env python3
"""JobFinderOS discovery: the live entrypoint for the discovery graph.

    python3 scripts/jobfinderos_discover.py                 # graph from config/discovery.yaml (default B)
    python3 scripts/jobfinderos_discover.py --graph C       # override the preset
    python3 scripts/jobfinderos_discover.py --shadow        # write only ATS Inbox.md + Discovery Review.md
    python3 scripts/jobfinderos_discover.py --weekly        # also write the About to Hire note (Graph D)
    python3 scripts/jobfinderos_discover.py --dry-run       # decide and print; touch no state, no vault
    python3 scripts/jobfinderos_discover.py --resume RUN_ID # continue a run that paused for review
    python3 scripts/jobfinderos_discover.py --reconsider-dropped

Reads config/ats_boards.yaml (boards + filters) and config/discovery.yaml (graph settings),
each falling back to its example. LLM nodes shell out to `claude -p` with explicit tools and
a permission mode, never --dangerously-skip-permissions. State lives in
$JOBFINDEROS_STATE_DIR (default ~/.jobfinderos). Never commits, never pushes, never sends.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from jfgraph.classify import load_accepted_terms  # noqa: E402
from jfgraph.config import load_discovery_config  # noqa: E402
from jfgraph.graphs import VARIANTS, build_graph, graph_flags  # noqa: E402
from jfgraph.llm import ClaudeCLI  # noqa: E402
from jfgraph.metrics import MetricsCollector, funnel  # noqa: E402
from jfgraph.paths import Paths  # noqa: E402
from jfgraph.review import VaultCheckboxPolicy, review_note_path  # noqa: E402
from jfgraph.runtime import MacNotifier, NullNotifier, RunContext, load_run_json, load_state  # noqa: E402
from jfgraph.state import new_state  # noqa: E402
from jfgraph.transport import LiveTransport, ThrottledTransport  # noqa: E402

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore


def make_logger(paths: Paths, clock):
    log_file = paths.logs_dir / "discover.log"

    def log(msg: str) -> None:
        line = f"[{clock().strftime('%Y-%m-%d %H:%M:%S %Z')}] {msg}"
        print(line)
        try:
            with paths.open_write(log_file, "a") as fh:
                fh.write(line + "\n")
        except Exception:  # noqa: BLE001
            pass

    return log


def main() -> int:
    ap = argparse.ArgumentParser(description="JobFinderOS discovery graph (live)")
    ap.add_argument("--graph", choices=[v for v in VARIANTS if v != "A"], help="preset; default from config/discovery.yaml")
    ap.add_argument("--shadow", action="store_true", help="observe only: inbox + review note, no notifications")
    ap.add_argument("--weekly", action="store_true", help="also write the About to Hire note")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--resume", metavar="RUN_ID")
    ap.add_argument("--reconsider-dropped", action="store_true")
    ap.add_argument("--max-live-calls", type=int, default=None, help="cap uncached claude calls this run (default from config; 0 = unlimited)")
    args = ap.parse_args()

    paths = Paths.live()
    cfg = load_discovery_config(paths, learned_terms=load_accepted_terms(paths.state_dir))
    graph = args.graph or cfg.graph
    if graph not in VARIANTS or graph == "A":
        print(f"unknown or unsupported live graph {graph!r}", file=sys.stderr)
        return 2
    tz = ZoneInfo(cfg.filters.timezone) if (cfg.filters.timezone and ZoneInfo) else None
    clock = (lambda: datetime.now(tz)) if tz else datetime.now
    log = make_logger(paths, clock)
    metrics = MetricsCollector()
    max_live = args.max_live_calls if args.max_live_calls is not None else int((cfg.llm or {}).get("max_live_calls", 0))
    llm = None if args.dry_run else ClaudeCLI(paths.cache_dir, live=True, max_live_calls=max_live, metrics=metrics, paths=paths)
    transport = ThrottledTransport(LiveTransport(cfg.filters.user_agent), min_gap_s=float(cfg.concurrency.get("throttle_seconds", 0.4)))

    if args.resume:
        run_id = args.resume
        ctx = RunContext(run_id=run_id, paths=paths, cfg=cfg, transport=transport, llm=llm, notifier=NullNotifier() if (args.shadow or args.dry_run) else MacNotifier(),
                         review=VaultCheckboxPolicy(review_note_path(paths, cfg.review)), clock=clock, sim=False, metrics=metrics, log=log,
                         extra={**graph_flags(graph), "dry_run": args.dry_run, "weekly": args.weekly, "reconsider_dropped": args.reconsider_dropped, "shadow": args.shadow})
        info = load_run_json(ctx)
        if info.get("status") not in ("awaiting_human", "budget_exceeded"):
            log(f"run {run_id} is {info.get('status')}; nothing to resume")
            return 0
        state = load_state(ctx)
        log(f"resuming {run_id} at {info.get('resume_node')}")
        state = build_graph(info.get("graph", graph)).run(ctx, state, resume_from=info.get("resume_node"))
    else:
        run_id = clock().strftime("%Y%m%dT%H%M%S") + f"-{graph}"
        ctx = RunContext(run_id=run_id, paths=paths, cfg=cfg, transport=transport, llm=llm, notifier=NullNotifier() if (args.shadow or args.dry_run) else MacNotifier(),
                         review=VaultCheckboxPolicy(review_note_path(paths, cfg.review)), clock=clock, sim=False, metrics=metrics, log=log,
                         extra={**graph_flags(graph), "dry_run": args.dry_run, "weekly": args.weekly, "reconsider_dropped": args.reconsider_dropped, "shadow": args.shadow})
        state = build_graph(graph).run(ctx, new_state(run_id, graph, "live", clock().strftime("%Y-%m-%d")))

    info = load_run_json(ctx)
    rows = funnel(state.get("node_metrics", []))
    by = {r["node"]: r for r in rows}
    cost = round(sum(r["cost_usd"] for r in rows), 3)
    log(f"run {run_id}: {info.get('status')}; {by.get('fetch_boards', by.get('parallel_fetch', {})).get('out', 0)} postings fetched, "
        f"{len(state.get('new_postings', []))} new, {len(state.get('surfaced', []))} surfaced, {len(state.get('act_now', []))} act-now; "
        f"{sum(r['llm_calls'] for r in rows)} LLM call(s), est. ${cost}")
    if info.get("status") == "awaiting_human":
        log(f"paused for review: tick the boxes in {review_note_path(paths, cfg.review)} and run --resume {run_id}")
    summary = {"run_id": run_id, "graph": graph, "status": info.get("status"), "surfaced": len(state.get("surfaced", [])), "act_now": len(state.get("act_now", [])),
               "problems": state.get("problems", []), "est_cost_usd": cost}
    print(json.dumps(summary))
    return 0 if info.get("status") in ("ok", "awaiting_human") else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
