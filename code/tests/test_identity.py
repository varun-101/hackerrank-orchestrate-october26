"""Tests for identity resolution. Run from the repository root: python -m unittest discover -s code/tests"""

import sys
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import identity  # noqa: E402


class IdentityTests(unittest.TestCase):
    def test_current_email_resolves(self):
        self.assertEqual(identity.resolve_user("Leonie.G9352@outlook.com", "2026-09-15T12:00:00Z"), "user_17")

    def test_historical_email_two_changes_back_resolves(self):
        # user_10: nadia.ionescu -> nadia.petrescu (audit_121) -> n.petrescu (audit_238)
        self.assertEqual(identity.resolve_user("nadia.ionescu@yahoo.com", "2026-09-18T12:25:00Z"), "user_10")
        ids = identity.subject_identifiers("user_10", "2026-09-18T12:25:00Z")
        self.assertEqual(ids["emails"], ["n.petrescu@yahoo.com", "nadia.ionescu@yahoo.com", "nadia.petrescu@yahoo.com"])
        self.assertEqual(len(ids["email_hashes"]), 3)
        self.assertEqual(ids["user_ids"], ["user_10"])
        self.assertEqual(ids["phones"], ["+40723481638"])

    def test_audit_rows_after_cutoff_are_ignored(self):
        # user_29 changed email on 2026-05-18 (audit_284); its snapshot is from that change too, so at
        # 2026-01-01 neither the old email (only in the later audit row) nor the snapshot is known.
        self.assertIsNone(identity.resolve_user("noah.f438085@outlook.com", "2026-01-01T00:00:00Z"))
        self.assertIsNone(identity.resolve_user("noah.f4380@outlook.com", "2026-01-01T00:00:00Z"))
        ids = identity.subject_identifiers("user_29", "2026-01-01T00:00:00Z")
        self.assertEqual(ids["emails"], [])
        self.assertEqual(identity.unresolved_kinds("user_29", "2026-01-01T00:00:00Z"),
                         ["emails", "phones", "email_hashes"])
        self.assertEqual(identity.unresolved_kinds("user_29", "2026-09-15T12:00:00Z"), [])
        self.assertEqual(identity.audit_ids("user_29", "2026-01-01T00:00:00Z"), [])
        self.assertEqual(identity.audit_ids("user_29", "2026-09-15T12:00:00Z"), ["audit_284", "audit_285"])

    def test_future_snapshot_values_are_not_known_at_cutoff(self):
        # In-memory December snapshot: user_10 moved to a new email after the September cutoff.
        future = dict(identity.load_identities())
        future["user_10"] = dict(future["user_10"], email="nadia.new@example.com", phone="+40 700 000 000",
                                 updated_at="2026-12-01T00:00:00Z")
        cutoff = "2026-09-18T12:25:00Z"
        with mock.patch.object(identity, "load_identities", return_value=future):
            self.assertIsNone(identity.resolve_user("nadia.new@example.com", cutoff))
            # The value at cutoff is reconstructed from eligible audit rows (audit_238's new_value).
            self.assertEqual(identity.resolve_user("n.petrescu@yahoo.com", cutoff), "user_10")
            ids = identity.subject_identifiers("user_10", cutoff)
            self.assertEqual(ids["emails"], ["n.petrescu@yahoo.com", "nadia.ionescu@yahoo.com", "nadia.petrescu@yahoo.com"])
            self.assertEqual(ids["phones"], [])  # phone never in an eligible audit row
            self.assertEqual(identity.unresolved_kinds("user_10", cutoff), ["phones"])
            # After the snapshot time the new values are known.
            self.assertIn("nadia.new@example.com", identity.subject_identifiers("user_10", "2026-12-02T00:00:00Z")["emails"])

    def test_unknown_email(self):
        self.assertIsNone(identity.resolve_user("nobody@example.com", "2026-09-15T12:00:00Z"))

    def test_date_only_times_compare(self):
        self.assertTrue(identity.at_or_before("2026-09-05", "2026-09-05T00:00:00Z"))
        self.assertFalse(identity.at_or_before("2026-09-06", "2026-09-05T23:59:59Z"))


if __name__ == "__main__":
    unittest.main()
