import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from capnet.gaps import log_gap, summarize


class TestGaps(unittest.TestCase):
    def test_log_and_summarize(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "gaps.jsonl")
            log_gap(path, "全息投影", None, "no_match")
            log_gap(path, "全息投影", None, "no_match")
            log_gap(path, "气味合成", "misc", "no_match")
            rows = summarize(path)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["query"], "全息投影")
            self.assertEqual(rows[0]["count"], 2)
            self.assertEqual(rows[1]["count"], 1)
            with open(path, encoding="utf-8") as f:
                lines = [json.loads(x) for x in f if x.strip()]
            self.assertEqual(len(lines), 3)
            self.assertIn("t", lines[0])

    def test_empty_file(self):
        self.assertEqual(summarize("/nonexistent/gaps.jsonl"), [])


if __name__ == "__main__":
    unittest.main()
