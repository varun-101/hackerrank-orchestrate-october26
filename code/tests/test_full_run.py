"""--full mode: root output.csv publication and run usage. No network; the LLM step is stubbed."""

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import evidence
import llm_tasks
import main
import output
import pipeline


def attempt(input_tokens, output_tokens, error=None, usage=True):
    reported = {"input_tokens": input_tokens, "output_tokens": output_tokens, "reasoning_tokens": 0,
                "cached_tokens": 0, "total_tokens": input_tokens + output_tokens}
    return {"usage": reported if usage else None, "error": error, "response_model": "gpt-6.1-sol"}


def llm_result(task, cache_hit, attempts=(), historical=()):
    return {"task": task, "model": "gpt-6.1-sol", "cache_hit": cache_hit, "error": None,
            "attempts": list(attempts), "historical_attempts": list(historical)}


class WriteSubmissionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        patcher = patch.object(evidence, "REPO_ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.step = output.make_step("VERIFY_RECORD_ABSENT", "account_service", "account_1", "evidence_01")

    def test_valid_rows_are_written_sorted_without_leftovers(self):
        results = {"case_34": ("COMPLETE", [self.step]), "case_33": ("NOT_APPLICABLE", [])}
        self.assertEqual(main.write_submission(results, ["case_34", "case_33"]), [])
        rows = output.read_rows(self.root / "output.csv")
        self.assertEqual([r["case_id"] for r in rows], ["case_33", "case_34"])
        self.assertEqual(rows[0]["steps_json"], "[]")
        self.assertEqual(list(self.root.iterdir()), [self.root / "output.csv"])

    def test_invalid_rows_leave_no_output_and_keep_an_earlier_one(self):
        results = {"case_33": ("INCOMPLETE", [])}
        errors = main.write_submission(results, ["case_33"])
        self.assertTrue(any("INCOMPLETE must have at least one step" in e for e in errors))
        self.assertEqual(list(self.root.iterdir()), [])
        (self.root / "output.csv").write_text("earlier valid output", encoding="utf-8")
        self.assertTrue(main.write_submission(results, ["case_33"]))
        self.assertEqual((self.root / "output.csv").read_text(encoding="utf-8"), "earlier valid output")
        self.assertEqual(list(self.root.iterdir()), [self.root / "output.csv"])


class UsageSummaryTests(unittest.TestCase):
    def test_cached_run_has_no_live_attempts_and_reports_cache_built_totals_apart(self):
        info = {"results": {"a": llm_result("intent", True, historical=[attempt(100, 10)]),
                            "b": llm_result("image_facts", True, historical=[attempt(1000, 200)])}}
        usage = main.usage_summary(info, False, 10, 3.14159, 0.5)
        self.assertEqual(usage["mode"], "cached")
        self.assertEqual((usage["cache_hits"], usage["live_requests"]), (2, 0))
        self.assertEqual(usage["unique_llm_requests"], {"total": 2, "image_facts": 1, "intent": 1})
        self.assertEqual(usage["this_run"]["total"]["attempts"], 0)
        self.assertEqual(usage["this_run"]["total"]["total_tokens"], 0)
        built = usage["cache_built_not_this_run"]
        self.assertEqual((built["total"]["input_tokens"], built["total"]["output_tokens"]), (1100, 210))
        self.assertEqual(built["per_evaluation_request"]["input_tokens"], 110.0)
        self.assertEqual(built["by_task"]["intent"]["total_tokens"], 110)
        self.assertEqual(built["response_models_seen"], ["gpt-6.1-sol"])
        self.assertEqual(usage["wall_clock_seconds"], 3.14)
        self.assertIsNone(usage["estimated_cost_usd"])

    def test_cold_run_counts_retries_and_unknown_usage(self):
        retried = [attempt(100, 0, error="network_or_timeout", usage=False), attempt(100, 10)]
        info = {"results": {"a": llm_result("intent", False, attempts=retried)}}
        usage = main.usage_summary(info, True, 4, 1, 1)
        total = usage["this_run"]["total"]
        self.assertEqual(usage["mode"], "cold")
        self.assertEqual((usage["live_requests"], usage["cache_hits"]), (1, 0))
        self.assertEqual((total["attempts"], total["failed_attempts"], total["unknown_usage_attempts"]), (2, 1, 1))
        self.assertEqual((total["input_tokens"], total["output_tokens"], total["total_tokens"]), (100, 10, 110))
        self.assertEqual(usage["this_run"]["per_evaluation_request"]["attempts"], 0.5)
        self.assertEqual(usage["cache_built_not_this_run"]["total"]["attempts"], 0)


class FullCommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results, cls.trace = pipeline.run()  # keyword-only core; the LLM step is stubbed below
        cls.case_ids = [r["case_id"] for r in output.read_rows(evidence.DATASET / "deletion_requests.csv")]

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        info = {"results": {"a": llm_result("intent", True, historical=[attempt(100, 10)])}}
        self.context = {"trace": {"llm": info}}

    def run_full(self, results):
        with patch.object(evidence, "REPO_ROOT", self.root), \
             patch.object(llm_tasks, "prepare", return_value=self.context), \
             patch.object(pipeline, "run", return_value=(results, self.trace)), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            code = main.main(["--full"])
        return code, out.getvalue()

    def test_full_writes_every_evaluation_row_in_order_and_usage(self):
        code, printed = self.run_full(self.results)
        self.assertEqual(code, 0)
        rows = output.read_rows(self.root / "output.csv")
        self.assertEqual(len(rows), 518)
        self.assertEqual([r["case_id"] for r in rows], self.case_ids)
        self.assertEqual(output.validate_output(self.root / "output.csv", self.case_ids), [])
        self.assertIn("wrote 518 rows", printed)
        usage = json.loads((self.root / "output/usage.json").read_text(encoding="utf-8"))
        self.assertEqual(usage["evaluation_requests"], 518)
        self.assertEqual(usage["this_run"]["total"]["attempts"], 0)
        self.assertEqual(usage["cache_built_not_this_run"]["total"]["total_tokens"], 110)
        for name in ("sample_predictions.csv", "sample_scores.json", "trace.json"):
            self.assertTrue((self.root / "output" / name).is_file())
        self.assertEqual([p.name for p in self.root.glob("*.tmp")], [])

    def test_validation_failure_exits_nonzero_without_output_csv(self):
        broken = dict(self.results, **{self.case_ids[0]: ("INCOMPLETE", [])})
        code, printed = self.run_full(broken)
        self.assertEqual(code, 1)
        self.assertIn("output.csv was not written", printed)
        self.assertFalse((self.root / "output.csv").exists())
        self.assertFalse((self.root / "output/usage.json").exists())

    def test_samples_mode_never_writes_root_output(self):
        with patch.object(evidence, "REPO_ROOT", self.root), \
             patch.object(llm_tasks, "prepare", return_value=self.context), \
             patch.object(pipeline, "run", return_value=(self.results, self.trace)), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main.main(["--samples"]), 0)
        self.assertFalse((self.root / "output.csv").exists())
        self.assertFalse((self.root / "output/usage.json").exists())

    def test_mode_is_required_exclusive_and_full_needs_the_llm_path(self):
        for argv in ([], ["--samples", "--full"], ["--full", "--keyword-only-diagnostic"]):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                main.main(argv)


if __name__ == "__main__":
    unittest.main()
