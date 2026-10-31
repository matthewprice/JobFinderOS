import json
import tempfile
import unittest
from pathlib import Path

from jfgraph.paths import Paths
from jfgraph.transport import (DictTransport, RateLimited, RecordingTransport, SnapshotMiss, SnapshotTransport,
                               ThrottledTransport, TransportError, url_key)

U1 = "https://boards-api.greenhouse.io/v1/boards/acme/jobs"
U2 = "https://api.lever.co/v0/postings/acme?mode=json"
U3 = "https://api.ashbyhq.com/posting-api/job-board/nope"


class RecordReplayTests(unittest.TestCase):
    def test_recording_then_snapshot_roundtrip_including_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = Paths.sim(Path(tmp) / "scratch")
            rec_dir = paths.state_dir / "snap"
            inner = DictTransport({U1: {"jobs": [{"id": 1}]}, U2: "plain text", U3: RateLimited("HTTP 429", status=429, url=U3)})
            rec = RecordingTransport(inner, rec_dir, paths=paths, clock=lambda: "2026-10-31T00:00:00")
            self.assertEqual(rec.get_json(U1)["jobs"][0]["id"], 1)
            self.assertEqual(rec.get_text(U2), "plain text")
            with self.assertRaises(RateLimited):
                rec.get_json(U3)
            self.assertTrue((rec_dir / f"{url_key(U1)}.json").is_file())
            index = [json.loads(l) for l in (rec_dir / "index.jsonl").read_text().splitlines()]
            self.assertEqual({row["url"] for row in index}, {U1, U2, U3})

            snap = SnapshotTransport(rec_dir)
            self.assertEqual(snap.get_json(U1), {"jobs": [{"id": 1}]})
            self.assertEqual(snap.get_text(U2), "plain text")
            with self.assertRaises(RateLimited):
                snap.get_json(U3)
            with self.assertRaises(SnapshotMiss):
                snap.get_json("https://example.com/never-recorded")
            self.assertEqual(inner.calls, [U1, U2, U3])   # replay made no further inner calls

    def test_recording_refuses_to_write_outside_scratch(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = Paths.sim(Path(tmp) / "scratch")
            outside = Path(tmp) / "outside"
            rec = RecordingTransport(DictTransport({U1: {}}), outside, paths=paths)
            with self.assertRaises(Exception):
                rec.get_json(U1)
            self.assertFalse(outside.exists())


class ThrottleTests(unittest.TestCase):
    def test_min_gap_per_host_and_backoff_on_429(self):
        sleeps: list[float] = []
        clock = {"t": 0.0}

        def fake_sleep(s):
            sleeps.append(round(s, 3))
            clock["t"] += s

        class Flaky(DictTransport):
            def __init__(self):
                super().__init__({U1: {"ok": True}})
                self.n = 0

            def get_json(self, url, *, headers=None, timeout=25):
                self.n += 1
                if self.n < 3:
                    raise RateLimited("HTTP 429", status=429, url=url)
                return super().get_json(url)

        t = ThrottledTransport(Flaky(), min_gap_s=0.5, retries=3, backoff_s=1.0, sleep=fake_sleep, clock=lambda: clock["t"])
        self.assertEqual(t.get_json(U1), {"ok": True})
        # two backoffs (1s, 2s) plus gap waits between the retries on the same host
        self.assertIn(1.0, sleeps)
        self.assertIn(2.0, sleeps)
        self.assertEqual(t.attempts[-1], (U1, 3))

    def test_non_retryable_error_raises_immediately(self):
        t = ThrottledTransport(DictTransport({}), min_gap_s=0, sleep=lambda s: None)
        with self.assertRaises(TransportError) as cm:
            t.get_json(U1)
        self.assertEqual(cm.exception.status, 404)
        self.assertEqual(t.attempts, [(U1, 1)])


if __name__ == "__main__":
    unittest.main()
