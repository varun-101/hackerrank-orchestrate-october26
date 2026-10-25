"""Tests for the sample scoring script. Run from the repository root: python -m unittest discover -s code/tests"""

import copy
import importlib.util
import sys
import unittest
from pathlib import Path

CODE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CODE))

import output  # noqa: E402

# Loaded by path: the name "main" would otherwise clash with code/main.py.
_spec = importlib.util.spec_from_file_location("evaluation_main", CODE / "evaluation" / "main.py")
evaluation_main = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(evaluation_main)
score = evaluation_main.score


def incomplete_case():
    return ("INCOMPLETE", output.sort_steps([
        output.make_step("REPORT_REMAINING_RECORD", "search", "doc_1", "evidence_01"),
        output.make_step("DELETE_RECORD", "search", "doc_1"),
        output.make_step("VERIFY_RECORD_ABSENT", "search", "doc_1"),
    ]))


EXPECTED = {
    "case_01": ("COMPLETE", output.sort_steps([output.make_step("VERIFY_RECORD_ABSENT", "account", "acc_1", "evidence_02")])),
    "case_02": incomplete_case(),
    "case_03": ("NOT_APPLICABLE", []),
}


class ScoringTests(unittest.TestCase):
    def test_identical_answers_score_100_percent(self):
        scores = score(EXPECTED, copy.deepcopy(EXPECTED))
        self.assertEqual(scores["status_accuracy"], 1.0)
        self.assertEqual(scores["sequence_accuracy"], 1.0)
        self.assertEqual(scores["step_multiset"]["accuracy"], 1.0)
        self.assertEqual(scores["step_positional_accuracy"], 1.0)
        self.assertEqual(set(scores["field_accuracy"].values()), {1.0})
        self.assertEqual(scores["mismatched_cases"], [])

    def test_one_wrong_evidence_id(self):
        predicted = copy.deepcopy(EXPECTED)
        predicted["case_02"][1][0]["evidence_id"] = "evidence_99"
        scores = score(EXPECTED, predicted)
        self.assertEqual(scores["status_accuracy"], 1.0)
        self.assertEqual(scores["field_accuracy"]["evidence_id"], 3 / 4)
        self.assertEqual(scores["field_accuracy"]["resource_id"], 1.0)
        self.assertEqual(scores["step_multiset"]["matched"], 3)
        self.assertEqual(scores["sequence_accuracy"], 2 / 3)
        [case] = scores["mismatched_cases"]
        self.assertEqual(case["missing_steps"][0]["evidence_id"], "evidence_01")
        self.assertEqual(case["extra_steps"][0]["evidence_id"], "evidence_99")
        self.assertEqual(case["positional_mismatches"][0]["fields"], ["evidence_id"])

    def test_reordered_steps_lower_only_order_sensitive_metrics(self):
        predicted = copy.deepcopy(EXPECTED)
        steps = predicted["case_02"][1]
        steps.reverse()
        for number, step in enumerate(steps, start=1):
            step["step_no"] = number
        scores = score(EXPECTED, predicted)
        self.assertEqual(scores["status_accuracy"], 1.0)
        self.assertEqual(scores["step_multiset"]["accuracy"], 1.0)
        self.assertEqual(set(scores["field_accuracy"].values()), {1.0})
        self.assertEqual(scores["sequence_accuracy"], 2 / 3)
        self.assertLess(scores["step_positional_accuracy"], 1.0)  # positional is order-sensitive by definition

    def test_swapped_citations_lose_field_and_step_credit(self):
        steps = output.sort_steps([
            output.make_step("REPORT_REMAINING_RECORD", "search", "doc_1", "evidence_01"),
            output.make_step("REPORT_REMAINING_RECORD", "search", "doc_2", "evidence_02"),
        ])
        expected = {"case_01": ("INCOMPLETE", steps)}
        swapped = copy.deepcopy(steps)
        swapped[0]["evidence_id"], swapped[1]["evidence_id"] = "evidence_02", "evidence_01"
        scores = score(expected, {"case_01": ("INCOMPLETE", swapped)})
        self.assertEqual(scores["field_accuracy"]["evidence_id"], 0.0)
        self.assertEqual(scores["field_accuracy"]["resource_id"], 1.0)
        self.assertEqual(scores["step_multiset"]["matched"], 0)

    def test_alignment_falls_back_to_position_for_unmatched_steps(self):
        expected = {"case_01": ("COMPLETE", [output.make_step("VERIFY_RECORD_ABSENT", "account", "acc_1", "ev_1")])}
        predicted = {"case_01": ("COMPLETE", [output.make_step("VERIFY_RECORD_ABSENT", "account", "acc_9", "ev_1")])}
        fields = score(expected, predicted)["field_accuracy"]
        self.assertEqual((fields["resource_id"], fields["evidence_id"], fields["system_id"]), (0.0, 1.0, 1.0))

    def test_missing_not_applicable_case_earns_no_credit(self):
        predicted = copy.deepcopy(EXPECTED)
        del predicted["case_03"]
        scores = score(EXPECTED, predicted)
        self.assertEqual(scores["sequence_accuracy"], 2 / 3)
        self.assertEqual(scores["status_accuracy"], 2 / 3)
        self.assertEqual(scores["confusion"]["NOT_APPLICABLE"]["MISSING"], 1)
        self.assertEqual([c["case_id"] for c in scores["mismatched_cases"]], ["case_03"])

    def test_wrong_status_and_missing_case(self):
        predicted = copy.deepcopy(EXPECTED)
        predicted["case_03"] = incomplete_case()
        del predicted["case_01"]
        predicted["case_77"] = ("NOT_APPLICABLE", [])
        scores = score(EXPECTED, predicted)
        self.assertEqual(scores["status_accuracy"], 1 / 3)
        self.assertEqual(scores["confusion"]["NOT_APPLICABLE"]["INCOMPLETE"], 1)
        self.assertEqual(scores["confusion"]["COMPLETE"]["MISSING"], 1)
        self.assertEqual(scores["extra_predicted_cases"], 1)
        self.assertEqual(scores["step_multiset"]["precision"], 3 / 6)
        self.assertEqual(scores["step_multiset"]["recall"], 3 / 4)


if __name__ == "__main__":
    unittest.main()
