"""Simulation instances: a fictional candidate, a frozen snapshot of real boards, a
labeled ground truth, and graph runs that replay the snapshot.

Layout (``sim/instances/<name>/``, gitignored, the ONLY writable root for the instance):
    instance.json         name, profile dir, created
    CLAUDE.md, .claude/   copied / symlinked from the repo so `/jobs-scout` runs here as-is
    config/               profile.md, scoring_rubric.md, recruiter_playbook.md, ats_boards.yaml
    vault/                skeleton; Graph A's scout writes here (reset before each sample)
    vault.skeleton/       pristine copy used for the reset
    fixtures/snapshot/    recorded board + detail responses (RecordingTransport format)
    fixtures/universe.jsonl, labels.jsonl, labels_review.md, manifest.json
    state/                seen-set, runs/<run_id>/, llm-cache/
    reports/              compare reports
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from .ats_adapters import fetch_board, fetch_detail
from .config import DiscoveryConfig, load_discovery_config, load_yaml
from .graphs import build_graph, graph_flags
from .llm import ClaudeCLI, LLMCall, PromptLibrary
from .metrics import MetricsCollector
from .paths import Paths, project_root
from .report import compare as compare_report
from .report import load_run
from .runtime import NullNotifier, RunContext
from .state import new_state
from .transport import LiveTransport, RecordingTransport, SnapshotTransport, ThrottledTransport, TransportError

VAULT_SKELETON = [
    "vault/Dashboard.md", "vault/Strategy.md", "vault/Companies/README.md", "vault/Stories/_Story Template.md",
    "vault/Tracking/Contacts.md", "vault/Tracking/Companies.md", "vault/Tracking/Email Follow-ups Queue.md",
    "vault/Automation/JobFinderOS — Local Runbook.md", "vault/Automation/JobFinderOS — Schedule & Run Log.md",
]
VAULT_DIRS = ["Companies", "Stories", "Tracking", "Automation", "Daily Digests", "Market Intel", "Outreach Drafts", "Archive/Daily Jobs Watch"]
PROFILE_SECTIONS_FOR_LABELING = ("Professional Identity", "Target Roles", "Compensation", "Location & Work Arrangement", "Industries & Domains", "Excluded Companies")
LABELS_SCHEMA = {
    "type": "object",
    "properties": {"labels": {"type": "array", "items": {
        "type": "object",
        "properties": {"req_id": {"type": "string"}, "label": {"enum": ["fit", "not_fit", "borderline"]},
                       "confidence": {"type": "number"}, "rationale": {"type": "string"}},
        "required": ["req_id", "label", "confidence", "rationale"]}}},
    "required": ["labels"],
}


class SimError(RuntimeError):
    pass


def _git_show(root: Path, rel: str) -> str | None:
    try:
        out = subprocess.run(["git", "show", f"HEAD:{rel}"], cwd=root, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout if out.returncode == 0 else None


@dataclass
class Instance:
    name: str
    root: Path

    @classmethod
    def at(cls, name: str, sim_root: Path | None = None) -> "Instance":
        base = sim_root or (project_root() / "sim" / "instances")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
            raise SimError(f"bad instance name {name!r}")
        return cls(name=name, root=(base / name).resolve())

    @property
    def paths(self) -> Paths:
        return Paths.sim(self.root)

    @property
    def fixtures(self) -> Path:
        return self.root / "fixtures"

    @property
    def snapshot_dir(self) -> Path:
        return self.fixtures / "snapshot"

    @property
    def universe_path(self) -> Path:
        return self.fixtures / "universe.jsonl"

    @property
    def labels_path(self) -> Path:
        return self.fixtures / "labels.jsonl"

    @property
    def manifest_path(self) -> Path:
        return self.fixtures / "manifest.json"

    @property
    def reports_dir(self) -> Path:
        return self.root / "reports"

    def exists(self) -> bool:
        return (self.root / "instance.json").is_file()

    def require(self) -> "Instance":
        if not self.exists():
            raise SimError(f"instance {self.name!r} not found at {self.root}; run `sim init` first")
        return self

    def cfg(self, overrides: dict[str, Any] | None = None) -> DiscoveryConfig:
        from .classify import load_accepted_terms
        return load_discovery_config(self.paths, overrides=overrides, learned_terms=load_accepted_terms(self.paths.state_dir))

    def sim_settings(self) -> dict[str, Any]:
        return (load_yaml(self.paths.config_dir / "ats_boards.yaml").get("sim") or {})

    def read_jsonl(self, path: Path) -> list[dict]:
        if not path.is_file():
            return []
        return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]

    def write_jsonl(self, path: Path, rows: list[dict]) -> None:
        with self.paths.open_write(path) as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    def labels(self) -> dict[str, str]:
        return {r["req_id"]: r["label"] for r in self.read_jsonl(self.labels_path)}

    def universe(self) -> list[dict]:
        return self.read_jsonl(self.universe_path)


# ------------------------------------------------------------------------ init

def init(inst: Instance, profile_dir: Path, registry_path: Path | None, *, force: bool = False) -> dict[str, Any]:
    repo = project_root()
    profile_dir = Path(profile_dir).resolve()
    for required in ("profile.md", "scoring_rubric.md", "ats_boards.yaml"):
        if not (profile_dir / required).is_file():
            raise SimError(f"profile dir {profile_dir} is missing {required}")
    if inst.exists() and not force:
        raise SimError(f"instance {inst.name} already exists at {inst.root}; pass --force to rebuild config and vault (fixtures are kept)")
    p = inst.paths
    p.ensure_layout()

    # config: profile (with the ATS URL table rendered from the registry) + rubric + playbook + merged registry
    boards_cfg = load_yaml(profile_dir / "ats_boards.yaml")
    registry_path = registry_path or (repo / "sim" / "registry.yaml")
    if not registry_path.is_file():
        registry_path = repo / "sim" / "registry.example.yaml"
    boards = (load_yaml(registry_path).get("boards") or []) + (boards_cfg.get("boards") or [])
    profile_text = (profile_dir / "profile.md").read_text(encoding="utf-8")
    profile_text = re.sub(r"^See `config/ats_boards\.yaml`.*$", render_profile_table(boards), profile_text, count=1, flags=re.M)
    with p.open_write(p.config_dir / "profile.md") as fh:
        fh.write(profile_text)
    with p.open_write(p.config_dir / "scoring_rubric.md") as fh:
        fh.write((profile_dir / "scoring_rubric.md").read_text(encoding="utf-8"))
    playbook = repo / "config" / "recruiter_playbook.md"
    if playbook.is_file():
        with p.open_write(p.config_dir / "recruiter_playbook.md") as fh:
            fh.write(playbook.read_text(encoding="utf-8"))
    boards_cfg["boards"] = boards
    with p.open_write(p.config_dir / "ats_boards.yaml") as fh:
        fh.write(f"# Generated by `sim init` from {profile_dir.name}/ats_boards.yaml + {registry_path.name}\n")
        yaml.safe_dump(boards_cfg, fh, allow_unicode=True, sort_keys=False)
    disc_example = repo / "config" / "discovery.example.yaml"
    if (profile_dir / "discovery.yaml").is_file():
        shutil.copy(profile_dir / "discovery.yaml", p.assert_writable(p.config_dir / "discovery.yaml"))
    elif disc_example.is_file():
        shutil.copy(disc_example, p.assert_writable(p.config_dir / "discovery.yaml"))

    # workspace: CLAUDE.md + .claude (symlink) so the baseline scout behaves exactly as in the repo
    with p.open_write(inst.root / "CLAUDE.md") as fh:
        fh.write((repo / "CLAUDE.md").read_text(encoding="utf-8"))
    link = inst.root / ".claude"
    if link.is_symlink():
        p.unlink(link)
    elif link.exists():
        shutil.rmtree(p.assert_writable(link))
    p.assert_writable(link.parent)
    os.symlink(repo / ".claude", link)

    # vault skeleton from the committed files (never the private working copy)
    skel = p.assert_writable(inst.root / "vault.skeleton")
    if skel.exists():
        shutil.rmtree(skel)
    for d in VAULT_DIRS:
        p.mkdir(skel / d)
    for rel in VAULT_SKELETON:
        text = _git_show(repo, rel)
        if text is None:
            continue
        with p.open_write(skel / Path(rel).relative_to("vault")) as fh:
            fh.write(text)
    reset_vault(inst)

    meta = {"name": inst.name, "profile_dir": str(profile_dir), "registry": str(registry_path), "created": datetime.now().isoformat(timespec="seconds"),
            "boards": len(boards)}
    with p.open_write(inst.root / "instance.json") as fh:
        json.dump(meta, fh, indent=1)
    return meta


PUBLIC_BOARD_URLS = {"greenhouse": "https://boards.greenhouse.io/{slug}", "ashby": "https://jobs.ashbyhq.com/{slug}",
                     "lever": "https://jobs.lever.co/{slug}", "smartrecruiters": "https://jobs.smartrecruiters.com/{slug}"}


def render_profile_table(boards: list[dict]) -> str:
    rows = ["| Company | Tier | Careers / ATS URL | ATS |", "|---------|------|-------------------|-----|"]
    for b in boards:
        if b.get("enabled") is False:
            continue
        url = PUBLIC_BOARD_URLS.get(b.get("ats", ""), "{slug}").format(slug=b.get("slug", ""))
        rows.append(f"| {b['company']} | {b.get('tier', '2')} | {url} | {str(b.get('ats', '')).capitalize()} |")
    return "\n".join(rows)


def reset_vault(inst: Instance) -> None:
    p = inst.paths
    vault = p.assert_writable(p.vault_dir)
    if vault.exists():
        shutil.rmtree(vault)
    shutil.copytree(inst.root / "vault.skeleton", vault)


# -------------------------------------------------------------------- snapshot

def select_universe(postings: list[dict], cfg: DiscoveryConfig, adjacent_re: re.Pattern | None, cap: int, *, seed: int = 7) -> tuple[list[dict], dict[str, Any]]:
    """The labeled universe: every posting whose title matches the profile's function terms
    (any seniority, so IC titles test the seniority rule), plus a company-stratified random
    sample of *adjacent* titles (the sim's broader pattern; seniority-passing only when the
    profile is leadership-only) up to the cap. Postings outside the universe are assumed
    not_fit, which the report states as a limitation."""
    from .gates import matches_function, passes_seniority
    f = cfg.filters
    core, core_ic, adjacent = [], [], []
    for x in postings:
        title = x.get("title", "")
        senior = passes_seniority(title, f)[0]
        if f.function_re is not None and matches_function(title, f):
            if f.leadership_only and not senior:
                core_ic.append(x)                 # IC titles in the function: a sanity pool, never fit
            else:
                core.append(x)
        elif adjacent_re is not None and adjacent_re.search(title):
            if f.leadership_only and not senior:
                continue
            adjacent.append(x)

    rnd = random.Random(seed)

    def stratified(pool: list[dict], quota: int) -> list[dict]:
        """Round-robin across companies after a seeded shuffle, so no board dominates."""
        if quota <= 0 or not pool:
            return []
        if len(pool) <= quota:
            return list(pool)
        by_company: dict[str, list[dict]] = {}
        for x in pool:
            by_company.setdefault(x["company"], []).append(x)
        for lst in by_company.values():
            rnd.shuffle(lst)
        out: list[dict] = []
        companies = sorted(by_company)
        while len(out) < quota and any(by_company[c] for c in companies):
            for c in companies:
                if by_company[c] and len(out) < quota:
                    out.append(by_company[c].pop())
        return out

    if not cap:
        cap = len(core) + len(adjacent) + len(core_ic)
    ic_quota = min(len(core_ic), 10) if f.leadership_only else 0
    adjacent_quota = min(len(adjacent), max(cap // 5, 20))
    core_quota = max(0, cap - adjacent_quota - ic_quota)
    core_s = stratified(core, core_quota)
    leftover = core_quota - len(core_s)
    adjacent_s = stratified(adjacent, adjacent_quota + leftover)
    ic_s = stratified(core_ic, ic_quota)
    info = {"core_total": len(core), "core_sampled": len(core_s), "core_truncated": len(core_s) < len(core),
            "adjacent_total": len(adjacent), "adjacent_sampled": len(adjacent_s),
            "ic_total": len(core_ic), "ic_sampled": len(ic_s), "seed": seed, "cap": cap}
    order = {id(x): i for i, x in enumerate(postings)}
    universe = sorted(core_s + adjacent_s + ic_s, key=lambda x: order[id(x)])
    return universe, info


def snapshot(inst: Instance, *, throttle: float = 0.4, log=print) -> dict[str, Any]:
    inst.require()
    p = inst.paths
    cfg = inst.cfg()
    settings = inst.sim_settings()
    universe_re = re.compile("(" + settings["universe_title_terms"] + ")", re.I) if settings.get("universe_title_terms") else None
    cap = int(settings.get("universe_max_postings", 0) or 0)

    snap = p.assert_writable(inst.snapshot_dir)
    if snap.exists():
        shutil.rmtree(snap)
    transport = RecordingTransport(ThrottledTransport(LiveTransport(cfg.filters.user_agent), min_gap_s=throttle), snap, paths=p)

    boards_report, all_postings = [], []
    for b in cfg.active_boards:
        try:
            postings = fetch_board(b, transport)
            boards_report.append({"company": b["company"], "ats": b["ats"], "slug": b["slug"], "status": "ok", "count": len(postings)})
            all_postings.extend(postings)
            log(f"  {b['company']:<14} {b['ats']:<10} {len(postings):>4} postings")
        except (TransportError, ValueError) as exc:
            boards_report.append({"company": b["company"], "ats": b["ats"], "slug": b["slug"], "status": f"error: {exc}", "count": 0})
            log(f"  {b['company']:<14} {b['ats']:<10} FAILED {exc}")

    universe, uni_info = select_universe(all_postings, cfg, universe_re, cap, seed=int(settings.get("universe_seed", 7)))
    truncated = uni_info["adjacent_sampled"] < uni_info["adjacent_total"] or uni_info["core_truncated"]
    log(f"{len(all_postings)} postings fetched; universe {len(universe)}: core {uni_info['core_sampled']}/{uni_info['core_total']}, "
        f"adjacent {uni_info['adjacent_sampled']}/{uni_info['adjacent_total']}, IC sanity {uni_info['ic_sampled']}/{uni_info['ic_total']}")

    rows = []
    for x in universe:
        desc = x.get("description") or ""
        if x.get("ats") == "greenhouse":
            try:
                desc = fetch_detail(x, transport)
            except TransportError as exc:
                log(f"  detail failed {x['req_id']}: {exc}")
        rows.append({k: x.get(k, "") for k in ("req_id", "company", "ats", "tier", "title", "location", "url", "posted_at")} | {"description": desc[:3000]})
    inst.write_jsonl(inst.universe_path, rows)

    index = snap / "index.jsonl"
    manifest = {
        "instance": inst.name, "snapshot_date": datetime.now().isoformat(timespec="seconds"), "boards": boards_report,
        "total_postings": len(all_postings), "universe_count": len(rows), "universe_truncated": truncated, "universe": uni_info,
        "universe_title_terms": settings.get("universe_title_terms", ""),
        "index_sha256": hashlib.sha256(index.read_bytes()).hexdigest() if index.is_file() else None,
    }
    with p.open_write(inst.manifest_path) as fh:
        json.dump(manifest, fh, indent=1)
    return manifest


# ----------------------------------------------------------------------- label

from .profile import candidate_brief  # noqa: E402  (the labeler and the scorer share the same brief)


def _postings_block(batch: list[dict], desc_chars: int) -> str:
    out = []
    for x in batch:
        out.append(f"### {x['req_id']}\n- Title: {x['title']}\n- Company: {x['company']}\n- Location: {x.get('location') or '(none)'}\n"
                   f"- Description: {(x.get('description') or '(none)')[:desc_chars]}\n")
    return "\n".join(out)


def label(inst: Instance, *, model: str = "sonnet", batch_size: int = 20, desc_chars: int = 1200, live: bool = False,
          max_live_calls: int = 5, labeler_tag: str | None = None, log=print) -> dict[str, Any]:
    inst.require()
    p = inst.paths
    cfg = inst.cfg()
    universe = inst.universe()
    if not universe:
        raise SimError("no universe.jsonl; run `sim snapshot` first")
    lib = PromptLibrary(Path(__file__).resolve().parent / "prompts")
    template, version = lib.load("label_ground_truth")
    brief = candidate_brief((p.config_dir / "profile.md").read_text(encoding="utf-8"))
    metrics = MetricsCollector()
    cli = ClaudeCLI(p.cache_dir, live=live, max_live_calls=max_live_calls, metrics=metrics, paths=p)
    metrics.start_node("label", "llm", 1, len(universe))

    tag = labeler_tag or f"llm:{model}"
    existing = {r["req_id"]: r for r in inst.read_jsonl(inst.labels_path) if r.get("labeler") == tag}
    rows: list[dict] = []
    todo = [x for x in universe if x["req_id"] not in existing]
    rows.extend(existing.values())
    for i in range(0, len(todo), batch_size):
        batch = todo[i:i + batch_size]
        prompt = template.replace("{candidate_brief}", brief).replace("{postings}", _postings_block(batch, desc_chars))
        call = LLMCall(node="label", prompt_name="label_ground_truth", prompt_version=version, prompt=prompt, schema=LABELS_SCHEMA,
                       model=model, tools=(), max_budget_usd=0.40, timeout_s=300)
        result = cli.call(call)
        got = {r["req_id"]: r for r in ((result.structured or {}).get("labels") or []) if isinstance(r, dict)}
        for x in batch:
            r = got.get(x["req_id"])
            rows.append({"req_id": x["req_id"], "label": (r or {}).get("label", "borderline"), "confidence": float((r or {}).get("confidence", 0) or 0),
                         "rationale": (r or {}).get("rationale", "(no label returned)"), "labeler": tag, "title": x["title"], "company": x["company"]})
        log(f"  labeled {min(i + batch_size, len(todo))}/{len(todo)}" + (" (replayed)" if result.cached else f" est. ${result.cost_usd:.3f}"))
    m = metrics.finish_node(items_out=len(rows), drops={})

    # merge with other labelers' rows already on disk
    others = [r for r in inst.read_jsonl(inst.labels_path) if r.get("labeler") != tag]
    inst.write_jsonl(inst.labels_path, others + rows)
    counts = {k: sum(1 for r in rows if r["label"] == k) for k in ("fit", "not_fit", "borderline")}
    return {"labeler": tag, "labeled": len(rows), "counts": counts, "llm": m.llm, "live_calls": cli.live_calls}


def review_sheet(inst: Instance, *, sample_rate: float = 0.10, seed: int = 7) -> Path:
    """Disagreements between labelers plus a random slice of agreements, as a checklist."""
    rows = inst.read_jsonl(inst.labels_path)
    by_id: dict[str, dict[str, dict]] = {}
    for r in rows:
        if r.get("labeler", "") in ("final",) or r.get("labeler", "").startswith("human"):
            continue
        by_id.setdefault(r["req_id"], {})[r["labeler"]] = r
    rnd = random.Random(seed)
    lines = ["# Label review", "", "Tick the box and write the final label after `final:` (fit / not_fit / borderline). `sim label --finalize` folds it in.", ""]
    n_dis = 0
    for rid, per in sorted(by_id.items()):
        labels = {v["label"] for v in per.values()}
        disagree = len(labels) > 1
        if not disagree and rnd.random() > sample_rate:
            continue
        n_dis += disagree
        any_row = next(iter(per.values()))
        votes = " · ".join(f"{k}: {v['label']} ({float(v.get('confidence', 0)):.2f}) {v.get('rationale', '')}" for k, v in per.items())
        lines.append(f"- [ ] `{rid}` **{any_row.get('company', '')} — {any_row.get('title', '')}** {'DISAGREE' if disagree else 'sample'} · {votes} · final: ")
    path = inst.fixtures / "labels_review.md"
    with inst.paths.open_write(path) as fh:
        fh.write("\n".join(lines) + "\n")
    return path


def finalize_labels(inst: Instance) -> dict[str, Any]:
    """Resolve one label per posting: a human line wins, else majority, else the first labeler."""
    rows = inst.read_jsonl(inst.labels_path)
    human: dict[str, str] = {}
    review = inst.fixtures / "labels_review.md"
    if review.is_file():
        for line in review.read_text(encoding="utf-8").splitlines():
            m = re.match(r"- \[[xX]\] `([^`]+)`.*final:\s*(fit|not_fit|borderline)", line)
            if m:
                human[m.group(1)] = m.group(2)
    by_id: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("labeler") == "final":
            continue
        by_id.setdefault(r["req_id"], []).append(r)
    final = []
    for rid, rs in by_id.items():
        if rid in human:
            lab, who = human[rid], "human"
        else:
            votes: dict[str, int] = {}
            for r in rs:
                votes[r["label"]] = votes.get(r["label"], 0) + 1
            lab = max(votes.items(), key=lambda kv: (kv[1], kv[0] == rs[0]["label"]))[0]
            who = "majority" if len(rs) > 1 else rs[0]["labeler"]
        final.append({"req_id": rid, "label": lab, "labeler": "final", "resolved_by": who, "title": rs[0].get("title", ""), "company": rs[0].get("company", "")})
    keep = [r for r in rows if r.get("labeler") != "final"]
    inst.write_jsonl(inst.labels_path, keep + final)
    agree = sum(1 for rs in by_id.values() if len({r["label"] for r in rs}) == 1 and len(rs) > 1)
    multi = sum(1 for rs in by_id.values() if len(rs) > 1)
    return {"final": len(final), "human_overrides": len(human), "agreement_rate": round(agree / multi, 3) if multi else None,
            "counts": {k: sum(1 for r in final if r["label"] == k) for k in ("fit", "not_fit", "borderline")}}


# ------------------------------------------------------------------------- run

def run(inst: Instance, graph: str, *, repeat: int = 1, live: bool = False, max_live_calls: int = 5, overrides: dict[str, Any] | None = None,
        reconsider_dropped: bool = False, fresh_seen: bool = True, log=print) -> list[dict[str, Any]]:
    inst.require()
    if not inst.snapshot_dir.is_dir():
        raise SimError("no snapshot; run `sim snapshot` first")
    p = inst.paths
    overrides = dict(overrides or {})
    if graph in ("C", "D") and "classifier.only_req_ids_file" not in overrides and inst.universe_path.is_file():
        # the labeled universe is the measurement frame; classifying outside it spends calls recall cannot see
        overrides["classifier.only_req_ids_file"] = str(inst.universe_path)
    cfg = inst.cfg(overrides)
    # runs are only "repeats" of each other when their configuration is identical
    material = json.dumps({"overrides": {k: v for k, v in overrides.items() if k != "classifier.only_req_ids_file"},
                           "boards": (inst.paths.config_dir / "ats_boards.yaml").read_text(encoding="utf-8"),
                           "discovery": (inst.paths.config_dir / "discovery.yaml").read_text(encoding="utf-8") if (inst.paths.config_dir / "discovery.yaml").is_file() else "",
                           "reconsider": reconsider_dropped}, sort_keys=True)
    config_hash = hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]
    config_label = ", ".join(f"{k}={v}" for k, v in sorted(overrides.items()) if k != "classifier.only_req_ids_file") or "defaults"
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    results = []
    for sample in range(max(1, repeat)):
        run_id = f"{graph}-{stamp}-s{sample}"
        if fresh_seen:
            seen = p.state_dir / "ats_seen.json"
            if seen.exists():
                seen.unlink()
        if graph == "A":
            reset_vault(inst)
        sig_fixture = inst.fixtures / "signals.jsonl"
        if graph == "D" and sig_fixture.is_file():
            with p.open_write(p.vault_dir / "Market Intel" / "Signals.jsonl") as fh:
                fh.write(sig_fixture.read_text(encoding="utf-8"))
        metrics = MetricsCollector()
        ctx = RunContext(
            run_id=run_id, paths=p, cfg=cfg, transport=SnapshotTransport(inst.snapshot_dir),
            llm=ClaudeCLI(p.cache_dir, live=live, max_live_calls=max_live_calls, metrics=metrics, paths=p),
            notifier=NullNotifier(), clock=datetime.now, sim=True, metrics=metrics, log=log,
            extra={**graph_flags(graph), "sample": sample, "workspace": str(inst.root), "reconsider_dropped": reconsider_dropped,
                   "config_hash": config_hash, "config_label": config_label},
        )
        state = build_graph(graph).run(ctx, new_state(run_id, graph, "sim", datetime.now().strftime("%Y-%m-%d")))
        if graph == "A":
            keep = p.assert_writable(ctx.run_dir / "vault")
            if keep.exists():
                shutil.rmtree(keep)
            shutil.copytree(p.vault_dir, keep)
        status = json.loads((ctx.run_dir / "run.json").read_text(encoding="utf-8")).get("status")
        results.append({"run_id": run_id, "graph": graph, "status": status, "surfaced": len(state.get("surfaced") or []),
                        "act_now": len(state.get("act_now") or []), "live_calls": ctx.llm.live_calls, "run_dir": str(ctx.run_dir)})
        log(f"run {run_id}: {status}, surfaced {results[-1]['surfaced']}, act {results[-1]['act_now']}, live calls {ctx.llm.live_calls}")
        if status == "budget_exceeded":
            log("live call budget reached; stopping further samples")
            break
    return results


def list_runs(inst: Instance) -> list[Path]:
    runs = inst.paths.runs_dir
    return sorted([d for d in runs.iterdir() if (d / "run.json").is_file()], key=lambda d: d.name) if runs.is_dir() else []


# --------------------------------------------------------------- discover bench

def discover_bench(truth_path: Path, *, live: bool = False, throttle: float = 0.4, cfg_discover: dict | None = None, log=print) -> dict[str, Any]:
    """Score ``discover_board`` against known answers. Probes are recorded under
    ``sim/instances/_bench/probes`` so later runs replay for free."""
    from .config import DEFAULT_DISCOVERY
    from .discover import discover_board
    bench = Instance.at("_bench")
    p = bench.paths
    p.ensure_layout()
    probes_dir = bench.root / "probes"
    if live:
        transport = RecordingTransport(ThrottledTransport(LiveTransport("JobFinderOS-Sim/1.0 (discovery benchmark)"), min_gap_s=throttle), probes_dir, paths=p)
    else:
        if not probes_dir.is_dir():
            raise SimError("no recorded probes; run `sim discover-bench --live` once")
        transport = SnapshotTransport(probes_dir)
    cfg = dict(DEFAULT_DISCOVERY["discover"], **(cfg_discover or {}))
    truth = load_yaml(Path(truth_path)).get("companies") or []
    rows, counts = [], {"correct": 0, "wrong_board": 0, "not_found": 0, "ambiguous": 0, "rate_limited": 0, "unprobed": 0, "correct_none": 0}
    probe_total = 0
    for t in truth:
        res = discover_board(t["company"], transport, cfg)
        probe_total += len(res.get("probes") or [])
        expected_none = str(t.get("ats", "")).lower() in ("none", "", "null")
        if res["status"] == "found":
            if expected_none or (res["ats"], res["slug"]) != (t.get("ats"), t.get("slug")):
                verdict = "wrong_board"
            else:
                verdict = "correct"
        elif res["status"] in ("not_found", "unprobed") and expected_none:
            verdict = "correct_none"
        else:
            verdict = res["status"]
        counts[verdict] = counts.get(verdict, 0) + 1
        rows.append({"company": t["company"], "expected": f"{t.get('ats')}/{t.get('slug', '')}", "got": f"{res['status']} {res.get('ats') or ''}/{res.get('slug') or ''}",
                     "confidence": res.get("confidence", 0.0), "probes": len(res.get("probes") or []), "verdict": verdict})
        log(f"  {t['company']:<14} {verdict:<12} {rows[-1]['got']:<32} conf {float(res.get('confidence', 0)):.2f} probes {rows[-1]['probes']}")
    n = len(truth)
    summary = {"companies": n, **counts, "mean_probes": round(probe_total / n, 2) if n else 0,
               "correct_rate": round((counts["correct"] + counts["correct_none"]) / n, 3) if n else None,
               "wrong_board_rate": round(counts["wrong_board"] / n, 3) if n else None, "live": live}
    out = bench.reports_dir / f"discover-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
    lines = ["# Board discovery benchmark", "", f"Truth: `{truth_path}` · {'live probes' if live else 'replayed probes'}", "",
             "| metric | value |", "|---|---|", *[f"| {k} | {v} |" for k, v in summary.items()], "",
             "| company | expected | got | confidence | probes | verdict |", "|---|---|---|---|---|---|",
             *[f"| {r['company']} | {r['expected']} | {r['got']} | {r['confidence']:.2f} | {r['probes']} | {r['verdict']} |" for r in rows], ""]
    with p.open_write(out) as fh:
        fh.write("\n".join(lines))
    summary["report"] = str(out)
    return summary


# --------------------------------------------------------------------- compare

def latest_discover_summary() -> dict[str, Any] | None:
    bench = Instance.at("_bench")
    reports = sorted(bench.reports_dir.glob("discover-*.md")) if bench.reports_dir.is_dir() else []
    if not reports:
        return None
    out: dict[str, Any] = {}
    for line in reports[-1].read_text(encoding="utf-8").splitlines():
        m = re.match(r"\| (\w+) \| ([^|]+) \|$", line.strip())
        if m and m.group(1) in ("companies", "correct_rate", "wrong_board_rate", "mean_probes", "correct", "wrong_board"):
            try:
                out[m.group(1)] = float(m.group(2)) if "." in m.group(2) else int(m.group(2))
            except ValueError:
                pass
    return out or None


def term_rejection_rate(inst: Instance) -> float | None:
    from .classify import LEARNED_FILENAME, load_learned
    recs = load_learned(inst.paths.state_dir / LEARNED_FILENAME)
    judged = [r for r in recs.values() if r.get("status") in ("accepted", "rejected") and r.get("accepted_on") is None or r.get("status") == "rejected"]
    reviewed = [r for r in recs.values() if r.get("status") == "rejected" or (r.get("status") == "accepted" and r.get("reviewed"))]
    if not reviewed:
        return None
    return round(sum(1 for r in reviewed if r["status"] == "rejected") / len(reviewed), 3)


def compare(inst: Instance, run_ids: list[str] | None = None, *, title: str | None = None, gates_path: Path | None = None,
            publish_as: str | None = None) -> Path:
    inst.require()
    dirs = list_runs(inst)
    if run_ids:
        wanted = set(run_ids)
        dirs = [d for d in dirs if d.name in wanted]
    if not dirs:
        raise SimError("no runs to compare")
    runs = [load_run(d) for d in dirs]
    labels = inst.labels()
    postings = {x["req_id"]: x for x in inst.universe()}
    graphs = {}
    for g in sorted({r["graph"] for r in runs}):
        try:
            graphs[g] = build_graph(g).mermaid()
        except Exception:  # noqa: BLE001
            pass
    gates_path = gates_path or (project_root() / "sim" / "gates.yaml")
    gates_cfg = load_yaml(gates_path) if Path(gates_path).is_file() else None
    discover = latest_discover_summary()
    rate = term_rejection_rate(inst)
    md = compare_report(runs, labels, title=title or f"Discovery comparison — {inst.name}", graphs_mermaid=graphs, postings_by_id=postings,
                        gates_cfg=gates_cfg, discover=discover, term_rejection_rate=rate)
    out = inst.reports_dir / f"compare-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
    with inst.paths.open_write(out) as fh:
        fh.write(md)
    if publish_as:
        publish_benchmark(inst, runs, labels, gates_cfg or {}, discover, rate, publish_as)
    return out


def shadow_report(state_dir: Path, *, since: str | None = None, log=print) -> Path:
    """Live shadow phase: graphs run side by side on the real registry over days. For each
    graph, when did it first surface each posting relative to the posting's own date, and
    what did only one graph find? Reads ``<state_dir>/runs/*``; writes ``<state_dir>/reports/``."""
    from statistics import median
    state_dir = Path(state_dir)
    runs_dir = state_dir / "runs"
    first_seen: dict[str, dict[str, tuple[str, str]]] = {}      # graph -> req_id -> (run_date, posted_at)
    titles: dict[str, str] = {}
    n_runs = 0
    for d in sorted(runs_dir.iterdir()) if runs_dir.is_dir() else []:
        try:
            info = json.loads((d / "run.json").read_text(encoding="utf-8"))
            state = json.loads((d / "state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if info.get("status") != "ok" or (since and str(state.get("run_date", "")) < since):
            continue
        n_runs += 1
        g = info.get("graph", "?")
        for c in state.get("surfaced") or []:
            p = c.get("posting") or {}
            rid = p.get("req_id")
            if not rid:
                continue
            titles[rid] = f"{p.get('company', '')} — {p.get('title', '')}"
            first_seen.setdefault(g, {}).setdefault(rid, (state.get("run_date", ""), p.get("posted_at", "")))
    lines = [f"# Shadow report — {datetime.now().strftime('%Y-%m-%d')}", "", f"Runs considered: {n_runs}" + (f" since {since}" if since else ""), ""]
    lines += ["| graph | postings surfaced | median hours posting → surfaced | within 24h | within 72h |", "|---|---|---|---|---|"]
    for g, seen in sorted(first_seen.items()):
        lags = []
        for rid, (run_date, posted) in seen.items():
            try:
                lag = (datetime.fromisoformat(run_date) - datetime.fromisoformat(str(posted).replace("Z", "+00:00")).replace(tzinfo=None)).total_seconds() / 3600
                if lag >= 0:
                    lags.append(lag)
            except ValueError:
                continue
        med = round(median(lags), 1) if lags else "–"
        lines.append(f"| {g} | {len(seen)} | {med} | {sum(1 for x in lags if x <= 24)} | {sum(1 for x in lags if x <= 72)} |")
    all_ids = {rid for seen in first_seen.values() for rid in seen}
    only = {rid: [g for g, seen in first_seen.items() if rid in seen] for rid in all_ids}
    only = {rid: gs for rid, gs in only.items() if len(gs) == 1}
    if only and len(first_seen) > 1:
        lines += ["", "## Found by only one graph", "", "| graph | posting | first surfaced |", "|---|---|---|"]
        for rid, gs in sorted(only.items(), key=lambda kv: kv[1][0]):
            lines.append(f"| {gs[0]} | {titles.get(rid, rid)} | {first_seen[gs[0]][rid][0]} |")
    out = state_dir / "reports" / f"shadow-{datetime.now().strftime('%Y%m%d')}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log("\n".join(lines))
    return out


BENCH_DOC = project_root() / "docs" / "discovery-benchmark.md"
BENCH_HEADER = """# Discovery benchmark

How the discovery graph (`scripts/jfgraph/`) compares with the original LLM scout, measured on
fictional candidates against a frozen snapshot of real public job boards with a labeled ground
truth. The protocol is in `sim/README.md`; the gates in `sim/gates.yaml`.

Graphs: **A** = the original `/jobs-scout` (an LLM fetching and judging boards), **poller** = the
deterministic ATS poller alone, **B** = poller-first with board auto-discovery, a web sweep, and
LLM scoring, **C** = B plus the responsibility classifier and learned title terms, **D** = C plus
hiring signals and tier promotion. Recall is measured over the labeled universe; costs are the
CLI's estimates under subscription auth.

"""


def publish_benchmark(inst: Instance, runs: list[dict], labels: dict[str, str], gates_cfg: dict, discover: dict | None, rate: float | None, label: str) -> Path:
    """Write or replace this instance's section in docs/discovery-benchmark.md (a repo file the
    owner asked to publish: numbers only, no profile text, no company rows)."""
    from .report import evaluate_gates, graph_stats, render_benchmark_section
    stats = graph_stats(runs, labels)
    rows = evaluate_gates(stats, gates_cfg, discover=discover, term_rejection_rate=rate) if gates_cfg else []
    snapshot = json.loads(inst.manifest_path.read_text(encoding="utf-8")) if inst.manifest_path.is_file() else None
    section = render_benchmark_section(label, stats, rows, labels, snapshot, discover)
    start, end = f"<!-- instance:{inst.name} -->", f"<!-- /instance:{inst.name} -->"
    body = BENCH_DOC.read_text(encoding="utf-8") if BENCH_DOC.is_file() else BENCH_HEADER
    block = f"{start}\n{section}\n{end}\n"
    if start in body and end in body:
        body = body[: body.index(start)] + block + body[body.index(end) + len(end):].lstrip("\n")
    else:
        body = body.rstrip("\n") + "\n\n" + block
    BENCH_DOC.parent.mkdir(parents=True, exist_ok=True)
    BENCH_DOC.write_text(body, encoding="utf-8")
    return BENCH_DOC
