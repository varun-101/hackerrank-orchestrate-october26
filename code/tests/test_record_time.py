"""Record-time rule (PLAN 5.5): a record's own time decides ownership; capture time decides state and citations.

Worked example: sample case_09 (user_61, audit_at 2026-09-16T09:00:00Z). evidence_06 is a log captured
2026-09-18, after the cutoff, whose line at 08:52:59 ties audit_record_4902 to user_61.
"""

import random
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import activity
import evidence
import pipeline

CUTOFF = "2026-09-16T09:00:00Z"
VALUES = {"user_61"}


class RecordTimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.observations = evidence.load_observations()
        cls.obs = {o["obs_id"]: o for o in cls.observations}
        cls.activities = activity.load_activity()
        cls.late = cls.obs["evidence_06#8"]  # the audit_record_4902 read, in a file captured after CUTOFF
        cls.case_09 = next(r for r in pipeline.read_requests() if r["case_id"] == "case_09")

    def links(self, obs):
        return pipeline.subject_links(pipeline.Index([obs], []), VALUES, CUTOFF)

    def test_record_before_cutoff_in_late_file_links_but_is_not_eligible_state(self):
        self.assertFalse(pipeline.eligible(self.late, CUTOFF))  # never citable, never state
        links, excluded = self.links(self.late)
        self.assertEqual([(l["resource_id"], l["late_file"]) for l in links], [("audit_record_4902", True)])
        self.assertEqual(excluded, [])
        index = pipeline.Index([self.late], [])
        self.assertEqual(pipeline.state_entries(index, "audit_record_4902", "security_service", CUTOFF, VALUES), [])

    def test_record_after_cutoff_in_late_file_does_not_link(self):
        links, excluded = self.links(dict(self.late, observed_at="2026-09-16T09:30:00Z"))
        self.assertEqual((links, excluded), ([], [self.late["obs_id"]]))

    def test_record_without_own_time_in_late_file_does_not_link(self):
        links, excluded = self.links(dict(self.late, observed_at=None))
        self.assertEqual((links, excluded), ([], [self.late["obs_id"]]))
        # The same record in a file captured by the cutoff has no time of its own but does link.
        early = dict(self.late, observed_at=None, file_captured_at="2026-09-16T08:55:00Z")
        self.assertEqual([l["late_file"] for l in self.links(early)[0]], [False])

    def test_late_link_is_proven_to_its_owner_for_inference(self):
        # Inference never offers a resource that evidence ties to someone, even from a late file.
        index = pipeline.Index([self.late], [])
        self.assertIn("audit_record_4902", pipeline.owned_resources(index, CUTOFF))
        later = pipeline.Index([dict(self.late, observed_at="2026-09-16T09:30:00Z")], [])
        self.assertNotIn("audit_record_4902", pipeline.owned_resources(later, CUTOFF))

    def test_retained_record_linked_through_late_file_is_verified_without_evidence(self):
        results, trace = pipeline.run([self.case_09])
        status, steps = results["case_09"]
        self.assertEqual(status, "INCOMPLETE")
        present = [s for s in steps if s["resource_id"] == "audit_record_4902"]
        self.assertEqual(present, [{"step_no": present[0]["step_no"], "phase": "VERIFICATION",
                                    "system_id": "security_service", "resource_id": "audit_record_4902",
                                    "operation": "VERIFY_RECORD_PRESENT", "state": "PRESENT", "evidence_id": ""}])
        self.assertNotIn("evidence_06", {s["evidence_id"] for s in steps})
        events = {e["stage"]: e for e in trace["cases"]["case_09"]}
        self.assertIn("evidence_06#8", events["cutoff"]["outputs"]["linked_from_late_file"])
        self.assertEqual(events["steps"]["outputs"]["verified_without_state_evidence"], ["audit_record_4902"])
        for stage in ("state", "steps"):  # no state and no citation comes from the late file
            self.assertFalse([r for r in events[stage]["evidence"] if r["id"] == "evidence_06"], stage)

    def resource(self, resource_id, verdict, action, cited="", retained_without_state=False):
        return {"resource_id": resource_id, "system": "security_service", "action": action, "verdict": verdict,
                "candidates": {cited: {"names": True, "copies": {"primary"}, "obs": [cited + "#1"]}} if cited else {},
                "retained_without_state": retained_without_state}

    def test_complete_case_does_not_gain_a_retained_step_without_evidence(self):
        retained = self.resource("audit_record_1", "gap", "RETAIN", retained_without_state=True)
        compliant = self.resource("account_1", "compliant", "DELETE", "evidence_01")
        violation = self.resource("account_2", "violation", "DELETE", "evidence_02")
        captured = {"evidence_01": "2026-09-01T00:00:00Z", "evidence_02": "2026-09-01T00:00:00Z"}
        status, steps, _ = pipeline.build_steps([retained, compliant], captured)
        self.assertEqual(status, "COMPLETE")
        self.assertEqual([s["resource_id"] for s in steps], ["account_1"])  # unchanged, no invented citation
        status, steps, _ = pipeline.build_steps([retained, compliant, violation], captured)
        self.assertEqual(status, "INCOMPLETE")
        added = [s for s in steps if s["resource_id"] == "audit_record_1"]
        self.assertEqual([(s["operation"], s["state"], s["evidence_id"]) for s in added],
                         [("VERIFY_RECORD_PRESENT", "PRESENT", "")])

    def test_later_inputs_do_not_change_earlier_answers(self):
        requests = [self.case_09]
        expected, _ = pipeline.run(requests)
        activities = list(self.activities)
        activities.append(dict(activities[0], activity_id="activity_99999", event_time="2026-12-01T00:00:00Z"))
        # A late file whose records are all timed after the cutoff: it cannot link, state or cite anything.
        observations = list(self.observations)
        for n, source in enumerate(["evidence_06#8", "evidence_06#26", "evidence_93#24", "evidence_51#4"]):
            observations.append(dict(self.obs[source], obs_id=f"later#{n}", file_id="later",
                                     file_captured_at="2026-12-01T00:00:00Z", observed_at="2026-12-01T00:00:00Z"))
        random.Random(5).shuffle(observations)
        with patch.object(evidence, "load_observations", return_value=observations), \
             patch.object(activity, "load_activity", return_value=activities):
            actual, _ = pipeline.run(requests)
        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
