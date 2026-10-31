"""Human review as a node, using the vault's checkbox habit.

Pending items are written to one note (``Market Intel/Discovery Review.md``). The
candidate ticks a box in Obsidian; the next run reads the answers. A ticked line is an
accept; a ticked line containing ``REJECT`` is a reject. Simulations answer through
``AutoPolicy`` instead.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Protocol

KINDS = {"board": "Boards to enroll", "term": "Learned title terms", "act": "Act-now candidates"}


class ReviewPolicy(Protocol):
    def answer(self, item: dict[str, Any]) -> str | None: ...   # "accept" | "reject" | None


class AutoPolicy:
    def __init__(self, mode: str = "accept", truth: dict[str, str] | None = None) -> None:
        self.mode = mode
        self.truth = truth or {}
        self.asked: list[str] = []

    def answer(self, item: dict[str, Any]) -> str | None:
        self.asked.append(item["id"])
        if self.mode == "from_truth":
            return self.truth.get(item["id"])
        if self.mode in ("accept", "reject"):
            return self.mode
        return None


class VaultCheckboxPolicy:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._answers: dict[str, str] | None = None

    def answer(self, item: dict[str, Any]) -> str | None:
        if self._answers is None:
            self._answers = read_review_answers(self.path)
        return self._answers.get(item["id"])


def review_note_path(paths: Any, review_cfg: dict[str, Any]) -> Path:
    return paths.vault_dir / (review_cfg or {}).get("note", "Market Intel/Discovery Review.md")


def read_review_answers(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not Path(path).is_file():
        return out
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        m = re.match(r"- \[(?P<box>[ xX])\] `(?P<id>[^`]+)`(?P<rest>.*)$", line)
        if not m:
            continue
        if m.group("box").lower() == "x":
            out[m.group("id")] = "reject" if "REJECT" in m.group("rest") else "accept"
    return out


def write_review_note(paths: Any, path: Path, pending: list[dict[str, Any]], *, now_text: str = "") -> int:
    """Rewrite the note from ``pending`` while keeping lines already answered."""
    answered = read_review_answers(path)
    existing_lines: dict[str, str] = {}
    if Path(path).is_file():
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            m = re.match(r"- \[[ xX]\] `([^`]+)`", line)
            if m:
                existing_lines[m.group(1)] = line
    by_kind: dict[str, list[str]] = {k: [] for k in KINDS}
    seen = set()
    for item in pending:
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        line = existing_lines.get(item["id"]) if item["id"] in answered else None
        if line is None:
            line = f"- [ ] `{item['id']}` {item.get('text', '')}".rstrip()
        by_kind.setdefault(item.get("kind", "board"), []).append(line)
    for rid, line in existing_lines.items():
        if rid in answered and rid not in seen:
            kind = rid.split(":", 1)[0] if rid.split(":", 1)[0] in KINDS else "board"
            by_kind[kind].append(line)
    out = ["> **JobFinderOS:** Discovery Review · tick a box to accept; add `REJECT` on the line to decline · [[Dashboard]]", "",
           "# Discovery Review", "", f"Written by the discovery run{(' ' + now_text) if now_text else ''}. Answers are read at the next run.", ""]
    n = 0
    for kind, title in KINDS.items():
        lines = by_kind.get(kind) or []
        if not lines:
            continue
        out += [f"## {title}", "", *lines, ""]
        n += len(lines)
    with paths.open_write(path) as fh:
        fh.write("\n".join(out) + "\n")
    return n
