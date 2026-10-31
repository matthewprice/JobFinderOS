#!/usr/bin/env python3
"""
JobFinderOS - local direct-ATS poller.
================================================================================
A thin shim over the ``poller`` preset in scripts/jfgraph/ (the discovery graph).
Finds NEW matching reqs by hitting each target company's ATS JSON API directly.
Deterministic: HTTP + filter + diff. No LLM, no API keys. Filters and boards come
from config/ats_boards.yaml (falls back to config/ats_boards.example.yaml).

Pipeline (one node each; see jfgraph/graphs.py):
    load_context -> fetch_boards -> diff_seen -> gate_title -> gate_location
      -> fetch_description -> gate_comp -> finalize_unscored -> write_vault -> persist

    TITLE gate     seniority token if leadership_only, AND a target-function term
    LOCATION gate  remote / home region / country-wide = clean; hub-city-only at a
                   tier-1 = surface with a relocation flag; everything else dropped
    COMP gate      parsed range midpoint must clear filters.min_comp; unparseable =
                   surface as "comp unverified" (fails OPEN toward surfacing)
    seen-set       ~/.jobfinderos/ats_seen.json (or $JOBFINDEROS_STATE_DIR); every
                   fetched req is recorded with its last decision, so a dropped req is
                   not re-evaluated until --reconsider-dropped
    output         vault/Market Intel/ATS Inbox.md (unscored entries) + a desktop
                   notification for clean-location matches

Flags:
    --seed                 mark everything currently live as already-seen, write the
                           inbox scaffold, notify nothing. Run ONCE at install.
    --dry-run              fetch + decide + print. Touches no state, no vault.
    --reconsider-dropped   re-run the gates over reqs that were dropped before (after
                           a filter change).
    --status               print seen-set size and last-run info, then exit.

The old --push flag is gone: agents never commit or push (CLAUDE.md).
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from jfgraph.config import load_discovery_config  # noqa: E402
from jfgraph.graphs import build_graph, graph_flags  # noqa: E402
from jfgraph.metrics import funnel  # noqa: E402
from jfgraph.nodes import read_seen_summary  # noqa: E402
from jfgraph.paths import Paths  # noqa: E402
from jfgraph.runtime import MacNotifier, NullNotifier, RunContext  # noqa: E402
from jfgraph.state import new_state  # noqa: E402
from jfgraph.transport import LiveTransport, ThrottledTransport  # noqa: E402
from jfgraph.vault_writer import inbox_path  # noqa: E402

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore


def make_logger(paths: Paths, clock):
    log_file = paths.logs_dir / "ats-poll.log"

    def log(msg: str) -> None:
        line = f"[{clock().strftime('%Y-%m-%d %H:%M:%S %Z')}] {msg}"
        print(line)
        try:
            with paths.open_write(log_file, "a") as fh:
                fh.write(line + "\n")
        except Exception:  # noqa: BLE001  (logging never fails the run)
            pass

    return log


def main() -> int:
    ap = argparse.ArgumentParser(description="JobFinderOS local direct-ATS poller (jfgraph poller preset)")
    ap.add_argument("--seed", action="store_true", help="mark all current reqs seen; write nothing else")
    ap.add_argument("--dry-run", action="store_true", help="print decisions, change nothing")
    ap.add_argument("--reconsider-dropped", action="store_true", help="re-evaluate reqs dropped in earlier runs")
    ap.add_argument("--status", action="store_true", help="print state and exit")
    args = ap.parse_args()

    paths = Paths.live()
    if args.status:
        st = read_seen_summary(paths)
        if st:
            print(f"seen reqs : {st['count']}")
            print(f"last run  : {st['last_run']}")
            print(f"note      : {st['note']}")
        else:
            print("no state yet - run with --seed first")
        ib = inbox_path(paths)
        print(f"inbox     : {ib} ({'exists' if ib.exists() else 'not created'})")
        return 0

    cfg = load_discovery_config(paths)      # comp rule and thresholds from config/discovery.yaml (default: top-of-range)
    tz = ZoneInfo(cfg.filters.timezone) if (cfg.filters.timezone and ZoneInfo) else None
    clock = (lambda: datetime.now(tz)) if tz else datetime.now
    log = make_logger(paths, clock)

    transport = ThrottledTransport(LiveTransport(cfg.filters.user_agent), min_gap_s=float(cfg.concurrency.get("throttle_seconds", 0.4)))
    run_id = clock().strftime("%Y%m%dT%H%M%S") + "-poller"
    quiet = args.dry_run or args.seed
    ctx = RunContext(
        run_id=run_id, paths=paths, cfg=cfg, transport=transport, llm=None,
        notifier=NullNotifier() if quiet else MacNotifier(), clock=clock, sim=False, log=log,
        extra={"dry_run": args.dry_run, "seed": args.seed, "reconsider_dropped": args.reconsider_dropped, **graph_flags("poller")},
    )
    state = build_graph("poller").run(ctx, new_state(run_id, "poller", "live", clock().strftime("%Y-%m-%d")))

    by = {r["node"]: r for r in funnel(state.get("node_metrics", []))}
    log(f"{by.get('fetch_boards', {}).get('out', 0)} reqs fetched; {len(state.get('new_postings', []))} new; "
        f"{len(state.get('location_pass', []))} passed title+location; {len(state.get('comp_pass', []))} passed the comp floor")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
