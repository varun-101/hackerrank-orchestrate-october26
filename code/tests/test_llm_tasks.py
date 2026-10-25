"""Synthetic model outputs only: these fixtures are never real image extractions or approvals."""

import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import evidence
import intent
import llm
import llm_tasks as tasks
import main
import pipeline
import review
from test_llm import Server, Stream, terminal


def facts(resource="support_991", email="fictional@example.invalid"):
    return {"observations": [{"resource_id": resource, "system": "support_platform",
        "collection": "support_records", "copy": None, "observed_at": None,
        "fields": {"email": email, "full_name": "Synthetic Fixture"},
        "source": f"Synthetic fixture only: {resource} {email}"}], "uncertainties": []}


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cache = self.root / "raw.json"
        self.reviewed = self.root / "reviewed.md"
        self.requests = pipeline.read_requests()
        self.files = [f for f in evidence.load_manifest() if evidence.file_type(f) == "png"]
        self.images = {(evidence.DATASET / f["file_path"]).read_bytes(): f for f in self.files}
        self.mode = "normal"
        self.seen = []
        self.env = patch.dict(os.environ, {"ACCESS_TOKEN": "test-access-token", "LLM_MODEL": "gpt-6.1-sol"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def respond(self, body, number):
        content = body["input"][0]["content"]
        prompt = content[0]["text"]
        if len(content) == 1:
            text = json.loads(prompt.split("Request text (JSON string):\n")[1])
            decision = intent.classify(text)["decision"]
            if self.mode == "disagree":
                decision = "NOT_APPLICABLE" if decision == "DELETE_AUTHORIZED" else "DELETE_AUTHORIZED"
            value = {"decision": decision, "reason": "Synthetic stub, not a live model decision"}
        else:
            value = facts()
            if self.mode == "refresh_image":
                value["observations"][0]["fields"]["full_name"] = "Fresh synthetic fixture"
        event = terminal()
        event["response"]["output"][0]["content"][0]["text"] = json.dumps(value)
        return Stream([event], delay=.001)

    def runner(self, requests, **kwargs):
        self.seen = requests
        return llm.run_batch(requests, cache_path=self.cache, limits=llm.Limits(workers=8), **kwargs)

    def prepare(self, requests=None, **kwargs):
        real_open = open
        def checked(path, *args, **options):
            self.assertNotEqual(Path(path).name, "sample_output.csv")
            return real_open(path, *args, **options)
        with patch("builtins.open", checked):
            return tasks.prepare(self.requests if requests is None else requests,
                                 facts_path=self.reviewed, batch_runner=self.runner, **kwargs)

    def approve_synthetic(self, value=None):
        # Temporary synthetic approvals only; never write code/cache/reviewed_facts.md.
        entries = [{"file_id": f["file_id"], "sha256": hashlib.sha256(data).hexdigest(),
                    "approved": True, "facts": value or facts()} for data, f in self.images.items()]
        self.reviewed.write_text("# SYNTHETIC TEST ONLY\n```json\n" +
            json.dumps({"version": 1, "images": entries}) + "\n```\n", encoding="utf-8")

    def test_real_shapes_single_batch_dedup_review_gate_and_cache(self):
        server = Server(self.respond)
        with patch.object(llm, "_client", server.client), self.assertRaises(tasks.ReviewRequired) as caught:
            self.prepare()
        trace = caught.exception.trace
        unique = len({r["request_text"] for r in self.requests})
        self.assertEqual(trace["llm"]["request_count"], 550)
        self.assertEqual(trace["llm"]["unique_intents"], unique)
        self.assertEqual(len(server.posts), unique + 11)
        self.assertGreater(server.peak, 1)
        self.assertLessEqual(server.peak, 8)
        self.assertEqual(len(trace["cases"]), 550)
        self.assertEqual({i["kind"] for i in trace["llm"]["issues"]}, {"image_review"})
        tasks.write_diagnostics(trace, self.root)
        draft = tasks.read_reviewed(self.root / "screenshot_review_draft.md")
        self.assertEqual(len(draft), 11)
        self.assertTrue(all(not v["approved"] for v in draft.values()))
        self.assertTrue(all(r["decision_used"] is None for r in trace["llm"]["results"].values()
                            if r["task"] == "image_facts"))
        self.approve_synthetic()
        with patch.object(llm, "_client", side_effect=AssertionError("No network on cache hits")), \
             patch.dict(os.environ, {"ACCESS_TOKEN": ""}):
            context = self.prepare()
        self.assertEqual(context["trace"]["llm"]["usage_current"]["calls"], 0)
        history = context["trace"]["llm"]["usage_cached_historical_by_model"]["gpt-6.1-sol"]
        self.assertEqual(history["calls"], unique + 11)
        self.assertEqual(len(context["observations"]), 11)
        self.assertTrue(all(o["file_type"] == "png" for o in context["observations"]))
        # Synthetic image facts intentionally link nobody; deterministic decisions stay baseline.
        baseline, _ = pipeline.run()
        actual, integrated = pipeline.run(llm_context=context)
        self.assertEqual(actual, baseline)
        self.assertEqual(len(actual), 550)
        event = integrated["cases"][self.requests[0]["case_id"]][0]
        self.assertEqual(event["decided_by"], "LLM primary")
        self.assertIn("raw_response", event["llm"])
        context = copy.deepcopy(context)
        context["trace"]["cases"] = {}
        with patch.object(evidence, "REPO_ROOT", self.root), patch.object(tasks, "prepare", return_value=context):
            self.assertEqual(main.main(["--samples"]), 0)
        with (self.root / "output/sample_predictions.csv").open(encoding="utf-8") as handle:
            self.assertEqual(len(list(evidence.csv.DictReader(handle))), 32)
        self.assertEqual(len(json.loads((self.root / "output/trace.json").read_text(encoding="utf-8"))["cases"]), 550)
        self.assertFalse((self.root / "output.csv").exists())

    def test_reviewed_correction_precedes_raw_and_refresh_blocks_both_differences(self):
        self.approve_synthetic(facts(resource="support_992"))
        with patch.object(llm, "_client", Server(self.respond).client):
            context = self.prepare(self.requests[:1])
        self.assertEqual({o["resource_id"] for o in context["observations"]}, {"support_992"})
        image = next(r for r in context["trace"]["llm"]["results"].values() if r["task"] == "image_facts")
        self.assertEqual(image["parsed"]["observations"][0]["resource_id"], "support_991")
        self.assertEqual(image["decision_used"]["observations"][0]["resource_id"], "support_992")
        before = self.cache.read_bytes()
        self.mode = "refresh_image"
        with patch.object(llm, "_client", Server(self.respond).client), self.assertRaises(tasks.ReviewRequired) as caught:
            self.prepare(self.requests[:1], refresh=True)
        info = caught.exception.trace["llm"]
        self.assertTrue(info["differences"])
        self.assertIn("reviewed_facts_difference", {i["kind"] for i in info["issues"]})
        self.assertTrue(all(r["decision_used"]["observations"][0]["resource_id"] == "support_992"
            for r in info["results"].values() if r["task"] == "image_facts"))
        # Changed image entries remain intact; an unchanged intent can update its own attempts.
        old, new = json.loads(before)["entries"], json.loads(self.cache.read_bytes())["entries"]
        for key, result in info["results"].items():
            if result["task"] == "image_facts":
                self.assertEqual(new[key], old[key])

    def test_hash_change_blocks_stale_reviewed_facts(self):
        self.approve_synthetic()
        data = tasks.read_reviewed(self.reviewed)
        next(iter(data.values()))["sha256"] = "0" * 64
        self.reviewed.write_text("```json\n" + json.dumps({"version": 1, "images": list(data.values())}) + "\n```\n")
        with patch.object(llm, "_client", Server(self.respond).client), self.assertRaises(tasks.ReviewRequired) as caught:
            self.prepare(self.requests[:1])
        self.assertIn("stale_hash", {i.get("status") for i in caught.exception.trace["llm"]["issues"]})

    def test_disagreement_is_not_stored_as_per_request_override(self):
        self.approve_synthetic()
        self.mode = "disagree"
        with patch.object(llm, "_client", Server(self.respond).client), self.assertRaises(tasks.ReviewRequired) as caught:
            self.prepare(self.requests[:1])
        info = caught.exception.trace["llm"]
        self.assertIn("intent_review", {i["kind"] for i in info["issues"]})
        result = next(r for r in info["results"].values() if r["task"] == "intent")
        self.assertIsNone(result["decision_used"])
        self.assertTrue(result["keyword_disagreement"])

    def test_partial_errors_and_interruption_preserve_usage_and_preflight(self):
        for exception in (llm.BatchError, llm.BatchInterrupted):
            def fail(requests, **kwargs):
                result = llm._trace(requests[0])
                result["error"] = "network_or_timeout"
                result["attempts"] = [{"usage": None}]
                batch = llm.BatchResult(results={requests[0].key: result}, preflight=[{"error": "interrupted"}])
                if exception is llm.BatchInterrupted:
                    raise exception(batch)
                raise exception("Safe fixture failure", batch)
            with self.assertRaises(tasks.ReviewRequired) as caught:
                tasks.prepare(self.requests[:1], facts_path=self.reviewed, batch_runner=fail)
            info = caught.exception.trace["llm"]
            self.assertEqual(info["usage_current"]["unknown_usage_attempts"], 1)
            self.assertEqual(info["preflight"], [{"error": "interrupted"}])

    def test_cli_failure_preserves_successful_artifacts_and_never_reads_expected(self):
        directory = self.root / "output"
        directory.mkdir()
        for name in ("sample_predictions.csv", "sample_scores.json", "trace.json"):
            (directory / name).write_text("successful sentinel")
        real_open = open
        def checked(path, *args, **kwargs):
            self.assertNotEqual(Path(path).name, "sample_output.csv")
            return real_open(path, *args, **kwargs)
        with patch.object(evidence, "REPO_ROOT", self.root), \
             patch.object(tasks, "prepare", side_effect=tasks.ReviewRequired("safe stop",
                 {"cases": {}, "llm": {"images": {}, "results": {}, "issues": []}})), \
             patch.object(review, "write_llm_diagnostic") as renderer, patch("builtins.open", checked):
            self.assertEqual(main.main(["--samples"]), 2)
        renderer.assert_called_once()
        for name in ("sample_predictions.csv", "sample_scores.json", "trace.json"):
            self.assertEqual((directory / name).read_text(), "successful sentinel")

    def test_cache_persistence_failure_writes_diagnostics_preserving_artifacts(self):
        with patch.object(llm, "_client", Server(self.respond).client), self.assertRaises(tasks.ReviewRequired):
            self.prepare(self.requests[:1])
        accepted = self.cache.read_bytes()
        directory = self.root / "output"
        directory.mkdir()
        names = ("sample_predictions.csv", "sample_scores.json", "trace.json")
        for name in names:
            (directory / name).write_text("successful sentinel")
        original_prepare = tasks.prepare
        def prepare(requests, **kwargs):
            return original_prepare(self.requests[:1], facts_path=self.reviewed,
                                    batch_runner=self.runner, **kwargs)
        denied = PermissionError("synthetic contention")
        denied.winerror = 5
        with patch.object(evidence, "REPO_ROOT", self.root), \
             patch.object(tasks, "prepare", side_effect=prepare), \
             patch.object(llm, "_client", Server(self.respond).client), \
             patch.object(llm.os, "replace", side_effect=denied), patch.object(llm.time, "sleep"):
            self.assertEqual(main.main(["--samples", "--refresh-llm"]), 2)
        self.assertEqual(self.cache.read_bytes(), accepted)
        for name in names:
            self.assertEqual((directory / name).read_text(), "successful sentinel")
        trace = json.loads((directory / "llm_diagnostic_trace.json").read_text(encoding="utf-8"))
        self.assertIn("persistence failed", json.dumps(trace))
        self.assertGreater(trace["llm"]["usage_current"]["calls"], 0)
        self.assertTrue(any(r["parsed"] is not None and not r["cache_hit"]
                            for r in trace["llm"]["results"].values()))
        for name in ("llm_review.html", "llm_review.md", "screenshot_review_draft.md"):
            self.assertTrue((directory / name).is_file())
        self.assertFalse((self.root / "output.csv").exists())
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_facts_map_identifiers_times_and_copy_into_existing_parser(self):
        file = self.files[0]
        value = facts()
        value["observations"][0].update(copy="support_replica", observed_at="2026-09-01T00:00:00Z")
        obs = tasks.fact_observations(file, value)[0]
        self.assertEqual(obs["identifiers"]["emails"], ["fictional@example.invalid"])
        self.assertEqual(obs["copy"], "support_replica")
        self.assertEqual(obs["file_captured_at"], file["captured_at"])
        self.assertEqual(obs["source"], value["observations"][0]["source"])
        self.assertFalse(pipeline.eligible(dict(obs, file_captured_at="2027-01-01T00:00:00Z"), "2026-10-01T00:00:00Z"))

    def test_strict_parsers_reject_wrong_shapes_and_unsure_stops(self):
        for raw in ('{}', '{"decision":"COMPLETE","reason":"bad task"}',
                    '{"decision":"NOT_APPLICABLE","reason":""}'):
            with self.assertRaises(ValueError):
                tasks.parse_intent(raw)
        invalid = facts()
        invalid["observations"][0]["deletion_status"] = "COMPLETE"
        with self.assertRaises(ValueError):
            tasks.parse_facts(json.dumps(invalid))
        self.approve_synthetic()
        with patch.object(llm, "_client", Server(self.respond).client), \
             patch.object(intent, "classify", return_value={"decision": "UNSURE"}), \
             self.assertRaises(tasks.ReviewRequired) as caught:
            self.prepare(self.requests[:1])
        self.assertIn("intent_review", {i["kind"] for i in caught.exception.trace["llm"]["issues"]})


if __name__ == "__main__":
    unittest.main()
