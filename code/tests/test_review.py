"""Focused standalone review checks; integration uses the saved --samples artifacts."""

import base64
from html.parser import HTMLParser
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import review


class EmbeddedData(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.scripts = []
        self.payload = ""
        self.in_payload = False
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            attrs = dict(attrs)
            self.scripts.append(attrs)
            self.in_payload = attrs.get("id") == "review-data"

    def handle_endtag(self, tag):
        if tag == "script":
            self.in_payload = False

    def handle_data(self, data):
        if self.in_payload:
            self.payload += data


class EscapingTests(unittest.TestCase):
    def test_untrusted_text_cannot_end_script_and_round_trips_exactly(self):
        text = '</script><script>alert("injected")</script><img src=x onerror=alert(1)>&\u2028\u2029 é 中文'
        value = {"request": text, "source": "line 1\n" + text, "nested": [None, "&lt;"]}
        parsed = EmbeddedData(review.render(value))
        self.assertEqual(len(parsed.scripts), 2)
        self.assertFalse(any("src" in s for s in parsed.scripts))
        self.assertNotIn("<", parsed.payload)
        self.assertEqual(json.loads(parsed.payload), value)
        self.assertNotIn("innerHTML", review.PAGE)
        self.assertNotIn("document.write", review.PAGE)


@unittest.skipUnless((review.ROOT / "output/trace.json").exists(), "Run python code/main.py --samples first")
class ReviewIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = review.build_data()
        cls.trace = json.loads((review.ROOT / "output/trace.json").read_text(encoding="utf-8"))

    def test_actual_sample_metrics_and_all_trace_outputs_preserved(self):
        data = self.data
        self.assertEqual(len(data["cases"]), 550)
        self.assertEqual(sum(c["kind"] == "sample" for c in data["cases"].values()), 32)
        self.assertEqual(data["scores"], json.loads((review.ROOT / "output/sample_scores.json").read_text()))
        self.assertEqual(data["warnings"], [])
        for cid, case in data["cases"].items():
            final = next(e["outputs"] for e in self.trace["cases"][cid] if e["stage"] == "steps")
            self.assertEqual(case["produced"], (final["status"], final["steps"]))
            self.assertEqual(case["warnings"], [])
            if case["kind"] == "evaluation":
                self.assertIsNone(case["expected"])
        errors = [e for c in data["cases"].values() for e in c["validation_errors"]]
        self.assertCountEqual(errors, data["scores"]["validation_errors"])

    def test_evidence_refs_are_deduplicated_without_losing_trace_source(self):
        count = 0
        ids = set()
        for cid, original in self.trace["cases"].items():
            recorded = self.data["cases"][cid]["events"]
            for old, new in zip(original, recorded):
                self.assertEqual({k:v for k,v in old.items() if k != "evidence"},
                                 {k:v for k,v in new.items() if k != "evidence"})
                for ref, key in zip(old["evidence"], new["evidence"]):
                    self.assertEqual(ref["source"], self.data["sources"][key]["source"])
                    ids.add(key)
                    count += 1
        self.assertGreater(count, len(ids))
        for case in self.data["cases"].values():
            self.assertTrue(set(case["sources"]) <= self.data["sources"].keys())

    def test_cutoff_exclusions_and_identity_history_have_source_metadata(self):
        case = self.data["cases"]["case_26"]
        self.assertIn("evidence_02#16", case["excluded"])
        obs = self.data["sources"]["evidence_02#16"]
        self.assertEqual(obs["file_id"], "evidence_02")
        self.assertGreater(self.data["files"][obs["file_id"]]["captured_at"], case["request"]["audit_at"])
        self.assertTrue(obs["source"])
        case = self.data["cases"]["case_550"]
        self.assertIn("audit_30", case["history"])
        self.assertEqual(self.data["sources"]["audit_30"]["row"]["field_name"], "phone")

    def test_all_original_files_and_screenshot_bytes_embedded_once(self):
        manifest = review.rows(review.ROOT / "dataset/evidence_files.csv")
        self.assertEqual(set(self.data["files"]), {r["file_id"] for r in manifest})
        image_count = 0
        for row in manifest:
            file = self.data["files"][row["file_id"]]
            path = review.ROOT / "dataset" / row["file_path"]
            if "data_url" in file:
                self.assertEqual(base64.b64decode(file["data_url"].split(",")[1]), path.read_bytes())
                image_count += path.suffix == ".png"
            else:
                self.assertEqual(file["text"], path.read_text(encoding="utf-8"))
        self.assertEqual(image_count, 11)
        html = review.render(self.data)
        parsed = EmbeddedData(html)
        self.assertEqual(json.loads(parsed.payload)["files"], self.data["files"])
        self.assertIn("Keyword-only diagnostic", html)


if __name__ == "__main__":
    unittest.main()
