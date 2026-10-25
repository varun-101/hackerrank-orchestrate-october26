"""Step building, ordering, serialization and validation for output.csv (PLAN section 5.9).

Order rule (problem_statement.md "Rules"): phase (FINDING, REMEDIATION, VERIFICATION), then the
operation order of the "Allowed Operations" table, then system_id, then resource_id ascending.
All 193 steps of dataset/sample_output.csv follow it. evidence_id is a final tie-break so the
output does not depend on input order; the validator checks only the four required fields.

Validate a file from the repository root:
    python code/output.py output.csv --requests dataset/deletion_requests.csv
"""

import argparse
import csv
import json
import sys

PHASES = ["FINDING", "REMEDIATION", "VERIFICATION"]

# (operation, phase, state) in the order of the problem-statement table.
OPERATIONS = [
    ("REPORT_REMAINING_RECORD", "FINDING", "PRESENT"),
    ("REPORT_EXPOSED_PII", "FINDING", "PRESENT"),
    ("REPORT_ACTIVE_DATA_FLOW", "FINDING", "ACTIVE"),
    ("REPORT_RESTORE_RISK", "FINDING", "ACTIVE"),
    ("DISABLE_DATA_FLOW", "REMEDIATION", "DISABLED"),
    ("BLOCK_RESTORE", "REMEDIATION", "BLOCKED"),
    ("DELETE_RECORD", "REMEDIATION", "ABSENT"),
    ("REDACT_RECORD", "REMEDIATION", "REDACTED"),
    ("ANONYMIZE_RECORD", "REMEDIATION", "ANONYMIZED"),
    ("VERIFY_RECORD_ABSENT", "VERIFICATION", "ABSENT"),
    ("VERIFY_RECORD_PRESENT", "VERIFICATION", "PRESENT"),
    ("VERIFY_RECORD_REDACTED", "VERIFICATION", "REDACTED"),
    ("VERIFY_RECORD_ANONYMIZED", "VERIFICATION", "ANONYMIZED"),
    ("VERIFY_DATA_FLOW_DISABLED", "VERIFICATION", "DISABLED"),
    ("VERIFY_RESTORE_BLOCKED", "VERIFICATION", "BLOCKED"),
]
OPERATION_INDEX = {op: i for i, (op, _, _) in enumerate(OPERATIONS)}
OPERATION_PHASE = {op: phase for op, phase, _ in OPERATIONS}
OPERATION_STATE = {op: state for op, _, state in OPERATIONS}

# Each finding needs one of these remediations on the same record, and each remediation its check.
FINDING_FIXES = {
    "REPORT_REMAINING_RECORD": {"DELETE_RECORD"},
    "REPORT_EXPOSED_PII": {"REDACT_RECORD", "ANONYMIZE_RECORD"},
    "REPORT_ACTIVE_DATA_FLOW": {"DISABLE_DATA_FLOW"},
    "REPORT_RESTORE_RISK": {"BLOCK_RESTORE"},
}
FIX_CHECK = {
    "DELETE_RECORD": "VERIFY_RECORD_ABSENT",
    "REDACT_RECORD": "VERIFY_RECORD_REDACTED",
    "ANONYMIZE_RECORD": "VERIFY_RECORD_ANONYMIZED",
    "DISABLE_DATA_FLOW": "VERIFY_DATA_FLOW_DISABLED",
    "BLOCK_RESTORE": "VERIFY_RESTORE_BLOCKED",
}

STEP_FIELDS = ["step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"]
STATUSES = {"COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"}
COLUMNS = ["case_id", "deletion_status", "steps_json"]


def make_step(operation, system_id, resource_id, evidence_id=""):
    """A step with exactly the 7 contract fields. Phase and state follow from the operation."""
    if operation not in OPERATION_INDEX:
        raise ValueError(f"unknown operation {operation!r}")
    return {
        "step_no": 0,  # set by sort_steps
        "phase": OPERATION_PHASE[operation],
        "system_id": system_id,
        "resource_id": resource_id,
        "operation": operation,
        "state": OPERATION_STATE[operation],
        "evidence_id": evidence_id,
    }


def step_sort_key(step):
    return (PHASES.index(step["phase"]), OPERATION_INDEX[step["operation"]], step["system_id"], step["resource_id"],
            step["evidence_id"])


def sort_steps(steps):
    """Return copies of the steps in contract order, numbered from 1."""
    ordered = [dict(step) for step in sorted(steps, key=step_sort_key)]
    for number, step in enumerate(ordered, start=1):
        step["step_no"] = number
    return ordered


def serialize_steps(steps):
    """Deterministic compact JSON with sorted keys, the same form as sample_output.csv."""
    return json.dumps(steps, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def case_number(case_id):
    return int(case_id.split("_")[1])


def write_output(path, rows):
    """rows: (case_id, deletion_status, steps) tuples. Writes them in numeric case_id order."""
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(COLUMNS)
        for case_id, status, steps in sorted(rows, key=lambda row: case_number(row[0])):
            steps_json = "[]" if status == "NOT_APPLICABLE" else serialize_steps(sort_steps(steps))
            writer.writerow([case_id, status, steps_json])


def read_rows(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def validate_steps(status, steps):
    """Errors for one row's parsed steps_json (an empty list means valid)."""
    if not isinstance(steps, list):
        return ["steps_json is not a JSON array"]
    if status == "NOT_APPLICABLE":
        return [] if steps == [] else ["NOT_APPLICABLE must have steps_json []"]
    # A COMPLETE row with no steps is allowed by the contract (it is only a weak answer: nothing
    # linked had a usable state); main.py reports such rows as warnings. INCOMPLETE needs a plan.
    if not steps:
        return [] if status == "COMPLETE" else [f"{status} must have at least one step"]
    errors = []
    for i, step in enumerate(steps, start=1):
        if not isinstance(step, dict) or sorted(step) != sorted(STEP_FIELDS):
            errors.append(f"step {i}: fields must be exactly {STEP_FIELDS}")
            continue
        if type(step["step_no"]) is not int:  # not isinstance: JSON true would pass as 1
            errors.append(f"step {i}: step_no must be an integer, got {step['step_no']!r}")
            continue
        not_strings = [k for k in STEP_FIELDS[1:] if not isinstance(step[k], str)]
        if not_strings:
            errors.append(f"step {i}: {', '.join(not_strings)} must be strings")
            continue
        op = step["operation"]
        if op not in OPERATION_INDEX:
            errors.append(f"step {i}: unknown operation {op!r}")
            continue
        if step["phase"] != OPERATION_PHASE[op] or step["state"] != OPERATION_STATE[op]:
            errors.append(f"step {i}: {op} needs phase {OPERATION_PHASE[op]} and state {OPERATION_STATE[op]}")
        if step["step_no"] != i:
            errors.append(f"step {i}: step_no is {step['step_no']!r}, expected {i}")
        if not (step["system_id"] and step["resource_id"]):
            errors.append(f"step {i}: system_id and resource_id must be non-empty")
        cited = step["evidence_id"] != ""
        if status == "COMPLETE" and step["phase"] != "VERIFICATION":
            errors.append(f"step {i}: COMPLETE cases contain only VERIFICATION steps")
        if (status == "COMPLETE" or step["phase"] == "FINDING") and not cited:
            errors.append(f"step {i}: {step['phase']} step in a {status} case must cite evidence")
        if status == "INCOMPLETE" and step["phase"] != "FINDING" and step["evidence_id"] != "":
            errors.append(f"step {i}: INCOMPLETE {step['phase']} step must have empty evidence_id")
    if errors:
        return errors
    if status == "INCOMPLETE":
        errors.extend(incomplete_plan_errors(steps))
    order = [step_sort_key(s)[:4] for s in steps]
    if order != sorted(order):
        errors.append("steps are not in phase / operation / system_id / resource_id order")
    return errors


def incomplete_plan_errors(steps):
    """An INCOMPLETE plan has all three phases, and every finding is fixed and every fix verified."""
    errors = [f"INCOMPLETE case has no {phase} step" for phase in PHASES if phase not in {s["phase"] for s in steps}]
    present = {(s["operation"], s["system_id"], s["resource_id"]) for s in steps}
    for s in steps:
        target = (s["system_id"], s["resource_id"])
        fixes = FINDING_FIXES.get(s["operation"])
        if fixes and not any((fix, *target) in present for fix in fixes):
            errors.append(f"{s['operation']} on {target} has no {' or '.join(sorted(fixes))} step")
        check = FIX_CHECK.get(s["operation"])
        if check and (check, *target) not in present:
            errors.append(f"{s['operation']} on {target} has no {check} step")
    return errors


def validate_output(path, expected_case_ids=None):
    """Errors for a whole output file (an empty list means valid)."""
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        rows = list(reader)
    if header != COLUMNS:
        return [f"header is {header}, expected {COLUMNS}"]
    errors = []
    case_ids = [row[0] if row else "" for row in rows]
    if expected_case_ids is not None:
        expected = sorted(expected_case_ids, key=case_number)
        if case_ids != expected:
            missing = set(expected) - set(case_ids)
            extra = set(case_ids) - set(expected)
            errors.append(f"case ids differ from requests: {len(rows)} rows for {len(expected)} requests, "
                          f"{len(missing)} missing, {len(extra)} extra, or order differs")
    if len(set(case_ids)) != len(case_ids):
        errors.append("duplicate case_id rows")
    for line, row in enumerate(rows, start=2):
        if len(row) != 3:
            errors.append(f"line {line}: expected 3 columns, got {len(row)}")
            continue
        case_id, status, steps_json = row
        try:
            case_number(case_id)
        except (IndexError, ValueError):
            errors.append(f"line {line}: bad case_id {case_id!r}")
            continue
        if status not in STATUSES:
            errors.append(f"{case_id}: unknown deletion_status {status!r}")
            continue
        try:
            steps = json.loads(steps_json)
        except json.JSONDecodeError as e:
            errors.append(f"{case_id}: invalid JSON ({e})")
            continue
        errors.extend(f"{case_id}: {error}" for error in validate_steps(status, steps))
    numbers = [case_number(c) for c in case_ids if c.startswith("case_") and c[5:].isdigit()]
    if numbers != sorted(numbers):
        errors.append("rows are not in numeric case_id order")
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate an output.csv file.")
    parser.add_argument("path")
    parser.add_argument("--requests", help="requests CSV whose case_ids the file must cover exactly")
    args = parser.parse_args(argv)
    expected = [row["case_id"] for row in read_rows(args.requests)] if args.requests else None
    errors = validate_output(args.path, expected)
    for error in errors:
        print(error)
    print(f"{args.path}: {'VALID' if not errors else f'{len(errors)} error(s)'}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
