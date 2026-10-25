"""Tests for the assertion ledger (development tooling). Run from the repository root:
python -m unittest discover -s code/tests"""

import sys
import unittest
from pathlib import Path

CODE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CODE))
sys.path.insert(0, str(CODE / "evaluation"))

import evidence  # noqa: E402
import identity  # noqa: E402
import ledger  # noqa: E402

OBS = {o["obs_id"]: o for o in evidence.load_observations()}


class LedgerTests(unittest.TestCase):
    def test_observed_states(self):
        self.assertEqual(ledger.observed_state(OBS["evidence_92#21"]), "DISABLED")  # enabled=false
        self.assertEqual(ledger.observed_state(OBS["evidence_01#17"]), "ACTIVE")    # eligible, no block
        self.assertEqual(ledger.observed_state(OBS["evidence_92#14"]), "BLOCKED")   # restore_allowed=false
        self.assertEqual(ledger.observed_state(OBS["evidence_51#4"]), "EXPOSED")    # tombstone with email
        self.assertEqual(ledger.observed_state(OBS["evidence_51#3"]), "CLEAN")      # tombstone, user_id only
        self.assertEqual(ledger.observed_state(OBS["evidence_01#3"]), "ABSENT")     # 404 lookup

    def test_json_bundle_scope_counts_only_for_its_subject(self):
        scope = OBS["evidence_51#1"]  # empty customer_profiles in user_541/542's bundle
        self.assertEqual(ledger.scope_absences(scope, "account_5334", about_subject=False), [])
        self.assertEqual(ledger.scope_absences(scope, "account_4857", about_subject=True), ["customer_profiles"])

    def test_json_bundle_membership_uses_only_eligible_records(self):
        # case_31 (user_541): evidence_51's empty customer_profiles proves account_4857 absent only while
        # a record of the bundle tied to user_541 (evidence_51#3, #6) is eligible at the cutoff.
        step = {"resource_id": "account_4857", "system_id": "account_service", "operation": "VERIFY_RECORD_ABSENT"}
        cutoff, subject = "2026-09-15T12:00:00Z", {"user_541"}

        def bundle_absences(late):
            observations = [dict(o, observed_at="2026-12-01T00:00:00Z") if o["obs_id"] in late else o
                            for o in OBS.values()]
            index = ledger.Index(observations, [])
            entries, _, _ = ledger.state_evidence(index, step, cutoff, {"ABSENT"}, False, subject)
            return [e for e in entries if e["file_id"] == "evidence_51"]

        self.assertTrue(bundle_absences(set()))
        self.assertTrue(bundle_absences({"evidence_51#3"}))  # evidence_51#6 still ties the bundle to user_541
        self.assertEqual(bundle_absences({"evidence_51#3", "evidence_51#6"}), [])

    def test_recreated_record_contradicts_older_404(self):
        def obs(obs_id, kind, at, fields):
            return {"obs_id": obs_id, "file_id": obs_id.split("#")[0], "kind": kind, "resource_id": "account_1",
                    "related_resource_ids": [], "file_captured_at": at, "observed_at": at, "table": None,
                    "copy": None, "fields": fields, "subject_refs": [], "lookup_keys": [],
                    "identifiers": {"emails": fields.get("email", []), "phones": [], "email_hashes": [], "user_ids": []}}
        gone = obs("file_a#1", "absence", "2026-09-01T00:00:00Z", {"http_status": "404"})
        back = obs("file_b#1", "record", "2026-09-10T00:00:00Z", {"email": ["a@example.com"]})
        gone_again = obs("file_c#1", "absence", "2026-09-12T00:00:00Z", {"http_status": "404"})
        step = {"resource_id": "account_1", "system_id": "account_service", "operation": "VERIFY_RECORD_ABSENT"}
        needed, violation = ledger.needed_states(step, "COMPLETE", {})

        def check(observations):
            index = ledger.Index(observations, [])
            entries, _, supported = ledger.state_evidence(index, step, "2026-09-15T00:00:00Z", needed, violation, set())
            return supported, ledger.citation_candidates(entries, needed)

        self.assertEqual(check([gone, back]), (False, {}))
        supported, candidates = check([gone, back, gone_again])
        self.assertTrue(supported)
        self.assertEqual(sorted(candidates), ["file_c"])  # file_a's 404 was contradicted by file_b

        older = obs("file_d#1", "absence", "2026-09-11T00:00:00Z", {"http_status": "404"})
        _, candidates = check([gone, back, older, gone_again])
        self.assertEqual(sorted(candidates), ["file_c", "file_d"])  # corroborating 404s after the recreation

    def test_sql_scope_lists_replica_copies(self):
        scope = next(o for o in OBS.values() if o["file_id"] == "evidence_54" and o["kind"] == "scope")
        tables = ledger.scope_absences(scope, "account_004", about_subject=False)
        self.assertEqual(sorted(ledger.copy_key(t, None) for t in tables), ["failover_replica", "primary"])
        self.assertNotIn("customer_profiles_failover_replica", ledger.scope_absences(scope, "account_005", False))

    def test_ledger_explains_every_sample_step(self):
        rows, hits, _, window, _ = ledger.build()
        self.assertEqual(len(rows), 193)
        cited = [r for r in rows if r["evidence_id"]]
        self.assertTrue(all(r["cited_is_candidate"] and not r["cited_after_audit"] for r in cited))
        self.assertEqual(len(hits["acb"]), len(cited))
        self.assertEqual(window[1], [])  # no evidence contradicts the request-window hypothesis
        self.assertEqual(ledger.summarize(rows, hits, window)["identity_gaps"], [])

    def test_unresolved_subject_is_an_identity_gap(self):
        # case_01's email is user_01's snapshot email with no audit history: a snapshot updated after
        # audit_at hides it, so the case has no subject. The ledger must report it, not crash.
        row = identity.load_identities()["user_01"]
        original = row["updated_at"]
        row["updated_at"] = "2026-12-01T00:00:00Z"
        try:
            rows, hits, _, window, _ = ledger.build()
        finally:
            row["updated_at"] = original
        self.assertTrue(all(r["subject"] is None for r in rows if r["case_id"] == "case_01"))
        self.assertEqual(ledger.summarize(rows, hits, window)["identity_gaps"], ["case_01"])
        self.assertIn("## case_01 (COMPLETE, subject **identity gap", ledger.markdown(rows, ledger.summarize(rows, hits, window), []))


if __name__ == "__main__":
    unittest.main()
