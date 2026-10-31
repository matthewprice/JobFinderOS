# Discovery simulations

How the discovery graph (`scripts/jfgraph/`) is measured against a baseline, on fictional
candidates, with a frozen snapshot of real job boards and a labeled ground truth.

Everything private lives under `sim/instances/<name>/` and `sim/profiles/` (gitignored).
What ships publicly: this README, `registry.example.yaml`, `gates.yaml`, and the curated
benchmark report at `docs/discovery-benchmark.md`.

## The protocol

```
python3 scripts/jobfinderos_sim.py init     --instance NAME --profile-dir sim/profiles/NAME
python3 scripts/jobfinderos_sim.py snapshot --instance NAME            # live board fetch, recorded for replay
python3 scripts/jobfinderos_sim.py label    --instance NAME --live     # LLM labels the universe (5 calls max)
python3 scripts/jobfinderos_sim.py label    --instance NAME --review   # checklist of disagreements + a 10% sample
python3 scripts/jobfinderos_sim.py label    --instance NAME --finalize # human edits win, else majority
python3 scripts/jobfinderos_sim.py run      --instance NAME --graph poller
python3 scripts/jobfinderos_sim.py run      --instance NAME --graph A --repeat 5   # today's scout, live
python3 scripts/jobfinderos_sim.py run      --instance NAME --graph B              # replays the snapshot
python3 scripts/jobfinderos_sim.py compare  --instance NAME [--publish-as "Profile N: one neutral sentence"]
python3 scripts/jobfinderos_sim.py discover-bench --live     # board auto-discovery against sim/discover_truth.yaml
python3 scripts/jobfinderos_sim.py shadow-report --since YYYY-MM-DD   # live phase: time-to-surface per graph
```

- **Live shadow phase**: run `scripts/jobfinderos_discover.py --graph B --shadow` (and C, D) daily beside the
  existing routine for two weeks, then `shadow-report` reads the real state dir and compares when each graph
  first surfaced each posting against the posting's own date.
- **Publishing**: `compare --publish-as` writes this instance's section (metrics, gates, no company rows,
  no profile text) into `docs/discovery-benchmark.md`.

- **Instance** = a fictional profile (`profile.md`, `scoring_rubric.md`, `ats_boards.yaml` with
  filters and a `sim:` block) plus the shared `sim/registry.yaml` of public boards.
- **Snapshot** records every board response and the descriptions of the labeling universe, so
  graphs B, C, D replay the same day's market. Graph A (the LLM scout) fetches live, so run it
  on snapshot day.
- **Universe** = postings whose title matches the profile's function terms (and its seniority
  rule when leadership-only), sampled round-robin across companies, plus a slice of adjacent
  titles and a small IC sanity sample. Recall is measured over this universe; postings outside
  it are assumed not fit. The manifest records the counts.
- **Labels** come from an LLM that sees the candidate's goals but not the search vocabulary or
  thresholds; disagreements and a sample go to a human checklist; `--finalize` resolves them.
- **Budget**: every command caps uncached `claude -p` calls (default 5). The cache under
  `state/llm-cache/` makes re-analysis free; `--live` forces fresh calls.
- **Safety**: an instance can only write inside its own directory (`jfgraph.paths.Paths.sim`);
  nothing pushes, nothing notifies, nothing touches `~/.jobfinderos`.

## Reading a compare report

Per-run table (surfaced, act-now, estimated cost, tokens), metrics against the labels
(recall, lenient and strict precision, F1, cost per true positive), per-graph mean and best
with the repeatability Jaccard over repeated runs, per-node funnels, the Mermaid diagram of
each graph, and the appendix of postings only one graph found. Acceptance gates live in
`sim/gates.yaml` (Phase 7).
