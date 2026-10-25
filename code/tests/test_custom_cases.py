"""The custom evaluation cases must produce exactly their hand-written answers.

The cases live in code/evaluation/custom_cases/ and are run by code/evaluation/custom_cases.py on a
temporary dataset (keyword intent rules, no model call). Run from the repository root:
    python -m unittest discover -s code/tests
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import identity
import output
from evaluation import custom_cases


class CustomCaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = custom_cases.run_all()

    def test_every_case_folder_ran(self):
        self.assertGreaterEqual(len(self.cases), 3)  # the challenge requires at least three
        self.assertEqual(len(self.cases), len([p for p in custom_cases.CASES_DIR.iterdir() if p.is_dir()]))

    def test_each_case_matches_its_expected_answer(self):
        for case in self.cases:
            with self.subTest(case=case["name"]):
                self.assertEqual(case["predicted"], case["expected"])

    def test_expected_answers_obey_the_output_contract(self):
        # The hand-written answers must themselves pass the same validator as output.csv.
        for case in self.cases:
            for case_id, (status, steps) in case["expected"].items():
                with self.subTest(case=case_id):
                    self.assertEqual(output.validate_steps(status, steps), [])

    def test_real_dataset_is_served_again_afterwards(self):
        # The runner patches the dataset path and identity's caches; both must be back to normal.
        self.assertIn("user_01", identity.load_identities())
        self.assertNotIn("user_9101", identity.load_identities())


if __name__ == "__main__":
    unittest.main()
