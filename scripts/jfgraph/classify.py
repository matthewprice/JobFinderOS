"""Responsibility matching and the learning loop (Graph C).

A *near miss* is a posting that passes the seniority rule but not the function regex.
The classifier reads its description and says whether it is the candidate's job. Each
confirmed near miss contributes a title term; a term seen at ``min_companies`` distinct
companies (or accepted by a human) is OR'd into the function regex at config load, so
the deterministic gate learns the market's vocabulary without anyone editing YAML.

State files under the state dir:
    learned_terms.yaml   term -> {status, first_seen, companies[], req_ids[]}
    near_misses.jsonl    one line per classified posting (req_id, verdict, term, run)
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from .config import Filters

LEARNED_FILENAME = "learned_terms.yaml"
NEAR_MISS_LOG = "near_misses.jsonl"
CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {"verdicts": {"type": "array", "items": {
        "type": "object",
        "properties": {"req_id": {"type": "string"}, "fit": {"enum": ["yes", "no", "unsure"]}, "function_noun": {"type": "string"},
                       "title_term": {"type": "string"}, "confidence": {"type": "number"}, "evidence": {"type": "string"}},
        "required": ["req_id", "fit", "function_noun", "title_term", "confidence", "evidence"]}}},
    "required": ["verdicts"],
}


def normalize_term(term: str, f: Filters) -> str:
    """Lowercase, drop seniority words and punctuation, collapse spaces. Empty when the
    term is too short or already covered by the function regex."""
    t = (term or "").lower()
    if f.leadership_re is not None:
        t = f.leadership_re.sub(" ", t)
    t = re.sub(r"\b(of|the|and|for|senior|sr|lead|global|regional|team)\b", " ", t)
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) < 4 or len(t.split()) > 4:
        return ""
    if f.function_re is not None and f.function_re.search(t):
        return ""
    return t


def load_learned(path: Path) -> dict[str, dict[str, Any]]:
    if not Path(path).is_file():
        return {}
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return {str(k): dict(v) for k, v in (data.get("terms") or {}).items()}


def save_learned(paths: Any, path: Path, records: dict[str, dict[str, Any]]) -> None:
    with paths.open_write(path) as fh:
        fh.write("# Title terms learned from confirmed near misses. status: proposed | accepted | rejected.\n"
                 "# Accepted terms are OR'd into filters.function_terms at load; edit statuses freely.\n")
        yaml.safe_dump({"terms": dict(sorted(records.items()))}, fh, allow_unicode=True, sort_keys=False, width=120)


def accepted_terms(records: dict[str, dict[str, Any]]) -> list[str]:
    return sorted(t for t, r in records.items() if r.get("status") == "accepted")


def load_accepted_terms(state_dir: Path) -> list[str]:
    return accepted_terms(load_learned(Path(state_dir) / LEARNED_FILENAME))


def learn_terms(confirmed: list[dict], records: dict[str, dict[str, Any]], f: Filters, *, min_companies: int, require_review: bool,
                today: str, run_id: str) -> tuple[dict[str, dict[str, Any]], list[dict], list[dict]]:
    """Fold confirmed near misses into the learned-term records.

    Returns (records, changed records for this run, review items for terms that need a human)."""
    records = {k: dict(v) for k, v in records.items()}
    changed: dict[str, dict] = {}
    review: list[dict] = []
    for c in confirmed:
        term = normalize_term(c.get("title_term") or "", f)
        if not term:
            continue
        p = c["posting"]
        r = records.get(term) or {"term": term, "status": "proposed", "first_seen": today, "companies": [], "req_ids": [], "evidence": ""}
        if r.get("status") == "rejected":
            continue
        if p["company"] not in r["companies"]:
            r["companies"].append(p["company"])
        if p["req_id"] not in r["req_ids"]:
            r["req_ids"].append(p["req_id"])
        r["evidence"] = r.get("evidence") or (c.get("evidence") or "")[:200]
        r["last_run"] = run_id
        if r["status"] == "proposed" and len(r["companies"]) >= min_companies:
            if require_review:
                review.append({"id": f"term:{term.replace(' ', '-')}", "kind": "term",
                               "text": f"**{term}** seen at {', '.join(r['companies'])} · {r['evidence'][:120]}", "payload": {"term": term}})
            else:
                r["status"] = "accepted"
                r["accepted_on"] = today
        records[term] = r
        changed[term] = r
    return records, list(changed.values()), review


def append_near_misses(paths: Any, path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with paths.open_write(path, "a") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def load_near_miss_verdicts(path: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not Path(path).is_file():
        return out
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        out[r.get("req_id", "")] = r
    return out
