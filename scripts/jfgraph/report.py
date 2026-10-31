"""Comparison reports: runs side by side, per-node funnels, metrics against the labels.

Phase 3 version: run table, funnel tables, metrics table with per-graph mean/best, and
the "postings only one graph found" appendix. Gates and the publishable benchmark page
arrive in Phase 7.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .metrics import funnel, pipeline_metrics, repeatability


def load_run(run_dir: Path) -> dict[str, Any]:
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    info = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    return {"run_id": info.get("run_id", run_dir.name), "graph": info.get("graph", state.get("graph")), "info": info, "state": state, "dir": run_dir}


def surfaced_ids(state: dict[str, Any]) -> list[str]:
    return sorted({c["posting"]["req_id"] for c in state.get("surfaced") or [] if c.get("posting", {}).get("req_id")})


def act_ids(state: dict[str, Any]) -> list[str]:
    return sorted({c["posting"]["req_id"] for c in state.get("act_now") or [] if c.get("posting", {}).get("req_id")})


def total_cost(state: dict[str, Any]) -> tuple[float, int]:
    cost, tokens = 0.0, 0
    for m in state.get("node_metrics") or []:
        llm = m.get("llm") or {}
        cost += float(llm.get("cost_usd", 0) or 0)
        tokens += int(llm.get("input_tokens", 0) or 0) + int(llm.get("output_tokens", 0) or 0)
    return round(cost, 4), tokens


def _fmt(v: Any) -> str:
    if v is None:
        return "–"
    if isinstance(v, float):
        return f"{v:.3f}" if abs(v) < 10 else f"{v:,.1f}"
    return str(v)


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join(_fmt(v) for v in r) + " |")
    return "\n".join(out)


def graph_stats(runs: list[dict[str, Any]], labels: dict[str, str]) -> dict[str, dict[str, Any]]:
    """Per-graph summary: mean/best recall, mean precision, mean cost per TP, Jaccard, run count."""
    by: dict[str, list[dict]] = defaultdict(list)
    sets: dict[tuple[str, str], list[set[str]]] = defaultdict(list)
    act_sets: dict[tuple[str, str], list[set[str]]] = defaultdict(list)
    for r in runs:
        s, a = surfaced_ids(r["state"]), act_ids(r["state"])
        m = pipeline_metrics(s, a, labels)
        cost, _ = total_cost(r["state"])
        m["cost_usd"] = cost
        m["cost_per_tp"] = (cost / m["tp"]) if m["tp"] else None
        by[r["graph"]].append(m)
        key = (r["graph"], str(r["info"].get("config_hash") or ""))
        sets[key].append(set(s))
        act_sets[key].append(set(a))
    out = {}
    for g, ms in by.items():
        def mean(k):
            vals = [x[k] for x in ms if x.get(k) is not None]
            return round(sum(vals) / len(vals), 4) if vals else None
        # repeatability only across runs with the same configuration (the largest such group;
        # runs recorded before config hashes existed share the empty hash and lose ties)
        groups = [(h, v) for (gg, h), v in sets.items() if gg == g and len(v) > 1]
        best_group = max(groups, key=lambda hv: (len(hv[1]), hv[0] != ""))[0] if groups else None
        rep = repeatability(sets[(g, best_group)]) if best_group is not None else None
        rep_act = repeatability(act_sets[(g, best_group)]) if best_group is not None else None
        best_i = max(range(len(ms)), key=lambda i: (ms[i]["recall"] or 0))
        out[g] = {"runs": len(ms), "recall_mean": mean("recall"), "recall_best": ms[best_i]["recall"], "precision_mean": mean("precision"),
                  "precision_best_run": ms[best_i]["precision"], "act_precision_mean": mean("act_precision"), "act_precision_best_run": ms[best_i]["act_precision"],
                  "f1_mean": mean("f1"), "cost_mean": mean("cost_usd"),
                  "cost_per_tp_mean": mean("cost_per_tp"), "cost_per_tp_best_run": ms[best_i]["cost_per_tp"],
                  "jaccard": rep["mean_jaccard"] if rep else None, "act_jaccard": rep_act["mean_jaccard"] if rep_act else None}
    return out


def _delta_check(value: float | None, ref: float | None, minimum: float | None = None, abs_max: float | None = None) -> tuple[str, str]:
    if value is None or ref is None:
        return "n/a", "missing metric"
    d = value - ref
    if minimum is not None:
        return ("PASS" if d >= minimum else "FAIL"), f"Δ {d:+.3f} vs min {minimum:+.3f}"
    if abs_max is not None:
        return ("PASS" if abs(d) <= abs_max else "FAIL"), f"|Δ| {abs(d):.3f} vs max {abs_max:.3f}"
    return "n/a", ""


def _ratio_check(value: float | None, ref: float | None, maximum: float) -> tuple[str, str]:
    if value is None or ref is None or ref == 0:
        return "n/a", "missing metric"
    ratio = value / ref
    return ("PASS" if ratio <= maximum else "FAIL"), f"ratio {ratio:.2f} vs max {maximum:.2f}"


def evaluate_gates(stats: dict[str, dict[str, Any]], gates_cfg: dict[str, Any], *, discover: dict[str, Any] | None = None,
                   term_rejection_rate: float | None = None, about_to_hire_hit_rate: float | None = None) -> list[dict[str, Any]]:
    """Rows: gate, check, verdict, detail. A reference graph with Jaccard below the noise
    floor is checked against both its mean and its best run."""
    noise = float(gates_cfg.get("baseline_noise_jaccard", 0.5))
    rows: list[dict[str, Any]] = []
    for name, g in (gates_cfg.get("gates") or {}).items():
        if name == "G-discover" or "graph" not in g:
            if discover is None:
                rows.append({"gate": name, "check": "board discovery", "verdict": "n/a", "detail": "no discover-bench summary"})
                continue
            for key, cmp_, limit in (("correct_rate", ">=", g.get("correct_rate_min")), ("wrong_board_rate", "<=", g.get("wrong_board_rate_max")), ("mean_probes", "<=", g.get("mean_probes_max"))):
                if limit is None or discover.get(key) is None:
                    continue
                ok = discover[key] >= limit if cmp_ == ">=" else discover[key] <= limit
                rows.append({"gate": name, "check": key, "verdict": "PASS" if ok else "FAIL", "detail": f"{discover[key]} {cmp_} {limit}"})
            continue
        variant, ref = stats.get(g["graph"]), stats.get(g.get("vs", ""))
        if not variant or not ref:
            rows.append({"gate": name, "check": f"{g['graph']} vs {g.get('vs')}", "verdict": "n/a", "detail": "missing runs"})
            continue
        refs = [("mean", ref["recall_mean"], ref["precision_mean"], ref["cost_per_tp_mean"], ref.get("act_precision_mean"))]
        if ref.get("jaccard") is not None and ref["jaccard"] < noise:
            refs.append(("best run", ref["recall_best"], ref["precision_best_run"], ref["cost_per_tp_best_run"], ref.get("act_precision_best_run")))
        for label, r_rec, r_prec, r_cost, r_act in refs:
            suffix = f" (vs {g.get('vs')} {label})"
            if "act_precision_delta_min" in g:
                v, d = _delta_check(variant.get("act_precision_mean"), r_act, minimum=g["act_precision_delta_min"])
                rows.append({"gate": name, "check": "act-now precision" + suffix, "verdict": v, "detail": d})
            if "recall_delta_min" in g:
                v, d = _delta_check(variant["recall_mean"], r_rec, minimum=g["recall_delta_min"])
                rows.append({"gate": name, "check": "recall" + suffix, "verdict": v, "detail": d})
            if "recall_delta_abs_max" in g:
                v, d = _delta_check(variant["recall_mean"], r_rec, abs_max=g["recall_delta_abs_max"])
                rows.append({"gate": name, "check": "recall unchanged" + suffix, "verdict": v, "detail": d})
            if "precision_delta_min" in g:
                v, d = _delta_check(variant["precision_mean"], r_prec, minimum=g["precision_delta_min"])
                rows.append({"gate": name, "check": "precision" + suffix, "verdict": v, "detail": d})
            if "precision_delta_abs_max" in g:
                v, d = _delta_check(variant["precision_mean"], r_prec, abs_max=g["precision_delta_abs_max"])
                rows.append({"gate": name, "check": "precision unchanged" + suffix, "verdict": v, "detail": d})
            if "cost_per_tp_ratio_max" in g:
                v, d = _ratio_check(variant["cost_per_tp_mean"], r_cost, g["cost_per_tp_ratio_max"])
                rows.append({"gate": name, "check": "cost per TP" + suffix, "verdict": v, "detail": d})
        if "jaccard_min" in g:
            j = variant.get("jaccard")
            rows.append({"gate": name, "check": "repeatability (all surfaced)", "verdict": "n/a" if j is None else ("PASS" if j >= g["jaccard_min"] else "FAIL"),
                         "detail": "needs 2+ runs" if j is None else f"Jaccard {j:.3f} vs min {g['jaccard_min']}"})
        if "act_jaccard_min" in g:
            j = variant.get("act_jaccard")
            rows.append({"gate": name, "check": "repeatability (act-now)", "verdict": "n/a" if j is None else ("PASS" if j >= g["act_jaccard_min"] else "FAIL"),
                         "detail": "needs 2+ runs" if j is None else f"Jaccard {j:.3f} vs min {g['act_jaccard_min']}"})
        if "term_rejection_rate_max" in g:
            rows.append({"gate": name, "check": "learned-term rejection rate", "verdict": "n/a" if term_rejection_rate is None else ("PASS" if term_rejection_rate <= g["term_rejection_rate_max"] else "FAIL"),
                         "detail": "no human review yet" if term_rejection_rate is None else f"{term_rejection_rate:.2f} vs max {g['term_rejection_rate_max']}"})
        if "about_to_hire_hit_rate_min" in g:
            rows.append({"gate": name, "check": "about-to-hire 60-day hit rate", "verdict": "n/a" if about_to_hire_hit_rate is None else ("PASS" if about_to_hire_hit_rate >= g["about_to_hire_hit_rate_min"] else "FAIL"),
                         "detail": "pending shadow phase" if about_to_hire_hit_rate is None else f"{about_to_hire_hit_rate:.2f} vs min {g['about_to_hire_hit_rate_min']}"})
    return rows


def render_gates(rows: list[dict[str, Any]]) -> str:
    return _table(["gate", "check", "verdict", "detail"], [[r["gate"], r["check"], r["verdict"], r["detail"]] for r in rows])


def render_benchmark_section(instance_label: str, stats: dict[str, dict[str, Any]], gate_rows: list[dict[str, Any]], labels: dict[str, str],
                             snapshot: dict[str, Any] | None, discover: dict[str, Any] | None) -> str:
    """The publishable version: numbers only, no company or posting rows, no profile text."""
    fit_total = sum(1 for v in labels.values() if v == "fit")
    lines = [f"### {instance_label}", ""]
    if snapshot:
        lines.append(f"Snapshot {str(snapshot.get('snapshot_date', ''))[:10]}: {snapshot.get('total_postings')} live postings across "
                     f"{sum(1 for b in snapshot.get('boards', []) if b.get('status') == 'ok')} boards; {len(labels)} labeled "
                     f"({fit_total} fit, {sum(1 for v in labels.values() if v == 'borderline')} borderline).")
        lines.append("")
    lines.append(_table(["graph", "runs", "recall mean", "recall best", "precision (log band)", "precision (act-now)", "F1 mean", "est. cost / run", "cost / true positive", "Jaccard (all / act-now)"],
                        [[g, s["runs"], s["recall_mean"], s["recall_best"], s["precision_mean"], s.get("act_precision_mean"), s["f1_mean"], s["cost_mean"], s["cost_per_tp_mean"],
                          f"{_fmt(s['jaccard'])} / {_fmt(s.get('act_jaccard'))}"]
                         for g, s in sorted(stats.items(), key=lambda kv: ("A", "poller", "B", "C", "D").index(kv[0]) if kv[0] in ("A", "poller", "B", "C", "D") else 9)]))
    lines.append("")
    if discover:
        lines.append(f"Board discovery: {discover.get('companies')} companies, correct rate {discover.get('correct_rate')}, wrong-board rate {discover.get('wrong_board_rate')}, mean probes {discover.get('mean_probes')}.")
        lines.append("")
    lines += ["**Gates**", "", render_gates(gate_rows), ""]
    return "\n".join(lines)


def compare(runs: list[dict[str, Any]], labels: dict[str, str], *, title: str, graphs_mermaid: dict[str, str] | None = None,
            postings_by_id: dict[str, dict] | None = None, gates_cfg: dict[str, Any] | None = None, discover: dict[str, Any] | None = None,
            term_rejection_rate: float | None = None) -> str:
    lines = [f"# {title}", ""]
    lines.append(_table(["run", "graph", "status", "surfaced", "act now", "est. cost USD", "tokens", "started"],
                        [[r["run_id"], r["graph"], r["info"].get("status"), len(surfaced_ids(r["state"])), len(act_ids(r["state"])),
                          *total_cost(r["state"]), r["info"].get("started", "")] for r in runs]))
    lines.append("")

    # metrics per run and per graph
    fit_total = sum(1 for v in labels.values() if v == "fit")
    lines += ["## Metrics against the labels", "", f"Labeled postings: {len(labels)} (fit {fit_total}, borderline {sum(1 for v in labels.values() if v == 'borderline')}).", ""]
    per_run = []
    for r in runs:
        s, a = surfaced_ids(r["state"]), act_ids(r["state"])
        m = pipeline_metrics(s, a, labels)
        cost, tokens = total_cost(r["state"])
        m["cost_usd"] = cost
        m["cost_per_tp"] = round(cost / m["tp"], 4) if m["tp"] else None
        per_run.append((r, m))
    lines.append(_table(["run", "graph", "config", "recall", "precision", "precision (strict)", "F1", "act precision", "TP", "FP", "FN", "outside labels", "cost/TP"],
                        [[r["run_id"], r["graph"], r["info"].get("config_label") or "", m["recall"], m["precision"], m["precision_strict"], m["f1"], m["act_precision"],
                          m["tp"], m["fp"], m["fn"], m["unlabeled_surfaced"], m["cost_per_tp"]] for r, m in per_run]))
    lines.append("")
    lines += ["### Per graph (mean / best)", "", "Jaccard is computed only across runs that share a configuration.", ""]
    stats_all = graph_stats(runs, labels)
    lines.append(_table(["graph", "runs", "recall mean", "recall best", "precision mean", "F1 mean", "cost mean", "Jaccard"],
                        [[g, s["runs"], s["recall_mean"], s["recall_best"], s["precision_mean"], s["f1_mean"], s["cost_mean"], s["jaccard"] if s["jaccard"] is not None else "–"]
                         for g, s in stats_all.items()]))
    lines.append("")

    if gates_cfg:
        stats = graph_stats(runs, labels)
        gate_rows = evaluate_gates(stats, gates_cfg, discover=discover, term_rejection_rate=term_rejection_rate)
        noisy = [g for g, s in stats.items() if s.get("jaccard") is not None and s["jaccard"] < float(gates_cfg.get("baseline_noise_jaccard", 0.5))]
        lines += ["## Gates", ""]
        if noisy:
            lines += [f"Noisy graphs (Jaccard below {gates_cfg.get('baseline_noise_jaccard', 0.5)}): {', '.join(noisy)}. Gates against them are reported against both the mean and the best run.", ""]
        lines += [render_gates(gate_rows), ""]

    # funnels
    lines += ["## Per-node funnels", ""]
    for r in runs:
        lines += [f"### {r['run_id']} ({r['graph']})", ""]
        lines.append(_table(["node", "kind", "in", "out", "drops", "wall ms", "LLM calls", "cached", "tokens", "est. cost"],
                            [[f["node"], f["kind"], f["in"], f["out"], ", ".join(f"{k}: {v}" for k, v in (f["drops"] or {}).items()) or "–",
                              f["wall_ms"], f["llm_calls"], f["cached"], f["tokens"], f["cost_usd"]] for f in funnel(r["state"].get("node_metrics", []))]))
        lines.append("")

    # diagrams
    if graphs_mermaid:
        lines += ["## Graphs", ""]
        for g, mm in graphs_mermaid.items():
            lines += [f"### {g}", "", "```mermaid", mm, "```", ""]

    # appendix: found by only one graph
    found: dict[str, set[str]] = defaultdict(set)
    for r in runs:
        for rid in surfaced_ids(r["state"]):
            found[rid].add(r["graph"])
    only_one = {rid: gs for rid, gs in found.items() if len(gs) == 1}
    if only_one and len(stats_all) > 1:
        lines += ["## Postings only one graph found", ""]
        rows = []
        for rid, gs in sorted(only_one.items()):
            p = (postings_by_id or {}).get(rid, {})
            rows.append([rid, next(iter(gs)), labels.get(rid, "unlabeled"), p.get("company", ""), p.get("title", "")])
        lines.append(_table(["req_id", "graph", "label", "company", "title"], rows))
        lines.append("")
    return "\n".join(lines)
