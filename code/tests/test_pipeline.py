"""Stage 2b boundaries, exercised with participant evidence and small edits of those records."""

import copy
import random
import sys
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import activity
import evidence
import identity
import output
import pipeline
import policy
from evaluation.main import load, sample_analytics


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.observations = evidence.load_observations()
        cls.obs = {o["obs_id"]: o for o in cls.observations}
        cls.activities = activity.load_activity()
        cls.index = pipeline.Index(cls.observations, cls.activities)
        cls.requests = pipeline.read_requests()
        cls.results, cls.trace = pipeline.run()

    def links(self, case_id):
        return self.trace["cases"][case_id][-1]["outputs"]["resource_links"]

    def test_lookup_result_required_and_provenance_preserved(self):
        returned = self.obs["evidence_01#3"]
        values = {"leonie.g9352@outlook.com"}
        links, _ = pipeline.subject_links(pipeline.Index([returned], []), values, "2026-09-15T12:00:00Z")
        self.assertEqual([(l["resource_id"], l["type"]) for l in links], [("account_4092", "lookup")])
        request_only = copy.deepcopy(returned)
        # Same trace, but the ID now appears only on the request, never on its completed result.
        request_only["source"] = returned["source"].replace("event=profile_lookup_started",
                                                                         "resource_id=account_4092 event=profile_lookup_started")
        request_only["source"] = request_only["source"].replace("resource_id=account_4092 event=profile_lookup_completed",
                                                                         "event=profile_lookup_completed")
        self.assertFalse(pipeline.returns_resource(request_only))
        self.assertEqual(pipeline.subject_links(pipeline.Index([request_only], []), values,
                                               "2026-09-15T12:00:00Z")[0], [])

    def test_repeat_requests_split_links_but_single_request_keeps_history(self):
        self.assertIn("account_4092", self.links("case_17"))
        self.assertNotIn("account_6523", self.links("case_17"))
        self.assertNotIn("tombstone_17", self.links("case_17"))
        self.assertIn("account_6523", self.links("case_46"))
        self.assertNotIn("object_19", self.links("case_07"))
        self.assertIn("object_19", self.links("case_61"))
        self.assertNotIn("export_1260", self.links("case_33"))
        self.assertIn("export_1260", self.links("case_52"))
        request = next(r for r in self.requests if r["case_id"] == "case_34")
        link_event = next(e for e in self.trace["cases"]["case_34"] if e["stage"] == "linking")
        self.assertTrue(any(identity.to_time(l["time"]) < identity.to_time(request["requested_at"])
                            for l in link_event["outputs"]["links"]))

    def test_json_absence_needs_eligible_bundle_membership(self):
        cutoff = "2026-09-15T12:00:00Z"
        def bundle_entries(index, values):
            return [e for e in pipeline.state_entries(index, "account_4857", "account_service", cutoff, values)
                    if e["file_id"] == "evidence_51"]
        self.assertTrue(bundle_entries(self.index, {"user_541"}))
        self.assertFalse(bundle_entries(self.index, {"user_01"}))
        later = [dict(o, observed_at="2026-12-01T00:00:00Z") if o["obs_id"] in {"evidence_51#3", "evidence_51#6"}
                 else o for o in self.observations]
        self.assertFalse(bundle_entries(pipeline.Index(later, []), {"user_541"}))

    def test_same_time_observations_order_by_numeric_suffix(self):
        entry = lambda n, state: {"time": "2026-09-01T00:00:00Z", "copy": "primary", "state": state,
                                  "obs_id": f"evidence_04#{n}", "file_id": "evidence_04", "names": True}
        latest = pipeline.latest_per_copy([entry(10, "ABSENT"), entry(9, "EXPOSED")])
        self.assertEqual(latest["primary"]["obs_id"], "evidence_04#10")

    def test_primary_absence_does_not_clear_failover(self):
        entries = pipeline.state_entries(self.index, "account_005", "account_service",
                                          "2026-09-15T12:00:00Z", {"user_05"})
        latest = pipeline.latest_per_copy(entries)
        self.assertEqual(latest["primary"]["state"], "ABSENT")
        self.assertEqual(latest["failover_replica"]["state"], "EXPOSED")
        self.assertEqual(pipeline.judge("DELETE", latest), "violation")
        self.assertIn("evidence_54", pipeline.citation_candidates(entries, {"EXPOSED", "CLEAN"}))
        self.assertNotIn("evidence_90", pipeline.citation_candidates(entries, {"EXPOSED", "CLEAN"}))

    def test_policy_contact_tombstone_backup_and_attachment(self):
        rules = policy.load_rules()
        self.assertEqual(policy.observed_state(self.obs["evidence_51#3"]), "CLEAN")
        self.assertEqual(policy.observed_state(self.obs["evidence_51#4"]), "EXPOSED")
        contact = [o for o in self.observations if (o.get("resource_id") or "").startswith("analytics_")
                   and policy.is_contact_record(o)]
        self.assertTrue(contact)
        self.assertEqual(policy.classify_rule(contact[0]["resource_id"], contact, rules)[2], "DELETE")
        denied = next(o for o in self.observations if o.get("resource_id") == "backup_3250")
        self.assertEqual(policy.observed_state(denied), "BLOCKED")
        attachment = [o for o in self.observations if o.get("resource_id") == "support_001"]
        self.assertEqual(policy.classify_rule("support_001", attachment, rules)[2], "DELETE")

    def test_catalog_alias_requires_both_document_and_link_before_cutoff(self):
        linked = self.index.by_resource["export_1842"]
        pdf = next(o for o in linked if o["file_type"] == "pdf")
        self.assertFalse(pipeline.eligible(pdf, "2026-07-18T11:05:00Z"))
        self.assertTrue(pipeline.eligible(pdf, "2026-07-18T12:00:00Z"))
        finding = self.results["case_33"][1][0]
        self.assertEqual((finding["resource_id"], finding["evidence_id"]), ("export_1842", "evidence_18"))

    def test_activity_claim_supplies_system_but_not_state(self):
        # The search document explicitly mentions account_006. Its activity supplies the service;
        # the empty SQL snapshot, not the worker's SUCCESS, proves the account's absence.
        account = next(s for s in self.results["case_06"][1] if s["resource_id"] == "account_006")
        self.assertEqual(account["operation"], "VERIFY_RECORD_ABSENT")
        self.assertEqual(account["evidence_id"], "")
        empty = pipeline.Index([], self.activities)
        self.assertEqual(pipeline.state_entries(empty, "account_006", "account_service",
                                                "2026-09-15T12:00:00Z", set()), [])

    def test_trace_and_output_agree_and_citations_are_eligible(self):
        cutoffs = {r["case_id"]: r["audit_at"] for r in self.requests}
        for cid, (status, steps) in self.results.items():
            self.assertEqual(steps, self.trace["cases"][cid][-1]["outputs"]["steps"])
            if steps or status == "NOT_APPLICABLE":
                self.assertEqual(output.validate_steps(status, steps), [], cid)
            for event in self.trace["cases"][cid]:
                for ref in event["evidence"]:
                    if "obs_id" in ref:
                        self.assertTrue(pipeline.eligible(self.obs[ref["obs_id"]], cutoffs[cid]), (cid, ref["id"]))

    def test_future_inputs_and_shuffled_inputs_do_not_change_predictions(self):
        # The real evidence_13 bundle contains a later record inside an already eligible file.
        late_record = self.obs["evidence_13#39"]
        self.assertTrue(identity.at_or_before(late_record["file_captured_at"], "2026-08-12T09:00:00Z"))
        self.assertFalse(pipeline.eligible(late_record, "2026-08-12T09:00:00Z"))
        rng = random.Random(17)
        observations = list(self.observations)
        activities = list(self.activities)
        requests = list(self.requests)
        rng.shuffle(observations)
        rng.shuffle(activities)
        rng.shuffle(requests)
        # Append a later-captured contradiction with an older record time.
        future = dict(self.obs["evidence_51#4"], obs_id="later#1", file_id="later",
                      file_captured_at="2026-12-01T00:00:00Z")
        observations.append(future)
        future_activity = dict(activities[0], activity_id="activity_99999", event_time="2026-12-01T00:00:00Z")
        activities.append(future_activity)
        with patch.object(evidence, "load_observations", return_value=observations), \
             patch.object(activity, "load_activity", return_value=activities):
            actual, _ = pipeline.run(requests)
        self.assertEqual(actual, self.results)

    def test_pipeline_never_reads_sample_answers(self):
        import builtins
        original = builtins.open
        def checked(path, *args, **kwargs):
            self.assertNotEqual(Path(path).name, "sample_output.csv")
            return original(path, *args, **kwargs)
        with patch("builtins.open", side_effect=checked):
            pipeline.run(self.requests[:1])

    def test_intent_is_deduplicated_and_unsure_stops_before_loading_evidence(self):
        requests = [dict(self.requests[0], case_id=f"case_{n}") for n in range(3)]
        with patch.object(evidence, "load_observations") as load:
            with self.assertRaises(pipeline.UnsureIntent):
                pipeline.run(requests, classify=lambda text: {"decision": "UNSURE"})
            load.assert_not_called()
        calls = []
        def classify(text):
            calls.append(text)
            return {"decision": "DELETE_AUTHORIZED"}
        pipeline.classify_intents(requests, classify)
        self.assertEqual(len(calls), 1)

    def test_analytics_separates_unlinked_steps_without_claiming_inference(self):
        cid = "case_06"
        predicted = {cid: self.results[cid]}
        expected = {cid: (predicted[cid][0], predicted[cid][1] + [
            output.make_step("VERIFY_RECORD_ABSENT", "account_service", "unlinked")])}
        report = sample_analytics(expected, predicted, self.trace)
        self.assertEqual(report["unmatched_expected_resources"], 1)
        self.assertIsNone(report["by_link_type"]["inferred"])
        self.assertEqual(report["by_link_type"]["proven"]["step_multiset"]["accuracy"], 1)

    def test_split_scores_penalize_reversed_case06_order(self):
        expected = {"case_06": load(evidence.DATASET / "sample_output.csv")["case_06"]}
        status, steps = expected["case_06"]
        predicted = {"case_06": (status, [dict(s, step_no=n) for n, s in enumerate(reversed(steps), 1)])}
        before = copy.deepcopy((expected, predicted))
        baseline = sample_analytics(expected, expected, self.trace)["by_link_type"]["proven"]
        reversed_scores = sample_analytics(expected, predicted, self.trace)["by_link_type"]["proven"]
        self.assertEqual(reversed_scores["step_multiset"], baseline["step_multiset"])
        self.assertEqual(baseline["sequence_accuracy"], 1)
        self.assertEqual(baseline["step_positional_accuracy"], 1)
        self.assertEqual(reversed_scores["sequence_accuracy"], 0)
        self.assertEqual(reversed_scores["step_positional_accuracy"], 0)
        self.assertEqual((expected, predicted), before)

    def test_trace_intervals_enclose_calculations_and_reuse_shared_intent(self):
        requests = [r for r in self.requests if r["request_text"] == self.requests[2]["request_text"]][:2]
        self.assertEqual(len(requests), 2)
        calculations = {}
        original_subject = pipeline.subject_of
        def classify(text):
            self.assertNotIn("intent", calculations)  # one calculation for the shared text
            started = datetime.now(timezone.utc)
            time.sleep(0.01)
            answer = pipeline.intent.classify(text)
            calculations["intent"] = (started, datetime.now(timezone.utc))
            return answer
        def subject(request):
            started = datetime.now(timezone.utc)
            time.sleep(0.01)
            answer = original_subject(request)
            calculations[request["case_id"]] = (started, datetime.now(timezone.utc))
            return answer
        with patch.object(pipeline, "subject_of", side_effect=subject):
            _, trace = pipeline.run(requests, classify=classify)
        intent_refs = []
        for request in requests:
            cid = request["case_id"]
            events = {e["stage"]: e for e in trace["cases"][cid]}
            for stage, key in (("intent", "intent"), ("identity", cid)):
                event = events[stage]
                start, end = map(identity.to_time, (event["started_at"], event["ended_at"]))
                self.assertLessEqual(start, calculations[key][0])
                self.assertGreaterEqual(end, calculations[key][1])
                self.assertLessEqual(identity.to_time(trace["created_at"]), start)
            intent_refs.append(tuple(events["intent"][k] for k in ("computation_id", "started_at", "ended_at")))
        self.assertEqual(intent_refs[0], intent_refs[1])

    def test_case03_trace_records_inputs_and_actual_citation_ranking(self):
        events = {e["stage"]: e for e in self.trace["cases"]["case_03"]}
        state_input = events["state"]["inputs"]["resources"]["account_7566"]
        self.assertEqual(state_input["system"], "account_service")
        self.assertIn("evidence_94#9", state_input["observation_ids"])
        self.assertIn("evidence_54#8", state_input["observation_ids"])
        self.assertIn("evidence_94#9", state_input["link_sources"])
        policy_input = events["policy"]["inputs"]
        self.assertIn("rule_01", policy_input["rule_ids"])
        self.assertIn("evidence_94#9", policy_input["resources"]["account_7566"]["observation_ids"])
        self.assertEqual(policy_input["resources"]["account_7566"]["latest_copy_observations"]["primary"],
                         "evidence_94#9")
        step_input = events["steps"]["inputs"]["resources"]["account_7566"]
        self.assertEqual((step_input["action"], step_input["verdict"]), ("DELETE", "compliant"))
        citation = events["steps"]["outputs"]["citations"]["account_7566"]
        self.assertEqual(step_input["candidate_file_ids"], citation["candidates"])
        ranking = citation["ranking"]
        self.assertEqual(ranking["comparison_order"], ["names_resource", "other_resource_candidate_count",
                                                     "copy_coverage_preferred", "captured_at", "file_id"])
        self.assertEqual(ranking["direction"], "descending lexicographic")
        candidates = ranking["ranked_candidates"]
        self.assertEqual(len(candidates), 23)
        self.assertEqual({c["file_id"] for c in candidates}, set(citation["candidates"]))
        winner = candidates[0]
        self.assertEqual(winner["file_id"], citation["cited"])
        # Two other resources of the case (audit_record_2016 and the inferred analytics_9406) also hold
        # evidence_94 among their candidates.
        self.assertEqual(winner["comparison_key"], [True, 2, False, "2026-09-26T10:38:56Z", "evidence_94"])
        self.assertEqual(winner["observation_ids"], ["evidence_94#9"])
        self.assertEqual(winner["copies"], ["primary"])
        # The next candidate covers a replica, but loses at the earlier names-resource preference.
        runner_up = candidates[1]
        self.assertEqual(runner_up["file_id"], "evidence_05")
        self.assertEqual(runner_up["comparison_key"], [False, 0, True, "2026-09-21T10:00:00Z", "evidence_05"])
        self.assertEqual(runner_up["copies"], ["primary", "read_replica"])
        for candidate in candidates:
            self.assertEqual(candidate["comparison_key"], [candidate[k] for k in ranking["comparison_order"]])
        self.assertEqual([c["comparison_key"] for c in candidates],
                         sorted((c["comparison_key"] for c in candidates), reverse=True))
        for cid, case_events in self.trace["cases"].items():
            for event in case_events:
                self.assertTrue(event["inputs"], (cid, event["stage"]))


if __name__ == "__main__":
    unittest.main()
