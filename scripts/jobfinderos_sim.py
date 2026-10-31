#!/usr/bin/env python3
"""JobFinderOS discovery simulations.

    jobfinderos_sim.py init     --instance NAME --profile-dir sim/profiles/NAME [--registry sim/registry.yaml] [--force]
    jobfinderos_sim.py snapshot --instance NAME [--throttle 0.4]
    jobfinderos_sim.py label    --instance NAME [--model sonnet] [--live] [--max-live-calls 5] [--second-opinion opus] [--review] [--finalize]
    jobfinderos_sim.py run      --instance NAME --graph poller|A|B|C|D [--repeat N] [--live] [--max-live-calls 5] [--set k=v ...]
    jobfinderos_sim.py compare  --instance NAME [--runs RUN_ID ...]
    jobfinderos_sim.py runs     --instance NAME

Everything an instance writes lives under sim/instances/NAME (gitignored). Live LLM
calls are capped per command (default 5); re-runs replay from the cache for free.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from jfgraph import sim  # noqa: E402


def parse_overrides(items: list[str] | None) -> dict:
    out = {}
    for item in items or []:
        if "=" not in item:
            raise SystemExit(f"--set expects key=value, got {item!r}")
        k, v = item.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="JobFinderOS discovery simulations")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init"); s.add_argument("--instance", required=True); s.add_argument("--profile-dir", required=True)
    s.add_argument("--registry"); s.add_argument("--force", action="store_true")
    s = sub.add_parser("snapshot"); s.add_argument("--instance", required=True); s.add_argument("--throttle", type=float, default=0.4)
    s = sub.add_parser("label"); s.add_argument("--instance", required=True); s.add_argument("--model", default="sonnet")
    s.add_argument("--live", action="store_true"); s.add_argument("--max-live-calls", type=int, default=5)
    s.add_argument("--batch-size", type=int, default=20); s.add_argument("--second-opinion", metavar="MODEL")
    s.add_argument("--review", action="store_true", help="write fixtures/labels_review.md"); s.add_argument("--finalize", action="store_true")
    s = sub.add_parser("run"); s.add_argument("--instance", required=True); s.add_argument("--graph", required=True, choices=["poller", "A", "B", "C", "D"])
    s.add_argument("--repeat", type=int, default=1); s.add_argument("--live", action="store_true"); s.add_argument("--max-live-calls", type=int, default=5)
    s.add_argument("--set", action="append", metavar="KEY=VALUE"); s.add_argument("--reconsider-dropped", action="store_true")
    s.add_argument("--keep-seen", action="store_true", help="do not clear the seen-set before the run")
    s = sub.add_parser("compare"); s.add_argument("--instance", required=True); s.add_argument("--runs", nargs="*"); s.add_argument("--title")
    s.add_argument("--gates", help="gates file (default sim/gates.yaml)")
    s.add_argument("--publish-as", metavar="LABEL", help="also write this instance's section into docs/discovery-benchmark.md under LABEL (numbers only)")
    s = sub.add_parser("runs"); s.add_argument("--instance", required=True)
    s = sub.add_parser("discover-bench"); s.add_argument("--truth", default="sim/discover_truth.yaml"); s.add_argument("--live", action="store_true")
    s.add_argument("--throttle", type=float, default=0.4)
    s = sub.add_parser("shadow-report", help="live shadow phase: time-to-surface per graph from the real state dir")
    s.add_argument("--since", metavar="YYYY-MM-DD"); s.add_argument("--state-dir", help="default $JOBFINDEROS_STATE_DIR or ~/.jobfinderos")
    args = ap.parse_args()

    if args.cmd == "shadow-report":
        from jfgraph.paths import state_dir
        out = sim.shadow_report(Path(args.state_dir) if args.state_dir else state_dir(create=False), since=args.since)
        print(f"\nwritten: {out}")
        return 0
    if args.cmd == "discover-bench":
        truth = Path(args.truth)
        if not truth.is_file():
            truth = Path("sim/discover_truth.example.yaml")
        summary = sim.discover_bench(truth, live=args.live, throttle=args.throttle)
        print(json.dumps(summary, indent=1))
        return 0

    inst = sim.Instance.at(args.instance)
    if args.cmd == "init":
        meta = sim.init(inst, Path(args.profile_dir), Path(args.registry) if args.registry else None, force=args.force)
        print(json.dumps(meta, indent=1))
    elif args.cmd == "snapshot":
        m = sim.snapshot(inst, throttle=args.throttle)
        failed = [b for b in m["boards"] if b["status"] != "ok"]
        print(json.dumps({k: m[k] for k in ("snapshot_date", "total_postings", "universe_count", "universe_truncated")}, indent=1))
        if failed:
            print("boards that failed (fix the slug or drop them):")
            for b in failed:
                print(f"  {b['company']:<14} {b['ats']:<10} {b['slug']:<16} {b['status']}")
    elif args.cmd == "label":
        if not args.review and not args.finalize:
            print(json.dumps(sim.label(inst, model=args.model, batch_size=args.batch_size, live=args.live, max_live_calls=args.max_live_calls), indent=1))
            if args.second_opinion:
                print(json.dumps(sim.label(inst, model=args.second_opinion, batch_size=args.batch_size, live=args.live, max_live_calls=args.max_live_calls), indent=1))
        if args.review:
            print(f"review sheet: {sim.review_sheet(inst)}")
        if args.finalize:
            print(json.dumps(sim.finalize_labels(inst), indent=1))
    elif args.cmd == "run":
        res = sim.run(inst, args.graph, repeat=args.repeat, live=args.live, max_live_calls=args.max_live_calls, overrides=parse_overrides(args.set),
                      reconsider_dropped=args.reconsider_dropped, fresh_seen=not args.keep_seen)
        print(json.dumps(res, indent=1))
    elif args.cmd == "compare":
        print(sim.compare(inst, args.runs, title=args.title, gates_path=Path(args.gates) if args.gates else None, publish_as=args.publish_as))
    elif args.cmd == "runs":
        for d in sim.list_runs(inst):
            info = json.loads((d / "run.json").read_text(encoding="utf-8"))
            print(f"{d.name:<40} {info.get('graph', ''):<7} {info.get('status', ''):<16} {info.get('started', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
