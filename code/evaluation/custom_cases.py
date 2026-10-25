"""Custom evaluation cases (AGENTS.md 6.8, PLAN section 8): small invented scenarios with hand-written answers.

Each folder in code/evaluation/custom_cases/ is a tiny dataset in the same file layout as dataset/
(deletion_requests.csv, user_identities.csv, evidence_files.csv, evidence/, and optionally
user_audit_log.csv and system_activity.csv) plus expected.json, the answer written by hand from the
rules in AGENTS.md before the pipeline was ever run on it. Everything is fictional.

For each case the runner builds a temporary dataset (the case folder, header-only versions of the
optional files, and the real deletion_rules.csv), points the pipeline at it, runs the pipeline and
compares the result with expected.json using score() from code/evaluation/main.py. Intent is decided
by the frozen keyword rules (pipeline.run() without LLM context): no model call, no cache, no network.
The real dataset/ is only read (deletion_rules.csv) and never changed.

Run from the repository root:
    python code/evaluation/custom_cases.py [--out output/custom_scores.json]
Exit code 1 if any case does not match its expected answer.
"""

import argparse
import json
import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import activity  # noqa: E402
import evidence  # noqa: E402
import identity  # noqa: E402
import pipeline  # noqa: E402
import policy  # noqa: E402
from evaluation.main import print_summary, score  # noqa: E402

CASES_DIR = Path(__file__).resolve().parent / "custom_cases"
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Files the pipeline always reads; a case folder may leave these out and get a header-only file.
HEADERS = {
    "sample_deletion_requests.csv": "case_id,email,requested_at,reported_status,reported_at,audit_at,request_text",
    "user_audit_log.csv": "audit_id,user_id,event_time,field_name,old_value,new_value",
    "system_activity.csv": "activity_id,event_time,event_type,source_system,target_system,resource_id,reported_result,details",
}


@contextmanager
def dataset_at(path):
    """Point the pipeline at another dataset folder.

    evidence, identity, activity and policy each hold their own DATASET name (they import it with
    `from evidence import DATASET`), so all four are patched. identity caches what it loads and the
    caches are cleared on entry and exit so neither dataset leaks into the other.
    """
    def clear_caches():
        for cached in (identity.load_identities, identity.load_audit_log, identity.audit_by_user):
            cached.cache_clear()

    with patch.object(evidence, "DATASET", path), patch.object(identity, "DATASET", path), \
            patch.object(activity, "DATASET", path), patch.object(policy, "DATASET", path):
        clear_caches()
        try:
            yield
        finally:
            clear_caches()


def run_case(case_dir):
    """(expected, predicted, description) for one case folder, both as {case_id: (status, steps)}."""
    document = json.loads((case_dir / "expected.json").read_text(encoding="utf-8"))
    expected = {case_id: (e["deletion_status"], e["steps"]) for case_id, e in document["expected"].items()}
    with tempfile.TemporaryDirectory() as tmp:
        dataset = Path(tmp)
        shutil.copytree(case_dir, dataset, dirs_exist_ok=True, ignore=shutil.ignore_patterns("expected.json"))
        for name, header in HEADERS.items():
            if not (dataset / name).exists():
                (dataset / name).write_text(header + "\n", encoding="utf-8")
        shutil.copy(evidence.DATASET / "deletion_rules.csv", dataset / "deletion_rules.csv")
        with dataset_at(dataset):
            results, _trace = pipeline.run()
    return expected, {case_id: results[case_id] for case_id in expected}, document["description"]


def run_all():
    """[{name, description, expected, predicted, scores, passed}] for every case folder, by name."""
    cases = []
    for case_dir in sorted(p for p in CASES_DIR.iterdir() if p.is_dir()):
        expected, predicted, description = run_case(case_dir)
        scores = score(expected, predicted)
        cases.append({"name": case_dir.name, "description": description, "expected": expected,
                      "predicted": predicted, "scores": scores,
                      "passed": scores["status_accuracy"] == 1 and scores["sequence_accuracy"] == 1})
    return cases


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the custom evaluation cases.")
    parser.add_argument("--out", default=str(REPO_ROOT / "output" / "custom_scores.json"))
    args = parser.parse_args(argv)
    cases = run_all()
    for case in cases:
        s = case["scores"]
        print(f"{'PASS' if case['passed'] else 'FAIL'}  {case['name']}: "
              f"status {s['status_accuracy']:.0%}, steps {s['step_multiset']['matched']}/{s['step_multiset']['expected']} matched "
              f"({s['step_multiset']['predicted']} predicted), sequence {s['sequence_accuracy']:.0%}")
    expected = {cid: e for case in cases for cid, e in case["expected"].items()}
    predicted = {cid: p for case in cases for cid, p in case["predicted"].items()}
    overall = score(expected, predicted)
    print(f"\nAll custom cases together ({sum(c['passed'] for c in cases)} of {len(cases)} cases exact):")
    print_summary(overall)
    report = {"cases": [{"name": c["name"], "description": c["description"], "passed": c["passed"],
                         "scores": c["scores"]} for c in cases],
              "overall": overall}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nWrote {args.out}")
    return 0 if all(c["passed"] for c in cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
