"""Inferred links (PLAN 6.3, 6.5): the pure assignment in code/inference.py and its per-cutoff use in the pipeline."""

import random
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import activity
import evidence
import identity
import inference
import pipeline

BASE = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def case(number, hours=0, status="COMPLETE", side=0):
    return {"case_id": f"case_{number}", "reported_at": BASE + timedelta(hours=hours), "status": status, "side": side}


def row(number, hours, result="SUCCESS"):
    return {"activity_id": f"activity_{number}", "resource_id": f"account_{number}",
            "time": BASE + timedelta(hours=hours), "result": result}


def chosen(outcomes):
    return {c: (o["row"]["activity_id"] if o["row"] else None) for c, o in outcomes.items()}


class AssignTests(unittest.TestCase):
    def test_weak_candidates_are_never_forced(self):
        # 0.6 h outside the account window costs 1.2, above NO_MATCH; the right time but the wrong
        # result still wins when it is the only candidate (a small status cost is not a reason to refuse).
        far = inference.assign("account", [case(1)], [row(10, 0.5 + 0.6)])
        self.assertIsNone(far["case_1"]["row"])
        wrong_result = inference.assign("account", [case(1)], [row(10, -1.0, "FAILED")])
        self.assertEqual(chosen(wrong_result), {"case_1": "activity_10"})

    def test_follow_ups_on_the_other_side_reject_an_analytics_row(self):
        analytics = lambda n, side: (case(n, side=side), {"activity_id": "activity_9", "resource_id": "analytics_9",
                                                             "time": BASE - timedelta(hours=0.48), "result": "SUCCESS"})
        self.assertIsNone(inference.assign("analytics", *[[x] for x in analytics(1, 1)])["case_1"]["row"])
        self.assertIsNotNone(inference.assign("analytics", *[[x] for x in analytics(1, -1)])["case_1"]["row"])
        self.assertIsNotNone(inference.assign("analytics", *[[x] for x in analytics(1, 0)])["case_1"]["row"])

    def test_one_row_goes_to_one_case_and_the_other_gets_no_match(self):
        outcomes = inference.assign("account", [case(2), case(1)], [row(10, -1.0)])
        self.assertEqual(chosen(outcomes), {"case_1": "activity_10", "case_2": None})
        self.assertEqual(outcomes["case_1"]["ambiguous"], ["no match"])  # case_2 could equally take it

    def test_constraint_moves_a_case_to_its_second_choice(self):
        # Both rows fit case_1, and it prefers row 10 (SUCCESS agrees with COMPLETE). Row 11 is outside
        # case_2's window, so case_2 can only take row 10 and case_1 settles for row 11.
        cases = [case(1, hours=0), case(2, hours=-1.4)]
        rows = [row(10, -1.0), row(11, 0.4, "FAILED")]
        outcomes = inference.assign("account", cases, rows)
        self.assertEqual(chosen(outcomes), {"case_1": "activity_11", "case_2": "activity_10"})
        self.assertTrue(outcomes["case_1"]["one_to_one_only"])
        self.assertFalse(outcomes["case_2"]["one_to_one_only"])

    def test_equal_candidates_pair_in_case_and_activity_order(self):
        rows = [row(11, -1.0), row(10, -1.0)]
        rows[0]["time"] = BASE - timedelta(hours=1.0, seconds=-5)  # activity_11 is later than activity_10
        outcomes = inference.assign("account", [case(2), case(1)], rows)
        self.assertEqual(chosen(outcomes), {"case_1": "activity_10", "case_2": "activity_11"})
        self.assertTrue(all(o["ambiguous"] for o in outcomes.values()))

    def test_shuffled_input_gives_identical_outcomes(self):
        rng = random.Random(5)
        cases = [case(n, hours=rng.choice([0, 0, 1, 2]), status=rng.choice(list(inference.STATUS_RESULT))) for n in range(1, 25)]
        rows = [row(100 + n, rng.uniform(-3, 3), rng.choice(["SUCCESS", "PARTIAL", "FAILED"])) for n in range(30)]
        expected = inference.assign("account", cases, rows)
        for seed in range(5):
            shuffled_cases, shuffled_rows = list(cases), list(rows)
            random.Random(seed).shuffle(shuffled_cases)
            random.Random(seed).shuffle(shuffled_rows)
            self.assertEqual(inference.assign("account", shuffled_cases, shuffled_rows), expected)


class CutoffTests(unittest.TestCase):
    """infer_links on real participant data with small, explicit changes."""

    @classmethod
    def setUpClass(cls):
        cls.observations = evidence.load_observations()
        cls.activities = activity.load_activity()
        cls.requests = pipeline.read_requests()

    def infer(self, requests, activities, observations=None):
        index = pipeline.Index(self.observations if observations is None else observations, activities)
        intents = {r["case_id"]: {"decision": "DELETE_AUTHORIZED"} for r in requests}
        by_person = {}
        for r in requests:
            user_id = identity.resolve_user(r["email"], r["audit_at"])
            by_person.setdefault(user_id, []).append(r)
        return pipeline.infer_links(requests, intents, by_person, index, activities)

    @staticmethod
    def accounts(found):
        return {c: sorted(l["resource_id"] for l in f["links"] if l["resource_id"].startswith("account_"))
                for c, f in found.items()}

    def crafted(self, audit_after_report_hours):
        """case_07 with a delete row 0.4 h after reported_at, and an audit_at that is before or after it."""
        base = next(r for r in self.requests if r["case_id"] == "case_07")
        reported = identity.to_time(base["reported_at"])
        stamp = lambda t: t.strftime("%Y-%m-%dT%H:%M:%SZ")
        request = dict(base, audit_at=stamp(reported + timedelta(hours=audit_after_report_hours)))
        extra = {**next(a for a in self.activities if a["resource_id"] == "account_007"),
                 "activity_id": "activity_99001", "resource_id": "account_99001",
                 "event_time": stamp(reported + timedelta(hours=0.4))}
        others = [a for a in self.activities if a["resource_id"] != "account_007"]
        return request, others + [extra]

    def test_row_after_the_cutoff_is_never_used(self):
        request, activities = self.crafted(0.2)
        self.assertEqual(self.accounts(self.infer([request], activities))["case_07"], [])
        request, activities = self.crafted(0.6)
        self.assertEqual(self.accounts(self.infer([request], activities))["case_07"], ["account_99001"])

    def test_later_activity_never_changes_earlier_cutoffs(self):
        cutoff = "2026-09-15T12:00:00Z"
        requests = [r for r in self.requests if identity.at_or_before(r["audit_at"], cutoff)]
        baseline = self.infer(requests, self.activities)
        # Copies of every earlier delete / anonymize row, moved 100 days later (after every cutoff in play).
        later = [dict(a, activity_id=f"activity_{100000 + activity.activity_number(a)}", resource_id=a["resource_id"] + "9",
                      event_time=(identity.to_time(a["event_time"]) + timedelta(days=100)).strftime("%Y-%m-%dT%H:%M:%SZ"))
                 for a in self.activities if a["event_type"] in ("DELETE_RECORD", "ANONYMIZE_RECORD")]
        self.assertEqual(self.accounts(self.infer(requests, self.activities + later)), self.accounts(baseline))

    def test_shuffled_requests_and_activity_give_identical_links(self):
        cutoff = "2026-09-15T12:00:00Z"
        requests = [r for r in self.requests if identity.at_or_before(r["audit_at"], cutoff)]
        baseline = self.infer(requests, self.activities)
        requests, activities = list(requests), list(self.activities)
        random.Random(3).shuffle(requests)
        random.Random(4).shuffle(activities)
        again = self.infer(requests, activities)
        links = lambda found: {c: sorted((l["resource_id"], l["source"]) for l in f["links"]) for c, f in found.items()}
        self.assertEqual(links(again), links(baseline))

    def test_resource_proven_to_someone_else_is_never_inferred(self):
        # account_005's delete row fits a request reported 0.5 h after it, but case_05's person owns the account.
        owner_row = next(a for a in self.activities if a["resource_id"] == "account_005" and a["event_type"] == "DELETE_RECORD")
        base = next(r for r in self.requests if r["case_id"] == "case_07")
        row_time = identity.to_time(owner_row["event_time"])
        stamp = lambda t: t.strftime("%Y-%m-%dT%H:%M:%SZ")
        request = dict(base, reported_status="COMPLETE", reported_at=stamp(row_time + timedelta(hours=0.5)),
                       audit_at=stamp(row_time + timedelta(days=10)))
        decoys = [a for a in self.activities if a["event_type"] != "DELETE_RECORD" or a["resource_id"] == "account_005"]
        self.assertEqual(self.accounts(self.infer([request], decoys))["case_07"], [])
        # The same row is chosen when nothing in the evidence ties account_005 to anyone.
        unowned = [o for o in self.observations if "account_005" not in (o.get("resource_id"), *o.get("related_resource_ids", []))]
        self.assertEqual(self.accounts(self.infer([request], decoys, unowned))["case_07"], ["account_005"])


if __name__ == "__main__":
    unittest.main()
