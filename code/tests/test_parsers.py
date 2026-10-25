"""Tests for evidence parsing, activity parsing and the trace recorder.

Run from the repository root: python -m unittest discover -s code/tests
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import activity  # noqa: E402
import evidence  # noqa: E402
import run_trace  # noqa: E402

MANIFEST = {row["file_id"]: row for row in evidence.load_manifest()}


def observations(file_id):
    return evidence.load_file(MANIFEST[file_id])[0]


class EvidenceTests(unittest.TestCase):
    def test_empty_account_collection_is_an_absence_scope(self):
        # evidence_51: account_service customer_profiles has records: []
        scopes = [o for o in observations("evidence_51")
                  if o["kind"] == "scope" and o["system"] == "account_service"]
        self.assertEqual(len(scopes), 1)
        self.assertEqual(scopes[0]["tables"], {"customer_profiles": []})
        self.assertEqual(scopes[0]["observed_at"], "2026-09-15T12:00:00Z")

    def test_replica_row_is_a_separate_copy_from_primary(self):
        # evidence_54: account_005 exists only in the failover replica table
        obs = observations("evidence_54")
        copies = [o for o in obs if o.get("resource_id") == "account_005"]
        self.assertEqual(len(copies), 1)
        self.assertEqual(copies[0]["copy"], "failover_replica")
        self.assertEqual(copies[0]["table"], "customer_profiles_failover_replica")
        self.assertIn("user_05", copies[0]["identifiers"]["user_ids"])
        self.assertIn("customer_profiles_failover_replica", copies[0]["source"])
        scope = next(o for o in obs if o["kind"] == "scope")
        self.assertIn("account_005", scope["tables"]["customer_profiles_failover_replica"])
        self.assertNotIn("account_005", scope["tables"]["customer_profiles_primary"])

    def test_inventory_scope_without_rows_is_recorded(self):
        # evidence_90: snapshot_scope covers account_service, customer_profiles is empty
        scopes = [o for o in observations("evidence_90") if o["kind"] == "scope"]
        self.assertEqual([(s["system"], s["tables"]) for s in scopes],
                         [("account_service", {"customer_profiles": []})])

    def test_search_wrapper_and_nested_document_are_merged(self):
        # evidence_92: index query wrapper and its hit document share resource_id search_1993
        matches = [o for o in observations("evidence_92") if o.get("resource_id") == "search_1993"]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["system"], "search_service")
        self.assertIn("noah.f438085@outlook.com", matches[0]["identifiers"]["emails"])

    def test_404_profile_lookup_is_an_absence(self):
        match = next(o for o in observations("evidence_92") if o.get("resource_id") == "account_5684")
        self.assertEqual(match["kind"], "absence")

    def test_log_trace_is_one_observation(self):
        # evidence_67: profile_lookup_started + profile_lookup_completed (404) in trace rb-061801-001
        obs = observations("evidence_67")
        self.assertEqual(len(obs), 1)
        only = obs[0]
        self.assertEqual(only["resource_id"], "account_6523")
        self.assertEqual(only["kind"], "absence")
        self.assertEqual(only["fields"]["events"], ["profile_lookup_started", "profile_lookup_completed"])
        self.assertEqual(only["source"].count("\n"), 1)
        # The email was only the lookup key, so it is not an identifier of the record.
        self.assertEqual(only["lookup_keys"], ["leonie.g9352@outlook.com"])
        self.assertEqual(only["identifiers"]["emails"], [])

    def test_cache_miss_and_retry_lines_do_not_make_absence(self):
        # evidence_34 trace tr-7722221 has dependency_retry lines but ends in a search hit
        match = next(o for o in observations("evidence_34") if o.get("fields", {}).get("trace_id") == "tr-7722221")
        self.assertEqual(match["kind"], "record")
        self.assertEqual(match["fields"]["result"], "hit")

    def test_all_log_lines_parse(self):
        for file_id, row in MANIFEST.items():
            if row["file_path"].endswith(".log"):
                stats = evidence.load_file(row)[1]
                self.assertEqual(stats["unparsed_lines"], 0, file_id)

    # Review round 1, finding 1
    def test_absence_keeps_explicit_subject_reference(self):
        # evidence_13: account_1084 NOT_FOUND with subject_reference mateo.alvarez@gmail.com
        match = next(o for o in observations("evidence_13") if o["resource_id"] == "account_1084")
        self.assertEqual(match["kind"], "absence")
        self.assertEqual(match["subject_refs"], ["mateo.alvarez@gmail.com"])
        self.assertEqual(match["lookup_keys"], [])
        self.assertEqual(match["identifiers"]["emails"], [])

    def test_lookup_only_absence_has_no_subject_reference(self):
        only = observations("evidence_67")[0]
        self.assertEqual(only["subject_refs"], [])
        self.assertEqual(only["lookup_keys"], ["leonie.g9352@outlook.com"])

    # Finding 2
    def test_request_line_identifiers_are_not_record_content(self):
        # evidence_01 trace tr-4614434: subject_ref=user_16 is only on audit_record_query_started
        match = next(o for o in observations("evidence_01") if o["resource_id"] == "audit_record_2857")
        self.assertEqual(match["identifiers"]["user_ids"], [])
        self.assertEqual(match["subject_refs"], [])
        self.assertEqual(match["lookup_keys"], ["user_16"])
        self.assertEqual(match["fields"]["request"]["subject_ref"], "user_16")
        self.assertNotIn("subject_ref", match["fields"])

    def test_result_line_subject_ref_is_record_content(self):
        # evidence_44 tr-1580266: message_read carries resource_id and subject_ref on the same line
        match = next(o for o in observations("evidence_44") if o["resource_id"] == "support_2659")
        self.assertEqual(match["subject_refs"], ["user_461"])
        self.assertEqual(match["identifiers"]["user_ids"], ["user_461"])

    # Finding 3
    def test_every_log_format_keeps_its_trace_id(self):
        expected = {"evidence_11": "tr-4973247",  # syslog
                    "evidence_16": "tr-6074163",  # brackets
                    "evidence_25": "tr-1108860",  # cloud
                    "evidence_21": "tr-8242693",  # pipe
                    "evidence_34": "tr-7722221"}  # json
        for file_id, trace_id in expected.items():
            obs = observations(file_id)
            self.assertTrue(all(o["fields"].get("trace_id") for o in obs), file_id)
            self.assertIn(trace_id, {o["fields"]["trace_id"] for o in obs}, file_id)

    def test_lines_without_trace_get_no_synthetic_trace_id(self):
        # evidence_41 (notification delivery) has no trace column at all
        obs = observations("evidence_41")
        self.assertTrue(obs)
        self.assertFalse(any("trace_id" in o["fields"] for o in obs))
        self.assertFalse(any(str(v).startswith("line-") for o in obs for v in o["fields"].values()))

    # Finding 4
    def test_pdf_labeled_times_and_aliases(self):
        journal = observations("evidence_55")[0]
        self.assertEqual(journal["resource_id"], "audit_record_003")
        self.assertEqual(journal["fields"]["times"], {"issued": "2026-09-05T06:30:00Z",
                                                      "recorded": "2026-08-25T04:03:00Z",
                                                      "retain_until": "2027-08-25T04:03:00Z"})
        self.assertEqual(journal["observed_at"], "2026-09-05T06:30:00Z")
        self.assertEqual(journal["subject_refs"], ["user_03"])
        attachment = observations("evidence_58")[0]
        self.assertEqual(attachment["fields"]["times"]["submitted"], "2026-06-18T10:24:00Z")
        self.assertEqual(attachment["observed_at"], "2026-09-12T07:30:00Z")  # captured
        self.assertEqual(attachment["fields"]["aliases"],
                         ["CS-4827", "ORD-20264827", "delivery_address_correction.pdf"])
        statement = observations("evidence_18")[0]
        self.assertEqual(statement["fields"]["aliases"], ["NST-2026-0718-1842", "ORD-20261260"])
        self.assertIsNone(statement["observed_at"])  # no as-of label; the manifest time applies

    def test_pdf_register_rows_keep_document_context(self):
        rows = observations("evidence_27")
        self.assertEqual(len(rows), 6)
        first = rows[0]
        self.assertEqual(first["fields"]["document"]["title"], "DISPUTED ORDERS AND TAX RECORDS REGISTER")
        self.assertIn("Finance Operations | May 2026", first["fields"]["document"]["context"])
        self.assertEqual(first["observed_at"], "2026-05-29T08:00:00Z")  # generated
        self.assertEqual(first["fields"]["times"], {"retain_until": "2033-05-04"})
        self.assertEqual(first["fields"]["aliases"], ["ORD-20264118"])
        self.assertIsNone(first["resource_id"])  # the register names no legal_hold_* id
        self.assertTrue(first["source"].startswith("DISPUTED ORDERS AND TAX RECORDS REGISTER\n"))

    # System alias normalization
    def test_profile_service_is_normalized_to_account_service(self):
        obs = [o for o in observations("evidence_53") if o["raw_system"] == "profile_service"]
        self.assertTrue(obs)
        self.assertTrue(all(o["system"] == "account_service" and o["copy"] is None for o in obs))
        # Grounding: every profile_service activity row targets account_service.
        targets = {r["target_system"] for r in activity.load_all_activity() if r["source_system"] == "profile_service"}
        self.assertEqual(targets, {"account_service"})

    def test_support_replica_stays_a_separate_copy(self):
        match = next(o for o in observations("evidence_61") if o["resource_id"] == "support_002")
        self.assertEqual((match["system"], match["raw_system"], match["copy"]),
                         ("support_platform", "support_replica", "support_replica"))

    def test_ungrounded_service_stays_raw(self):
        match = next(o for o in observations("evidence_61") if o["resource_id"] == "support_7731")
        self.assertEqual((match["system"], match["raw_system"]), ("attachment_preview", "attachment_preview"))

    # State over time and copies
    def test_later_contradictory_observations_are_both_kept(self):
        # account_2420: 404 in evidence_68 (Mar 20), present in evidence_40 (Mar 22)
        seen = [(o["file_id"], o["kind"], o["observed_at"])
                for file_id in ("evidence_68", "evidence_40") for o in observations(file_id)
                if o.get("resource_id") == "account_2420"]
        self.assertIn(("evidence_68", "absence", "2026-03-20T06:07:21Z"), seen)
        self.assertIn(("evidence_40", "record", "2026-03-22T08:00:00Z"), seen)

    def test_primary_and_replica_copies_with_different_state_are_kept_apart(self):
        sql = """-- Captured at: 2026-09-02T23:00:00Z
CREATE TABLE customer_profiles_primary (resource_id TEXT PRIMARY KEY, email TEXT, updated_at TEXT);
CREATE TABLE customer_profiles_failover_replica (resource_id TEXT PRIMARY KEY, email TEXT, updated_at TEXT);
INSERT INTO "customer_profiles_primary" VALUES('account_1',NULL,'2026-09-02T10:00:00Z');
INSERT INTO "customer_profiles_failover_replica" VALUES('account_1','a.b@example.com','2026-09-01T10:00:00Z');
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.sql"
            path.write_text(sql, encoding="utf-8")
            file = {"file_id": "test_sql", "file_path": "x.sql", "origin": "test", "captured_at": "2026-09-02T23:00:00Z"}
            obs = [o for o in evidence.parse_sql(file, path)[0] if o["kind"] == "record"]
        by_copy = {o["copy"]: o for o in obs}
        self.assertEqual(set(by_copy), {"primary", "failover_replica"})
        self.assertEqual(by_copy["primary"]["identifiers"]["emails"], [])
        self.assertEqual(by_copy["primary"]["observed_at"], "2026-09-02T10:00:00Z")
        self.assertEqual(by_copy["failover_replica"]["identifiers"]["emails"], ["a.b@example.com"])
        self.assertEqual(by_copy["failover_replica"]["observed_at"], "2026-09-01T10:00:00Z")


class ActivityTests(unittest.TestCase):
    rows = activity.load_activity()

    def test_sorted_by_event_time(self):
        times = [r["event_time"] for r in self.rows]
        self.assertEqual(times, sorted(times))
        ids = [r["activity_id"] for r in self.rows]
        # activity_4011 (2026-03-10) sits among March rows, not at the end
        self.assertLess(ids.index("activity_4011"), ids.index("activity_4000"))
        self.assertNotEqual(ids[-1], "activity_4011")

    def test_data_catalog_link_details(self):
        row = next(r for r in self.rows if r["activity_id"] == "activity_4000")
        self.assertEqual(row["resource_id"], "account_5762")
        self.assertEqual(row["emails"], ["haruka.s1838@zoho.com"])
        self.assertEqual(row["user_ids"], ["user_11"])
        self.assertFalse(row["exclusive_owner"])

    def test_exclusive_owner_and_downstream(self):
        attachment = next(r for r in self.rows if r["activity_id"] == "activity_4007")
        self.assertTrue(attachment["exclusive_owner"])
        self.assertEqual(attachment["link_alias"], "delivery_confirmation_19.pdf")
        restore = next(r for r in self.rows if r["activity_id"] == "activity_3387")
        self.assertEqual(restore["downstream_resource_id"], "search_4882")

    # Finding 5
    def test_classify_request_rows_are_not_pipeline_input(self):
        self.assertFalse(any(r["event_type"] == "CLASSIFY_REQUEST" for r in self.rows))
        offline = activity.load_all_activity()
        self.assertEqual(sum(r["event_type"] == "CLASSIFY_REQUEST" for r in offline), 10)
        self.assertEqual(len(offline), len(self.rows) + 10)


class TraceTests(unittest.TestCase):
    def test_record_and_write(self):
        t = run_trace.new_trace()
        obs = observations("evidence_67")[0]
        run_trace.record(t, "case_01", "evidence", outputs={"n": 1}, evidence=[run_trace.evidence_ref(obs)])
        run_trace.record(t, "case_01", "identity")
        events = t["cases"]["case_01"]
        self.assertEqual([e["order"] for e in events], [1, 2])
        self.assertEqual(events[0]["evidence"][0]["id"], "evidence_67")
        with tempfile.TemporaryDirectory() as tmp:
            path = run_trace.write_trace(t, Path(tmp) / "trace.json")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["cases"]["case_01"][1]["stage"], "identity")

    # Finding 6
    def test_falsy_values_are_recorded_as_given(self):
        t = run_trace.new_trace()
        run_trace.record(t, "c", "a", inputs=[], outputs=False)
        run_trace.record(t, "c", "b", outputs=0)
        run_trace.record(t, "c", "c")
        events = t["cases"]["c"]
        self.assertEqual((events[0]["inputs"], events[0]["outputs"]), ([], False))
        self.assertEqual(events[1]["outputs"], 0)
        self.assertEqual((events[2]["inputs"], events[2]["outputs"]), ({}, {}))

    def test_recorded_event_is_a_snapshot(self):
        t = run_trace.new_trace()
        outputs, extra = {"steps": [1]}, {"attempts": [{"n": 1}]}
        run_trace.record(t, "c", "a", outputs=outputs, llm=extra)
        outputs["steps"].append(2)
        extra["attempts"][0]["n"] = 99
        event = t["cases"]["c"][0]
        self.assertEqual(event["outputs"], {"steps": [1]})
        self.assertEqual(event["llm"], {"attempts": [{"n": 1}]})


if __name__ == "__main__":
    unittest.main()
