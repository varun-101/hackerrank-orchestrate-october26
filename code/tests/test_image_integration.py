"""Offline regression transcriptions of the four PNG shapes in the combined review.

Temporary approvals below exercise the gate; they do not approve production images.
No expected-output files or live model calls are used.
"""

import copy
import hashlib
import json
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


def facts(resource, fields, source, system=None, observed=None):
    return {"observations": [{"resource_id": resource, "system": system, "collection": None,
        "copy": None, "observed_at": observed, "fields": fields, "source": source}], "uncertainties": []}


class ImageIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "reviewed.md"
        self.manifest = evidence.load_manifest()
        self.files = {f["file_id"]: f for f in self.manifest if evidence.file_type(f) == "png"}
        self.requests = [r for r in pipeline.read_requests() if r["case_id"] in {
            "case_34", "case_35", "case_39", "case_55"}]
        self.fixtures = {
            "evidence_04": facts("account_5762", {
                "user_id": "user_11", "full_name": "Haruka Sato", "email": "haruka.s1838@zoho.com"},
                "Haruka Sato | user_11 | account_5762 | haruka.s1838@zoho.com"),
            "evidence_09": facts("search_428", {
                "customer_reference": "user_13", "display_name": "Gabriel Silva", "email": "g.silva76@gmail.com"},
                "search_428 | Gabriel Silva | g.silva76@gmail.com | user_13", observed="2026-09-01T05:58:21Z"),
            "evidence_14": facts("data_flow_1121", {
                "selector": "jain.aditi@gmail.com", "sync_mode": "Continuous sync",
                "status": "Healthy", "events_per_hour": 1284},
                "data_flow_1121 | jain.aditi@gmail.com | Continuous sync | Healthy | 1,284"),
            "evidence_32": facts("backup_9287", {"account": "areed1433@gmail.com", "region": "eu-west-1",
                "encryption": "AES-256", "checksum": "Verified"},
                "backup_9287 | areed1433@gmail.com | eu-west-1 | AES-256 | Verified"),
        }
        self.digests = {fid: hashlib.sha256((evidence.DATASET / f["file_path"]).read_bytes()).hexdigest()
                        for fid, f in self.files.items()}

    def write_reviewed(self):
        self.by_hash = {digest: copy.deepcopy(self.fixtures.get(fid, facts("support_999999",
            {"email": "fixture@example.invalid"}, "Synthetic unrelated fixture", system="support_platform")))
            for fid, digest in self.digests.items()}
        entries = [{"file_id": fid, "sha256": digest, "approved": True, "facts": self.by_hash[digest]}
                   for fid, digest in self.digests.items()]
        self.path.write_text("# SYNTHETIC REGRESSION ONLY\n```json\n" +
            json.dumps({"version": 1, "images": entries}) + "\n```\n", encoding="utf-8")

    def runner(self, requests, **kwargs):
        batch = llm.BatchResult()
        for req in requests:
            if req.image:
                value = copy.deepcopy(self.by_hash[hashlib.sha256(req.image).hexdigest()])
            else:
                text = json.loads(req.prompt.split("Request text (JSON string):\n")[1])
                value = {"decision": intent.classify(text)["decision"], "reason": "Synthetic offline fixture"}
            raw = json.dumps(value)
            result = llm._trace(req)
            result.update(cache_hit=True, raw_response=raw, parsed=req.parse(raw), decision_used=value)
            batch.results[req.key] = result
        return batch

    def prepare(self, requests=None):
        self.write_reviewed()
        return tasks.prepare(self.requests if requests is None else requests,
                             facts_path=self.path, batch_runner=self.runner)

    def test_actual_account_search_and_continuous_connector_produce_canonical_steps(self):
        requests = [r for r in self.requests if r["case_id"] != "case_55"]
        for visible in (False, True):
            if visible:
                for fid, title in (("evidence_04", "Customer Operations"),
                                   ("evidence_09", "Search Console"), ("evidence_14", "BridgeOps")):
                    self.fixtures[fid]["observations"][0]["system"] = title
            context = self.prepare(requests)
            results, trace = pipeline.run(requests, llm_context=context)
            for cid, rid, system, finding, fix, check, fid in (
                ("case_34", "account_5762", "account_service", "REPORT_REMAINING_RECORD",
                 "DELETE_RECORD", "VERIFY_RECORD_ABSENT", "evidence_04"),
                ("case_35", "search_428", "search_service", "REPORT_REMAINING_RECORD",
                 "DELETE_RECORD", "VERIFY_RECORD_ABSENT", "evidence_09"),
                ("case_39", "data_flow_1121", "integration_service", "REPORT_ACTIVE_DATA_FLOW",
                 "DISABLE_DATA_FLOW", "VERIFY_DATA_FLOW_DISABLED", "evidence_14")):
                status, steps = results[cid]
                self.assertEqual(status, "INCOMPLETE")
                selected = [s for s in steps if s["resource_id"] == rid]
                self.assertEqual([s["operation"] for s in selected], [finding, fix, check])
                self.assertEqual({s["system_id"] for s in selected}, {system})
                self.assertEqual([s["evidence_id"] for s in selected], [fid, "", ""])
            image = trace["llm"]["images"]["evidence_14"]
            normalized = image["normalized_observations"][0]
            self.assertTrue(normalized["fields"]["enabled"])
            self.assertEqual(normalized["raw_system"], "BridgeOps" if visible else "integration_admin")
            self.assertEqual(normalized["image_normalization"]["origin"], "integration_admin")
            self.assertEqual(normalized["source"], self.fixtures["evidence_14"]["observations"][0]["source"])
            self.assertNotIn("enabled", image["decision_used"]["observations"][0]["fields"])
            raw = trace["llm"]["results"][image["key"]]["parsed"]
            self.assertNotIn("enabled", raw["observations"][0]["fields"])

    def test_backup_without_restore_state_is_a_known_gap_not_a_blocker(self):
        # User decision: no restore state is inferred, and publication is not blocked.
        context = self.prepare()
        self.assertEqual(context["trace"]["llm"]["issues"], [])
        requests = [r for r in self.requests if r["case_id"] == "case_55"]
        results, trace = pipeline.run(requests, llm_context=context)
        self.assertEqual(results["case_55"][1], [])  # no step is emitted for the backup
        steps = next(e["outputs"] for e in trace["cases"]["case_55"] if e["stage"] == "steps")
        self.assertTrue(any("backup_9287: no usable state for rule action RETAIN_BACKUP" in g
                            for g in steps["gaps"]))
        image = trace["llm"]["images"]["evidence_32"]
        self.assertNotIn("restore_allowed", image["normalized_observations"][0]["fields"])

    def test_blocking_gate_preserves_successful_cli_artifacts(self):
        self.fixtures["evidence_14"]["observations"][0]["fields"].pop("events_per_hour")
        self.write_reviewed()
        directory = self.root / "output"
        directory.mkdir()
        names = ("sample_predictions.csv", "sample_scores.json", "trace.json")
        for name in names:
            (directory / name).write_text("earlier successful artifact", encoding="utf-8")
        original_prepare = tasks.prepare
        def prepare(requests, **kwargs):
            return original_prepare(requests, facts_path=self.path, batch_runner=self.runner, **kwargs)
        original_open = open
        def checked(path, *args, **kwargs):
            self.assertNotEqual(Path(path).name, "sample_output.csv")
            return original_open(path, *args, **kwargs)
        with patch.object(evidence, "REPO_ROOT", self.root), patch.object(pipeline, "read_requests", return_value=self.requests), \
             patch.object(tasks, "prepare", side_effect=prepare), patch("builtins.open", checked):
            self.assertEqual(main.main(["--samples"]), 2)
        for name in names:
            self.assertEqual((directory / name).read_text(encoding="utf-8"), "earlier successful artifact")
        trace = json.loads((directory / "llm_diagnostic_trace.json").read_text(encoding="utf-8"))
        self.assertEqual(trace["llm"]["issues"][0]["resource_id"], "data_flow_1121")
        self.assertTrue((directory / "llm_review.html").exists())
        self.assertFalse((self.root / "output.csv").exists())

    def test_image_gate_respects_both_cutoffs_and_does_not_block_unrelated_subjects(self):
        self.fixtures["evidence_14"]["observations"][0]["fields"].pop("events_per_hour")
        request = [r for r in self.requests if r["case_id"] == "case_39"]
        with self.assertRaises(tasks.ReviewRequired):  # control: eligible and unresolved blocks
            self.prepare(request)
        for dimension in ("capture", "record"):
            with self.subTest(dimension=dimension):
                manifest = copy.deepcopy(self.manifest)
                if dimension == "capture":
                    next(f for f in manifest if f["file_id"] == "evidence_14")["captured_at"] = "2027-01-01T00:00:00Z"
                else:
                    self.fixtures["evidence_14"]["observations"][0]["observed_at"] = "2027-01-01T00:00:00Z"
                with patch.object(evidence, "load_manifest", return_value=manifest):
                    context = self.prepare(request)
                self.assertEqual(context["trace"]["llm"]["issues"], [])
                self.fixtures["evidence_14"]["observations"][0]["observed_at"] = None
        # The same unresolved flow is irrelevant to the other actual subjects.
        context = self.prepare([r for r in self.requests if r["case_id"] != "case_39"])
        self.assertEqual(context["trace"]["llm"]["issues"], [])
        account = [r for r in self.requests if r["case_id"] == "case_34"]
        for dimension in ("file_captured_at", "observed_at"):
            late = copy.deepcopy(context)
            late["trace"]["cases"] = {}
            for obs in late["observations"]:
                if obs["file_id"] == "evidence_04":
                    obs[dimension] = "2027-01-01T00:00:00Z"
            _, trace = pipeline.run(account, llm_context=late)
            linking = next(e["outputs"] for e in trace["cases"]["case_34"] if e["stage"] == "linking")
            self.assertNotIn("evidence_04#1", [link["source"] for link in linking["links"]])

    def test_unresolved_service_and_uninterpretable_connector_remain_reviewable(self):
        # Removing the one supported factual flow state must not manufacture a disabled flag.
        self.fixtures["evidence_14"]["observations"][0]["fields"].pop("events_per_hour")
        with self.assertRaises(tasks.ReviewRequired) as caught:
            self.prepare([r for r in self.requests if r["case_id"] == "case_39"])
        issue = caught.exception.trace["llm"]["issues"][0]
        self.assertEqual(issue["resource_id"], "data_flow_1121")
        self.assertIn("flow activity/disabled state not established", issue["missing"])
        manifest = copy.deepcopy(self.manifest)
        next(f for f in manifest if f["file_id"] == "evidence_09")["origin"] = "unknown UI"
        self.fixtures["evidence_09"]["observations"][0]["system"] = "unknown service"
        with patch.object(evidence, "load_manifest", return_value=manifest), self.assertRaises(tasks.ReviewRequired) as caught:
            self.prepare([r for r in self.requests if r["case_id"] == "case_35"])
        issue = caught.exception.trace["llm"]["issues"][0]
        self.assertIn("canonical service unresolved", issue["missing"][0])

    def test_screenshot_names_become_known_resource_ids_and_keep_the_raw_name(self):
        self.fixtures["evidence_19"] = {"observations": [
            {"resource_id": "user_20", "system": "Pulse Analytics", "collection": None, "copy": None,
             "observed_at": None, "source": "Omar Darwish | user_20 | analytics_7438",
             "fields": {"full_name": "Omar Darwish", "user_id": "user_20", "analytics_id": "analytics_7438",
                        "email": "darwish.omar@icloud.com"}},
            {"resource_id": "user_20", "system": "Pulse Analytics", "collection": None, "copy": None,
             "observed_at": None, "source": "Order completed", "fields": {"event": "Order completed"}}],
            "uncertainties": []}
        self.fixtures["evidence_46"] = facts("delivery_confirmation_19.pdf", {
            "path": "eu-west-1 / support-attachments / 2026 / 02", "content_type": "application/pdf"},
            "delivery_confirmation_19.pdf | 684 KB", system="Nimbus Object Storage")
        requests = [r for r in pipeline.read_requests() if r["case_id"] in {"case_50", "case_61"}]
        context = self.prepare(requests)
        self.assertEqual(context["trace"]["llm"]["issues"], [])
        profiles = [o for o in context["observations"] if o["file_id"] == "evidence_19"]
        self.assertEqual(len(profiles), 1)  # the event row is part of the same resource
        self.assertEqual((profiles[0]["resource_id"], profiles[0]["raw_resource_id"]), ("analytics_7438", "user_20"))
        self.assertIn("Order completed", profiles[0]["source"])
        results, _ = pipeline.run(requests, llm_context=context)
        self.assertEqual({(s["resource_id"], s["evidence_id"]) for s in results["case_50"][1]},
                         {("analytics_7438", "evidence_19"), ("analytics_7438", "")})
        attachment = next(o for o in context["observations"] if o["file_id"] == "evidence_46")
        self.assertEqual((attachment["resource_id"], attachment["raw_resource_id"]),
                         ("object_19", "delivery_confirmation_19.pdf"))
        # The alias is a dated catalog fact: hidden from a case audited before that row exists.
        self.assertFalse(pipeline.eligible(attachment, "2026-02-13T08:00:00Z"))
        self.assertTrue(pipeline.eligible(attachment, "2026-02-14T10:00:00Z"))
        self.assertEqual([(s["operation"], s["resource_id"]) for s in results["case_61"][1]],
                         [("REPORT_REMAINING_RECORD", "object_19"), ("DELETE_RECORD", "object_19"),
                          ("VERIFY_RECORD_ABSENT", "object_19")])

    def test_canonical_resource_direct_alias_unknown_and_ambiguous(self):
        link = "LINK_RESOURCE_IDENTITY"
        activities = [
            {"resource_id": "account_1", "event_type": "UPDATE_RECORD", "link_alias": None},
            {"resource_id": "account_2", "event_type": "UPDATE_RECORD", "link_alias": None},
            {"resource_id": "tombstone_9", "event_type": link, "link_alias": "user_9",
             "event_time": "2026-03-10T06:07:01Z"},
            {"resource_id": "export_5", "event_type": link, "link_alias": "export_5", "event_time": "x"},
            {"resource_id": "export_7", "event_type": link, "link_alias": "Same-7", "event_time": "x"},
            {"resource_id": "export_8", "event_type": link, "link_alias": "Same-7", "event_time": "x"}]
        names = tasks.known_resource_names(activities, [{"resource_id": "search_3"}, {"resource_id": None}])

        def resolve(resource_id, **fields):
            return tasks.canonical_resource({"resource_id": resource_id, "fields": fields}, names)
        self.assertEqual(resolve("user_1", account_id="account_1"), ("account_1", None))
        self.assertEqual(resolve("user_9"), ("tombstone_9", "2026-03-10T06:07:01Z"))
        self.assertEqual(resolve("CS-1", header="search_3", note="free text"), ("search_3", None))
        self.assertEqual(resolve("account_2", other="account_1"), ("account_2", None))  # known name kept
        self.assertEqual(resolve("user_1", a="account_1", b="account_2"), ("user_1", None))  # ambiguous
        self.assertEqual(resolve("Same-7"), ("Same-7", None))  # one alias, two resources
        self.assertEqual(resolve("user_1", count=3), ("user_1", None))  # nothing known

    def test_screenshot_superseded_by_later_eligible_observation_does_not_block(self):
        self.fixtures["evidence_14"] = {"observations": [{
            "resource_id": "data_flow_9846", "system": "BridgeOps", "collection": None, "copy": None,
            "observed_at": None, "source": "data_flow_9846 | lucia-torres@gmail.com | Continuous",
            "fields": {"selector": "lucia-torres@gmail.com", "mode": "Continuous"}}], "uncertainties": []}
        request = [r for r in pipeline.read_requests() if r["case_id"] == "case_15"]
        self.assertEqual(self.prepare(request)["trace"]["llm"]["issues"], [])  # evidence_01 is later and eligible
        manifest = copy.deepcopy(self.manifest)  # control: when the image is the latest it decides
        next(f for f in manifest if f["file_id"] == "evidence_14")["captured_at"] = "2026-09-15T11:59:50Z"
        with patch.object(evidence, "load_manifest", return_value=manifest), \
                self.assertRaises(tasks.ReviewRequired) as caught:
            self.prepare(request)
        self.assertEqual(caught.exception.trace["llm"]["issues"][0]["resource_id"], "data_flow_9846")


if __name__ == "__main__":
    unittest.main()
