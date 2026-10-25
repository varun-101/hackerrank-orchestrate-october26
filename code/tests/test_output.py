"""Tests for output serialization and validation. Run from the repository root: python -m unittest discover -s code/tests"""

import csv
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import output  # noqa: E402

DATASET = Path(__file__).resolve().parent.parent.parent / "dataset"


def write_csv(rows):
    handle, path = tempfile.mkstemp(suffix=".csv")
    with os.fdopen(handle, "w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows([output.COLUMNS] + rows)
    return path


class SortAndSerializeTests(unittest.TestCase):
    def test_every_sample_row_round_trips_unchanged(self):
        rows = output.read_rows(DATASET / "sample_output.csv")
        total = 0
        for row in rows:
            steps = json.loads(row["steps_json"])
            total += len(steps)
            serialized = output.serialize_steps(output.sort_steps(steps))
            # Sample rows differ in key order (case_03 uses field order), so compare parsed arrays.
            self.assertEqual(json.loads(serialized), steps, row["case_id"])
            self.assertEqual(output.serialize_steps(json.loads(serialized)), serialized)
        self.assertEqual(total, 193)

    def test_sort_orders_phase_operation_system_resource_and_renumbers(self):
        steps = [
            output.make_step("VERIFY_RECORD_ABSENT", "search", "doc_1"),
            output.make_step("DELETE_RECORD", "search", "doc_1"),
            output.make_step("REPORT_REMAINING_RECORD", "search", "doc_1", "evidence_01"),
            output.make_step("DISABLE_DATA_FLOW", "sync", "job_1"),
            output.make_step("DELETE_RECORD", "account", "acc_2"),
            output.make_step("DELETE_RECORD", "account", "acc_1"),
        ]
        ordered = output.sort_steps(steps)
        self.assertEqual([(s["operation"], s["resource_id"]) for s in ordered], [
            ("REPORT_REMAINING_RECORD", "doc_1"), ("DISABLE_DATA_FLOW", "job_1"), ("DELETE_RECORD", "acc_1"),
            ("DELETE_RECORD", "acc_2"), ("DELETE_RECORD", "doc_1"), ("VERIFY_RECORD_ABSENT", "doc_1")])
        self.assertEqual([s["step_no"] for s in ordered], [1, 2, 3, 4, 5, 6])
        self.assertEqual(steps[0]["step_no"], 0)  # inputs are not modified

    def test_tied_steps_sort_the_same_whatever_the_input_order(self):
        tied = [output.make_step("REPORT_REMAINING_RECORD", "search", "doc_1", e) for e in ("ev_3", "ev_1", "ev_2")]
        first = output.sort_steps(tied)
        self.assertEqual([s["evidence_id"] for s in first], ["ev_1", "ev_2", "ev_3"])
        self.assertEqual(output.sort_steps(list(reversed(tied))), first)
        self.assertEqual(output.sort_steps([tied[1], tied[2], tied[0]]), first)

    def test_make_step_has_exactly_the_contract_fields(self):
        step = output.make_step("REDACT_RECORD", "support", "ticket_1")
        self.assertEqual(sorted(step), sorted(output.STEP_FIELDS))
        self.assertEqual((step["phase"], step["state"], step["evidence_id"]), ("REMEDIATION", "REDACTED", ""))
        with self.assertRaises(ValueError):
            output.make_step("PURGE_RECORD", "support", "ticket_1")

    def test_write_output_orders_cases_numerically_and_writes_empty_array(self):
        handle, path = tempfile.mkstemp(suffix=".csv")
        os.close(handle)
        step = output.make_step("VERIFY_RECORD_ABSENT", "account", "acc_1", "evidence_01")
        output.write_output(path, [("case_100", "COMPLETE", [step]), ("case_33", "NOT_APPLICABLE", [step]),
                                   ("case_9", "COMPLETE", [step])])
        rows = output.read_rows(path)
        os.remove(path)
        self.assertEqual([r["case_id"] for r in rows], ["case_9", "case_33", "case_100"])
        self.assertEqual(rows[1]["steps_json"], "[]")
        self.assertEqual(json.loads(rows[0]["steps_json"])[0]["step_no"], 1)


class ValidatorTests(unittest.TestCase):
    def setUp(self):
        self.finding = output.make_step("REPORT_REMAINING_RECORD", "search", "doc_1", "evidence_01")
        self.fix = output.make_step("DELETE_RECORD", "search", "doc_1")
        self.check = output.make_step("VERIFY_RECORD_ABSENT", "search", "doc_1")

    def errors(self, status, steps):
        return output.validate_steps(status, output.sort_steps(steps))

    def test_sample_output_is_valid(self):
        ids = [r["case_id"] for r in output.read_rows(DATASET / "sample_deletion_requests.csv")]
        self.assertEqual(output.validate_output(DATASET / "sample_output.csv", ids), [])

    def test_valid_incomplete_case(self):
        self.assertEqual(self.errors("INCOMPLETE", [self.finding, self.fix, self.check]), [])

    def test_incomplete_remediation_must_not_cite(self):
        self.fix["evidence_id"] = "evidence_01"
        self.assertTrue(self.errors("INCOMPLETE", [self.finding, self.fix, self.check]))

    def test_complete_needs_cited_verification_only(self):
        self.assertTrue(self.errors("COMPLETE", [self.check]))
        self.check["evidence_id"] = "evidence_02"
        self.assertEqual(self.errors("COMPLETE", [self.check]), [])
        self.assertTrue(self.errors("COMPLETE", [self.finding, self.check]))

    def test_wrong_state_numbering_order_and_fields(self):
        bad_state = dict(self.finding, state="ACTIVE")
        self.assertTrue(self.errors("INCOMPLETE", [bad_state, self.fix, self.check]))
        renumbered = output.sort_steps([self.finding, self.fix, self.check])
        renumbered[2]["step_no"] = 5
        self.assertTrue(output.validate_steps("INCOMPLETE", renumbered))
        reordered = output.sort_steps([self.finding, self.fix, self.check])
        reordered[1], reordered[2] = reordered[2], reordered[1]
        reordered[1]["step_no"], reordered[2]["step_no"] = 2, 3
        self.assertTrue(output.validate_steps("INCOMPLETE", reordered))
        extra_field = dict(self.finding, note="x")
        self.assertTrue(self.errors("INCOMPLETE", [extra_field, self.fix, self.check]))

    def test_incomplete_needs_remediation_and_verification_for_each_finding(self):
        finding_only = self.errors("INCOMPLETE", [self.finding])
        self.assertIn("INCOMPLETE case has no REMEDIATION step", finding_only)
        self.assertIn("INCOMPLETE case has no VERIFICATION step", finding_only)
        no_check = self.errors("INCOMPLETE", [self.finding, self.fix])
        self.assertEqual(no_check, ["INCOMPLETE case has no VERIFICATION step",
                                    "DELETE_RECORD on ('search', 'doc_1') has no VERIFY_RECORD_ABSENT step"])
        other_check = output.make_step("VERIFY_RECORD_ABSENT", "search", "doc_2")
        self.assertIn("DELETE_RECORD on ('search', 'doc_1') has no VERIFY_RECORD_ABSENT step",
                      self.errors("INCOMPLETE", [self.finding, self.fix, other_check]))
        wrong_fix = output.make_step("REDACT_RECORD", "search", "doc_1")
        wrong_check = output.make_step("VERIFY_RECORD_REDACTED", "search", "doc_1")
        self.assertIn("REPORT_REMAINING_RECORD on ('search', 'doc_1') has no DELETE_RECORD step",
                      self.errors("INCOMPLETE", [self.finding, wrong_fix, wrong_check]))

    def test_exposed_pii_accepts_redact_or_anonymize(self):
        finding = output.make_step("REPORT_EXPOSED_PII", "crm", "c_1", "evidence_03")
        for fix, check in (("REDACT_RECORD", "VERIFY_RECORD_REDACTED"), ("ANONYMIZE_RECORD", "VERIFY_RECORD_ANONYMIZED")):
            plan = [finding, output.make_step(fix, "crm", "c_1"), output.make_step(check, "crm", "c_1")]
            self.assertEqual(self.errors("INCOMPLETE", plan), [], fix)

    def test_malformed_field_types_are_errors_not_exceptions(self):
        steps = output.sort_steps([self.finding, self.fix, self.check])
        for field, value in (("operation", []), ("phase", None), ("evidence_id", 7), ("step_no", True), ("step_no", "1")):
            bad = [dict(s) for s in steps]
            bad[0][field] = value
            errors = output.validate_steps("INCOMPLETE", bad)
            self.assertTrue(errors, f"{field}={value!r}")
            self.assertIn("step 1:", errors[0])

    def test_not_applicable_needs_empty_array(self):
        self.assertEqual(output.validate_steps("NOT_APPLICABLE", []), [])
        self.assertTrue(output.validate_steps("NOT_APPLICABLE", [self.check]))

    def test_file_level_checks(self):
        good = json.dumps([])
        path = write_csv([["case_10", "NOT_APPLICABLE", good], ["case_9", "NOT_APPLICABLE", good],
                          ["case_11", "MAYBE", good], ["case_12", "COMPLETE", "[{"]])
        errors = output.validate_output(path, ["case_9", "case_10", "case_11", "case_12", "case_13"])
        os.remove(path)
        text = "\n".join(errors)
        self.assertIn("case ids differ", text)
        self.assertIn("numeric case_id order", text)
        self.assertIn("unknown deletion_status", text)
        self.assertIn("invalid JSON", text)


if __name__ == "__main__":
    unittest.main()
