import unittest
from pathlib import Path

import yaml

from jfgraph.config import Filters, load_yaml
from jfgraph.gates import classify_location, parse_comp, passes_leadership, passes_seniority, matches_function

DATA = Path(__file__).resolve().parent / "data"
GOLDEN = yaml.safe_load((DATA / "gates_golden.yaml").read_text(encoding="utf-8"))
FILTERS = Filters.from_dict(load_yaml(DATA / "golden" / "boards.yaml")["filters"])


class GatesGoldenTests(unittest.TestCase):
    """Every case was produced by the original jobfinderos_ats_poll.py functions."""

    def test_title_gate_matches_original(self):
        for row in GOLDEN["titles"]:
            with self.subTest(title=row["title"]):
                self.assertEqual(passes_leadership(row["title"], FILTERS), (row["ok"], row["lane"]))

    def test_location_gate_matches_original(self):
        for row in GOLDEN["locations"]:
            with self.subTest(location=row["location"], tier=row["tier"]):
                self.assertEqual(classify_location(row["location"], row["tier"], FILTERS), (row["cls"], row["reason"]))

    def test_comp_gate_matches_original(self):
        for row in GOLDEN["comps"]:
            with self.subTest(text=row["text"]):
                self.assertEqual(parse_comp(row["text"], FILTERS.min_comp), (row["status"], row["range"]))


class SplitGateTests(unittest.TestCase):
    def test_near_miss_is_seniority_pass_function_fail(self):
        ok, reason = passes_seniority("Director of Marketing", FILTERS)
        self.assertTrue(ok)
        self.assertFalse(matches_function("Director of Marketing", FILTERS))
        self.assertEqual(passes_seniority("Senior Solutions Engineer", FILTERS), (False, "no seniority token"))
        self.assertEqual(passes_seniority("Principal Solutions Engineer, Manager Track", FILTERS), (False, "ic override"))
        self.assertEqual(passes_seniority("", FILTERS), (False, "empty title"))

    def test_learned_terms_widen_function_gate(self):
        self.assertFalse(matches_function("Director, Solutions Architecture", FILTERS))
        learned = FILTERS.with_learned(["solutions architecture"])
        self.assertTrue(matches_function("Director, Solutions Architecture", learned))
        self.assertTrue(passes_leadership("Director, Solutions Architecture", learned)[0])
        self.assertEqual(FILTERS.extra_function_terms, ())      # original untouched

    def test_remote_us_part_beside_an_international_part(self):
        # measured miss: a GitLab posting listed "Remote, Canada; Remote, US" and was dropped as international
        self.assertEqual(classify_location("Remote, Canada; Remote, US", "2", FILTERS)[0], "clean")
        self.assertEqual(classify_location("US-Remote, US-Chicago", "2", FILTERS)[0], "hub_flag")   # hub named, remote word present
        self.assertEqual(classify_location("Toronto, Canada (Remote)", "1", FILTERS)[0], "drop")     # unchanged: no US part
        self.assertEqual(classify_location("Remote (EMEA)", "2", FILTERS)[0], "drop")

    def test_filters_defaults_and_ints(self):
        f = Filters.from_dict({"min_comp": "150000", "unknown_key": 1})
        self.assertEqual(f.min_comp, 150000)
        self.assertIsNone(f.function_re)
        self.assertTrue(matches_function("anything", f))
        self.assertEqual(parse_comp("$100,000 - $120,000", 0), ("clear", "$100,000-$120,000 (mid $110,000)"))


if __name__ == "__main__":
    unittest.main()
