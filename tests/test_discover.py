import unittest

from jfgraph.ats_adapters import BOARD_URLS
from jfgraph.discover import GREENHOUSE_BOARD_URL, discover_board, name_match, slug_candidates
from jfgraph.transport import DictTransport, RateLimited, SnapshotMiss

CFG = {"ats": ["greenhouse", "ashby", "lever"], "max_slugs": 6, "auto_enroll_min": 0.8, "ambiguous_min": 0.5}


def gh(slug):
    return BOARD_URLS["greenhouse"].format(slug=slug)


def ashby(slug):
    return BOARD_URLS["ashby"].format(slug=slug)


class SlugTests(unittest.TestCase):
    def test_candidates(self):
        self.assertEqual(slug_candidates("Acme Labs, Inc.")[:3], ["acmelabs", "acme-labs", "acme"])
        self.assertEqual(slug_candidates("Stripe")[0], "stripe")
        self.assertIn("scaleai", slug_candidates("Scale AI"))
        self.assertIn("scale", slug_candidates("Scale AI"))
        self.assertEqual(slug_candidates("Zed", {"Zed": ["zed-industries"]})[0], "zed-industries")
        self.assertLessEqual(len(slug_candidates("One Two Three Four Holdings LLC")), 6)

    def test_name_match(self):
        self.assertEqual(name_match("Vercel", "vercel", "Vercel"), 1.0)
        self.assertLess(name_match("Vercel", "vercel", "Verily Life Sciences"), 0.5)
        self.assertGreaterEqual(name_match("Notion", "notion", None, "Notion is a workspace"), 0.9)
        self.assertEqual(name_match("Scale AI", "scaleai", None), 0.85)
        self.assertLess(name_match("Linear", "line", None), 0.8)


class DiscoverTests(unittest.TestCase):
    def test_found_via_greenhouse_name_hint(self):
        t = DictTransport({gh("vercel"): {"jobs": [{"title": "Head of SA"}, {"title": "SE"}, {"title": "PM"}]}, GREENHOUSE_BOARD_URL.format(slug="vercel"): {"name": "Vercel"}})
        r = discover_board("Vercel", t, CFG)
        self.assertEqual((r["status"], r["ats"], r["slug"]), ("found", "greenhouse", "vercel"))
        self.assertEqual(r["confidence"], 1.0)
        self.assertEqual(len(r["probes"]), 1)            # stopped after the first confident hit

    def test_two_boards_one_with_a_mismatched_name_hint_is_ambiguous(self):
        t = DictTransport({gh("linear"): {"jobs": [{"title": f"Job {i}"} for i in range(5)]}, GREENHOUSE_BOARD_URL.format(slug="linear"): {"name": "Linear Motion Systems"},
                           ashby("linear"): {"jobs": [{"title": "PM", "descriptionPlain": "Linear is the issue tracker"}]}})
        r = discover_board("Linear", t, CFG)
        self.assertEqual(r["status"], "ambiguous")       # two live, plausible boards: never auto-enroll, ask the human
        self.assertEqual(r["ats"], "ashby")              # the hint-less Ashby hit with a text mention ranks first
        self.assertEqual(len(r["probes"]), 3)

    def test_abandoned_board_with_exact_name_does_not_win(self):
        # the Ramp case: greenhouse/ramp is a stale board named "Ramp" with one posting; the real board is on Ashby
        t = DictTransport({gh("ramp"): {"jobs": [{"title": "Copy of Chief Legal Officer"}]}, GREENHOUSE_BOARD_URL.format(slug="ramp"): {"name": "Ramp"},
                           ashby("ramp"): {"jobs": [{"title": f"Role {i}", "descriptionPlain": "Ramp is the finance platform"} for i in range(20)]}})
        r = discover_board("Ramp", t, CFG)
        self.assertEqual((r["status"], r["ats"]), ("found", "ashby"))
        # alone, a tiny board is ambiguous rather than found or lost
        alone = DictTransport({gh("ramp"): {"jobs": [{"title": "x"}]}, GREENHOUSE_BOARD_URL.format(slug="ramp"): {"name": "Ramp"}})
        self.assertEqual(discover_board("Ramp", alone, CFG)["status"], "ambiguous")

    def test_name_hint_only_board_is_found_when_alone(self):
        t = DictTransport({ashby("notion"): {"jobs": [{"title": f"Role {i}", "descriptionPlain": "Notion is a connected workspace"} for i in range(4)]}})
        r = discover_board("Notion", t, CFG)
        self.assertEqual((r["status"], r["ats"]), ("found", "ashby"))

    def test_two_plausible_boards_are_ambiguous(self):
        t = DictTransport({gh("harvey"): {"jobs": [{"title": "x"}]}, GREENHOUSE_BOARD_URL.format(slug="harvey"): {"name": "Harvey Nash"},
                           ashby("harvey"): {"jobs": [{"title": "y", "descriptionPlain": "Harvey builds legal AI"}]}})
        r = discover_board("Harvey", t, CFG)
        self.assertIn(r["status"], ("ambiguous", "found"))
        if r["status"] == "found":
            self.assertEqual(r["ats"], "ashby")

    def test_not_found_rate_limited_and_unprobed(self):
        self.assertEqual(discover_board("Nobody Corp", DictTransport({}), CFG)["status"], "not_found")
        rl = DictTransport({gh("brex"): RateLimited("HTTP 429", status=429, url=gh("brex"))})
        self.assertEqual(discover_board("Brex", rl, CFG)["status"], "rate_limited")

        class Snap(DictTransport):
            def _get(self, url):
                raise SnapshotMiss("no snapshot", url=url)
        self.assertEqual(discover_board("Brex", Snap({}), CFG)["status"], "unprobed")


if __name__ == "__main__":
    unittest.main()
