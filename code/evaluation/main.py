"""Sample scoring script (PLAN section 8): compare predictions with dataset/sample_output.csv.

Evaluation tooling only; the pipeline never reads sample_output.csv.

Run from the repository root:
    python code/evaluation/main.py [predictions.csv] [--expected dataset/sample_output.csv]
Default predictions: output/sample_predictions.csv. Writes output/sample_scores.json.

Metric definitions (used verbatim in evaluation_report.md). Every expected case is scored; a case
missing from the predictions counts as status MISSING with no steps and earns no credit in any
metric (a missing NOT_APPLICABLE case does not match []). Predicted cases that are not in the
expected file are counted and otherwise ignored.

- Status accuracy: cases whose predicted deletion_status equals the expected one, divided by the
  number of expected cases. The confusion table counts (expected, predicted) status pairs.
- Step key: a step's (phase, system_id, resource_id, operation, state, evidence_id), i.e. every
  field except step_no, which only encodes position.
- Step-level accuracy, multiset: per case, matched = size of the multiset intersection of expected
  and predicted step keys. Summed over cases: precision = matched / predicted steps,
  recall = matched / expected steps, accuracy = matched / sum over cases of
  max(expected steps, predicted steps). Order does not matter.
- Step-level accuracy, positional: predicted step i equals expected step i in all 7 fields
  (including step_no), divided by the sum over cases of max(expected steps, predicted steps).
  Order matters.
- Field-level accuracy, per field, on aligned steps: per case, each expected step is paired with
  the first unpaired predicted step that has the same (phase, operation, system_id, resource_id);
  the expected and predicted steps left over are then paired by position among themselves. A field
  is matched when a pair has equal values for it; matches are summed over cases and divided by the
  sum of max(expected steps, predicted steps). Values stay tied to their record, so citations
  swapped between two records count as two evidence_id misses, while a reordered but otherwise
  correct plan keeps 100%.
- Sequence accuracy: predicted cases whose parsed steps_json array equals the expected array
  exactly (same steps, same order, same step_no; object key order and whitespace ignored), divided
  by the number of expected cases. NOT_APPLICABLE cases match when both arrays are [].
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import output  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
STATUSES = ["COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"]
FIELDS = ["phase", "system_id", "resource_id", "operation", "state", "evidence_id"]
ALIGN_FIELDS = ["phase", "operation", "system_id", "resource_id"]


def step_key(step):
    return tuple(step.get(field) for field in FIELDS)


def load(path):
    """{case_id: (status, steps)} from an output-format CSV."""
    return {row["case_id"]: (row["deletion_status"], json.loads(row["steps_json"])) for row in output.read_rows(path)}


def ratio(numerator, denominator):
    return numerator / denominator if denominator else 1.0


def align(expected_steps, predicted_steps):
    """(expected, predicted) step pairs: same record and operation first, leftovers by position."""
    pairs, unpaired = [], list(predicted_steps)
    leftover = []
    for e in expected_steps:
        key = [e.get(f) for f in ALIGN_FIELDS]
        match = next((p for p in unpaired if [p.get(f) for f in ALIGN_FIELDS] == key), None)
        if match is None:
            leftover.append(e)
        else:
            pairs.append((e, match))
            unpaired.remove(match)
    return pairs + list(zip(leftover, unpaired))


def case_diff(expected_steps, predicted_steps):
    expected_keys = Counter(step_key(s) for s in expected_steps)
    predicted_keys = Counter(step_key(s) for s in predicted_steps)
    positional = []
    for i in range(min(len(expected_steps), len(predicted_steps))):
        fields = [f for f in ["step_no"] + FIELDS if expected_steps[i].get(f) != predicted_steps[i].get(f)]
        if fields:
            positional.append({"position": i + 1, "fields": fields,
                               "expected": expected_steps[i], "predicted": predicted_steps[i]})
    return {
        "missing_steps": [dict(zip(FIELDS, k)) for k in (expected_keys - predicted_keys).elements()],
        "extra_steps": [dict(zip(FIELDS, k)) for k in (predicted_keys - expected_keys).elements()],
        "positional_mismatches": positional,
    }


def score(expected, predicted):
    """expected, predicted: {case_id: (status, steps)}. Returns the metrics dict described above."""
    confusion = Counter()
    status_correct = sequence_correct = 0
    step_matched = expected_total = predicted_total = slots = positional_matched = 0
    field_matched = Counter()
    cases = []
    # hook: to split step metrics by link type (proven / inferred), score each subset of steps
    # separately with these same counts; nothing else needs to change.
    for case_id in sorted(expected, key=output.case_number):
        expected_status, expected_steps = expected[case_id]
        predicted_status, predicted_steps = predicted.get(case_id, ("MISSING", []))
        confusion[(expected_status, predicted_status)] += 1
        status_correct += expected_status == predicted_status
        sequence_correct += case_id in predicted and expected_steps == predicted_steps

        case_slots = max(len(expected_steps), len(predicted_steps))
        slots += case_slots
        expected_total += len(expected_steps)
        predicted_total += len(predicted_steps)
        matched = sum((Counter(map(step_key, expected_steps)) & Counter(map(step_key, predicted_steps))).values())
        step_matched += matched
        positional_matched += sum(e == p for e, p in zip(expected_steps, predicted_steps))
        for e, p in align(expected_steps, predicted_steps):
            for field in FIELDS:
                field_matched[field] += e.get(field) == p.get(field)

        if expected_status != predicted_status or expected_steps != predicted_steps:
            cases.append({"case_id": case_id, "expected_status": expected_status,
                          "predicted_status": predicted_status, "expected_steps": len(expected_steps),
                          "predicted_steps": len(predicted_steps), "matched_steps": matched,
                          **case_diff(expected_steps, predicted_steps)})

    total = len(expected)
    return {
        "cases": total,
        "extra_predicted_cases": len(set(predicted) - set(expected)),
        "status_accuracy": ratio(status_correct, total),
        "confusion": {e: {p: confusion[(e, p)] for p in STATUSES + ["MISSING"]} for e in STATUSES},
        "step_multiset": {"matched": step_matched, "expected": expected_total, "predicted": predicted_total,
                          "precision": ratio(step_matched, predicted_total),
                          "recall": ratio(step_matched, expected_total),
                          "accuracy": ratio(step_matched, slots)},
        "step_positional_accuracy": ratio(positional_matched, slots),
        "field_accuracy": {field: ratio(field_matched[field], slots) for field in FIELDS},
        "sequence_accuracy": ratio(sequence_correct, total),
        "exact_cases": total - len(cases),  # status and steps both equal
        "mismatched_cases": cases,
    }


def sample_analytics(expected, predicted, trace):
    """Stage coverage and link-method scores; expected labels are used only here, after prediction.

    'Unlinked' means there is no eligible pipeline link, not that inference would be correct.
    Split sequence metrics preserve relative order, renumber the filtered steps and omit cases empty on both sides.
    Resource agreement measures agreement with the sample's resource list, not hidden truth.
    """
    stages = {cid: {e["stage"]: e for e in trace["cases"][cid]} for cid in expected}
    links = {cid: events["steps"]["outputs"].get("resource_links", {}) for cid, events in stages.items()}
    groups = {}
    for kind in ("proven", "lookup", "inferred", "unlinked"):
        actual, wanted = {}, {}
        for cid, (status, steps) in expected.items():
            e = [s for s in steps if links[cid].get(s["resource_id"], "unlinked") == kind]
            p = [s for s in predicted[cid][1] if links[cid].get(s["resource_id"], "unlinked") == kind]
            if e or p:
                wanted[cid] = (status, [dict(s, step_no=n) for n, s in enumerate(e, 1)])
                actual[cid] = (predicted[cid][0], [dict(s, step_no=n) for n, s in enumerate(p, 1)])
        split = score(wanted, actual) if wanted else None
        groups[kind] = ({k: split[k] for k in ("cases", "step_multiset", "step_positional_accuracy",
                                               "field_accuracy", "sequence_accuracy")} if split else None)

    resources_expected = resources_found = resources_matched = 0
    citation_expected = citation_matched = 0
    per_case = {}
    for cid, (_, steps) in expected.items():
        wanted = {s["resource_id"] for s in steps}
        found = set(links[cid])
        resources_expected += len(wanted)
        resources_found += len(found)
        resources_matched += len(wanted & found)
        causes = []
        missing = sorted(wanted - found)
        if missing:
            causes.append("No eligible proven/lookup link: " + ", ".join(missing))
        gaps = stages[cid]["steps"]["outputs"].get("gaps", [])
        causes.extend(gaps)
        diff = case_diff(steps, predicted[cid][1])
        if diff["extra_steps"]:
            causes.append("Linked resource state, policy or citation differs; inspect state/policy trace.")
        per_case[cid] = {"expected_resources": len(wanted), "linked_resources": len(found),
                         "matched_resources": len(wanted & found), "missing_resources": missing,
                         "extra_resources": sorted(found - wanted), "likely_causes": causes}
        for e in steps:
            if not e["evidence_id"]:
                continue
            citation_expected += 1
            citation_matched += any(all(e[f] == p[f] for f in ALIGN_FIELDS + ["evidence_id"])
                                    for p in predicted[cid][1])
    linking = [events.get("linking", {}).get("outputs", {}) for events in stages.values()]
    return {
        "scope": "32 public samples only; split metrics use eligible pipeline link provenance",
        "by_link_type": groups,
        "identity_resolved": sum(bool(e.get("identity", {}).get("outputs", {}).get("user_id"))
                                 for e in stages.values()),
        "resources": {"expected": resources_expected, "linked": resources_found, "matched": resources_matched,
                      "sample_resource_agreement": ratio(resources_matched, resources_found)},
        "links_by_method": dict(Counter(kind for mapping in links.values() for kind in mapping.values())),
        "links_excluded_after_cutoff": sum(len(e.get("excluded_after_cutoff", [])) for e in linking),
        "links_assigned_to_other_requests": sum(len(e.get("moved_to_other_requests", [])) for e in linking),
        "ambiguous_inferred_links": 0,
        "unmatched_expected_resources": resources_expected - resources_matched,
        "citations": {"matched": citation_matched, "expected": citation_expected,
                      "accuracy": ratio(citation_matched, citation_expected)},
        "llm_usage": {"calls": 0, "attempts": 0, "input_tokens": 0, "output_tokens": 0,
                      "cache_hits": 0, "failures": 0, "batch_wall_seconds": 0},
        "per_case": per_case,
    }


def print_summary(scores):
    pct = lambda x: f"{100 * x:.1f}%"  # noqa: E731
    print(f"Cases: {scores['cases']} (exact {scores['exact_cases']}), extra predicted cases: {scores['extra_predicted_cases']}")
    print(f"Status accuracy:   {pct(scores['status_accuracy'])}")
    print("Confusion (rows expected, columns predicted):")
    columns = STATUSES + ["MISSING"]
    print("  " + " " * 16 + "".join(f"{c[:14]:>16}" for c in columns))
    for e in STATUSES:
        print(f"  {e:<16}" + "".join(f"{scores['confusion'][e][p]:>16}" for p in columns))
    m = scores["step_multiset"]
    print(f"Steps (multiset):  accuracy {pct(m['accuracy'])}, precision {pct(m['precision'])}, "
          f"recall {pct(m['recall'])} ({m['matched']} matched, {m['expected']} expected, {m['predicted']} predicted)")
    print(f"Steps (position):  {pct(scores['step_positional_accuracy'])}")
    print("Fields:            " + ", ".join(f"{f} {pct(v)}" for f, v in scores["field_accuracy"].items()))
    print(f"Sequence accuracy: {pct(scores['sequence_accuracy'])}")
    if "analytics" in scores:
        a = scores["analytics"]
        print(f"Resources:         {a['resources']['matched']}/{a['resources']['expected']} expected resources linked; "
              f"methods {a['links_by_method']}")
        for kind, split in a["by_link_type"].items():
            print(f"  {kind}: " + (f"step accuracy {pct(split['step_multiset']['accuracy'])}, "
                                    f"sequence {pct(split['sequence_accuracy'])}" if split else "not used"))
    for case in scores["mismatched_cases"]:
        print(f"\n{case['case_id']}: status {case['expected_status']} -> {case['predicted_status']}, "
              f"steps {case['matched_steps']}/{case['expected_steps']} matched, {case['predicted_steps']} predicted")
        for step in case["missing_steps"]:
            print("  missing  " + json.dumps(step, sort_keys=True))
        for step in case["extra_steps"]:
            print("  extra    " + json.dumps(step, sort_keys=True))
        for mismatch in case["positional_mismatches"]:
            print(f"  position {mismatch['position']}: {', '.join(mismatch['fields'])} differ")
        if "analytics" in scores:
            for cause in scores["analytics"]["per_case"][case["case_id"]]["likely_causes"]:
                print("  cause    " + cause)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Score predictions against the sample answers.")
    parser.add_argument("predictions", nargs="?", default=str(REPO_ROOT / "output" / "sample_predictions.csv"))
    parser.add_argument("--expected", default=str(REPO_ROOT / "dataset" / "sample_output.csv"))
    parser.add_argument("--out", default=str(REPO_ROOT / "output" / "sample_scores.json"))
    args = parser.parse_args(argv)
    scores = score(load(args.expected), load(args.predictions))
    print_summary(scores)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(scores, f, indent=2, sort_keys=True)
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
