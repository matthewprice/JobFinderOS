import tempfile
import unittest
from pathlib import Path

from jfgraph.paths import Paths
from jfgraph.profile import board_from_url, candidate_brief, parse_profile_table, role_titles
from jfgraph.registry import digest_companies, handoff_companies, merge_sources, registry_changed, write_boards_yaml
from jfgraph.review import AutoPolicy, read_review_answers, write_review_note

PROFILE = """# P

## Professional Identity
I lead things.

## Target Companies
- **Tier 1:** Acme

### Direct ATS URL table

| Company | Tier | Careers / ATS URL | ATS |
|---------|------|-------------------|-----|
| Acme | 1 | https://boards.greenhouse.io/acme | Greenhouse |
| Bolt | 2 | https://jobs.ashbyhq.com/bolt | Ashby |
| Zeta | 2 | https://zeta.example/careers | site |
| <Name> | 1 | <https://…> | <Greenhouse / Lever / Ashby / Workday / site> |

## Keywords (for searching)
**Role titles to search:**
```
Head of Widgets
Director, Widgets
```
"""


class ProfileTests(unittest.TestCase):
    def test_table_and_urls(self):
        rows = parse_profile_table(PROFILE)
        self.assertEqual([(r["company"], r["ats"], r["slug"], r["tier"]) for r in rows],
                         [("Acme", "greenhouse", "acme", "1"), ("Bolt", "ashby", "bolt", "2"), ("Zeta", None, None, "2")])
        self.assertEqual(board_from_url("https://job-boards.greenhouse.io/figma/jobs/1"), ("greenhouse", "figma"))
        self.assertEqual(board_from_url("https://jobs.lever.co/zoox/abc"), ("lever", "zoox"))
        self.assertIsNone(board_from_url("https://example.com"))
        self.assertEqual(role_titles(PROFILE), ["Head of Widgets", "Director, Widgets"])
        self.assertIn("I lead things", candidate_brief(PROFILE))
        self.assertNotIn("Head of Widgets", candidate_brief(PROFILE))


class MergeTests(unittest.TestCase):
    def test_merge_sources(self):
        registry = [{"company": "Acme", "ats": "greenhouse", "slug": "acme", "tier": "1"}]
        handoff = {"priority_now": [{"company": "Cue", "why_now": "x"}], "new_to_evaluate": [{"company": "Acme Inc", "action": "y"}]}
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "Daily Digests").mkdir()
            (vault / "Daily Digests" / "2026-10-30.md").write_text("# D\n\n## New Opportunities (scored 7+)\n| Company | Role | Score |\n|---|---|---|\n| Delta | VP | 8 |\n| Acme | Head | 7 |\n\n## Next\n", encoding="utf-8")
            reg, pending = merge_sources(registry, profile_text=PROFILE, handoff=handoff, vault_dir=vault, extra_companies=[{"company": "Echo"}],
                                         known_missing={"echo": {"status": "not_found"}})
            self.assertEqual(digest_companies(vault), ["Delta", "Acme"])
        self.assertEqual([(b["company"], b.get("source")) for b in reg], [("Acme", None), ("Bolt", "profile_table")])
        self.assertEqual(pending, ["Zeta", "Cue", "Delta"])          # Acme Inc dedupes to Acme; Echo is a known miss
        self.assertEqual(handoff_companies(handoff)[0]["tier"], "1")
        self.assertTrue(registry_changed(registry, reg))
        self.assertFalse(registry_changed(reg, list(reg)))

    def test_write_boards_yaml_keeps_filters(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Paths.sim(Path(tmp))
            p.ensure_layout()
            raw = {"filters": {"min_comp": 1}, "sim": {"x": 1}, "boards": []}
            out = write_boards_yaml(p, raw, [{"company": "Acme", "ats": "greenhouse", "slug": "acme", "tier": "1", "source": "discover", "confidence": 0.9, "secret": "no"}])
            text = out.read_text(encoding="utf-8")
            self.assertIn("min_comp: 1", text)
            self.assertIn("source: discover", text)
            self.assertNotIn("secret", text)


class ReviewTests(unittest.TestCase):
    def test_note_roundtrip_and_policies(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Paths.sim(Path(tmp))
            p.ensure_layout()
            note = p.vault_dir / "Market Intel" / "Discovery Review.md"
            items = [{"id": "board:zeta", "kind": "board", "text": "**Zeta** → ashby/zeta (0.62)"}, {"id": "term:field-engineering", "kind": "term", "text": "field engineering"}]
            n = write_review_note(p, note, items)
            self.assertEqual(n, 2)
            text = note.read_text(encoding="utf-8")
            self.assertIn("- [ ] `board:zeta`", text)
            self.assertIn("## Learned title terms", text)
            text = text.replace("- [ ] `board:zeta`", "- [x] `board:zeta`").replace("- [ ] `term:field-engineering` field engineering", "- [x] `term:field-engineering` field engineering REJECT")
            note.write_text(text, encoding="utf-8")
            self.assertEqual(read_review_answers(note), {"board:zeta": "accept", "term:field-engineering": "reject"})
            # rewriting with new pending keeps the answered lines
            write_review_note(p, note, [{"id": "board:omega", "kind": "board", "text": "Omega"}])
            again = read_review_answers(note)
            self.assertEqual(again["board:zeta"], "accept")
            self.assertIn("board:omega", note.read_text(encoding="utf-8"))
        auto = AutoPolicy("from_truth", {"board:zeta": "reject"})
        self.assertEqual(auto.answer({"id": "board:zeta"}), "reject")
        self.assertIsNone(auto.answer({"id": "board:x"}))
        self.assertEqual(AutoPolicy("accept").answer({"id": "y"}), "accept")


if __name__ == "__main__":
    unittest.main()
