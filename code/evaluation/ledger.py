"""Assertion ledger: explain every expected step of dataset/sample_output.csv with participant evidence.

Development and evaluation tooling only (PLAN section 8). It reads sample_output.csv, which the
pipeline must never do. It decides nothing for the pipeline; it reports, per expected step:

    subject       user_id resolved from the request email at audit_at (code/identity.py)
    link          how the resource is tied to the subject using only information eligible at audit_at:
                  PROVEN              an observation (identifiers / subject_refs) or an activity details
                                      text names both the resource and the subject
                  LOOKUP-ONLY         only observations whose lookup keys hold a subject identifier
                  INFERRED-CANDIDATE  only an unlabelled deletion_worker activity row names the resource
                  UNEXPLAINED         none of the above
    state         eligible observations supporting the state the step needs now, and the latest
                  eligible observation per physical copy
    citation      for cited steps: eligible candidate files proving the same state, and which ordering
                  of the PLAN 5.8 preference hypotheses reproduces the cited file
    rule          best-effort deletion_rules.csv rule and whether the operation fits its action

Run from the repository root:  python code/evaluation/ledger.py
Writes output/ledger.md and output/ledger.json and prints a summary.
"""

import csv
import itertools
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import activity  # noqa: E402
import evidence  # noqa: E402
import identity  # noqa: E402
from identity import at_or_before, to_time  # noqa: E402

OUTPUT = evidence.REPO_ROOT / "output"
NAME_FIELDS = {"full_name", "name", "display_name", "requester_name", "customer_name"}

# State a resource must be in *now* (at audit_at) for each operation. Findings and remediation need
# the violation; verification needs the compliant state, unless the case remediates the resource.
VIOLATION = {
    "REPORT_REMAINING_RECORD": {"EXPOSED", "CLEAN"}, "DELETE_RECORD": {"EXPOSED", "CLEAN"},
    "REPORT_EXPOSED_PII": {"EXPOSED"}, "REDACT_RECORD": {"EXPOSED"}, "ANONYMIZE_RECORD": {"EXPOSED"},
    "REPORT_ACTIVE_DATA_FLOW": {"ACTIVE"}, "DISABLE_DATA_FLOW": {"ACTIVE"},
    "REPORT_RESTORE_RISK": {"ACTIVE"}, "BLOCK_RESTORE": {"ACTIVE"},
}
COMPLIANT = {
    "VERIFY_RECORD_ABSENT": {"ABSENT"}, "VERIFY_RECORD_PRESENT": {"EXPOSED", "CLEAN"},
    "VERIFY_RECORD_REDACTED": {"CLEAN"}, "VERIFY_RECORD_ANONYMIZED": {"CLEAN"},
    "VERIFY_DATA_FLOW_DISABLED": {"DISABLED"}, "VERIFY_RESTORE_BLOCKED": {"BLOCKED"},
}
# Rule action -> operations consistent with it.
ACTION_OPS = {
    "DELETE": {"REPORT_REMAINING_RECORD", "DELETE_RECORD", "VERIFY_RECORD_ABSENT"},
    "REDACT": {"REPORT_EXPOSED_PII", "REDACT_RECORD", "VERIFY_RECORD_REDACTED"},
    "ANONYMIZE": {"REPORT_EXPOSED_PII", "ANONYMIZE_RECORD", "VERIFY_RECORD_ANONYMIZED"},
    "RETAIN": {"VERIFY_RECORD_PRESENT"},
    "RETAIN_BACKUP": {"REPORT_RESTORE_RISK", "BLOCK_RESTORE", "VERIFY_RESTORE_BLOCKED"},
    "FLOW": {"REPORT_ACTIVE_DATA_FLOW", "DISABLE_DATA_FLOW", "VERIFY_DATA_FLOW_DISABLED"},
}
PREFIX_CATEGORY = {
    "account": "USER_PROFILE", "search": "SEARCH_INDEX_ENTRY", "export": "USER_EXPORT",
    "audit_record": "SECURITY_AUDIT_LOG", "backup": "BACKUP_ARCHIVE", "tombstone": "ACCOUNT_TOMBSTONE",
    "cache": "CACHE_ENTRY", "marketing": "MARKETING_PROFILE", "email_delivery": "EMAIL_DELIVERY_LOG",
    "legal_hold": "LEGAL_HOLD_RECORD",
}
NO_CUTOFF = "9999-12-31T00:00:00Z"
HYPOTHESES = {"a": "names the resource", "b": "covers primary and replicas", "c": "holds the case's other states"}


# ---------------------------------------------------------------- loading

def read_csv(name):
    with open(evidence.DATASET / name, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def load_rules():
    return {r["data_category"]: r for r in read_csv("deletion_rules.csv")}


def all_requests():
    return read_csv("sample_deletion_requests.csv") + read_csv("deletion_requests.csv")


# ---------------------------------------------------------------- observation helpers

def eligible(obs, cutoff):
    return at_or_before(obs["file_captured_at"], cutoff) and \
        (obs["observed_at"] is None or at_or_before(obs["observed_at"], cutoff))


def obs_time(obs):
    return obs["observed_at"] or obs["file_captured_at"]


def copy_key(table, copy):
    """Physical copy name: 'primary' unless a replica table or copy label says otherwise."""
    if table:
        m = evidence.COPY_SUFFIX_RE.match(table)
        if m and m.group("copy") != "primary":
            return m.group("copy")
    return copy if copy and copy != "primary" else "primary"


def has_direct_identifiers(obs):
    ids = obs["identifiers"]
    names = any(obs["fields"].get(k) for k in NAME_FIELDS)
    nested = any(isinstance(v, dict) and any(v.get(k) for k in NAME_FIELDS) for v in obs["fields"].values())
    return bool(ids["emails"] or ids["phones"] or ids["email_hashes"] or names or nested)


def truthy(value):
    return str(value).lower() in ("true", "1")


def observed_state(obs):
    """ABSENT, ACTIVE, DISABLED, BLOCKED, EXPOSED (direct identifiers present) or CLEAN."""
    if obs["kind"] == "absence":
        return "ABSENT"
    f = obs["fields"]
    result = f.get("result")
    if result in ("ACTIVE", "DISABLED", "BLOCKED"):
        return result
    if result in ("FOUND", "IDENTIFIABLE"):
        return "EXPOSED"
    if result in ("REDACTED", "ANONYMIZED", "RETAINED"):
        return "CLEAN"
    if "enabled" in f:
        return "ACTIVE" if truthy(f["enabled"]) else "DISABLED"
    if "restore_allowed" in f:
        return "ACTIVE" if truthy(f["restore_allowed"]) else "BLOCKED"
    if "eligible" in f and "deletion_block" in f:
        return "ACTIVE" if truthy(f["eligible"]) and not truthy(f["deletion_block"]) else "BLOCKED"
    return "EXPOSED" if has_direct_identifiers(obs) else "CLEAN"


def scope_absences(scope, resource_id, about_subject):
    """Tables of a scope proving resource_id absent. SQL snapshot tables are complete. A JSON bundle
    collection is an extract about its own subjects (evidence_51 lists no profiles, yet evidence_92
    shows account_5334 present at that time), so only an empty collection counts, and only in a
    bundle that holds a record tied to the subject (`about_subject`)."""
    tables = []
    for table, ids in scope["tables"].items():
        if resource_id in ids:
            continue
        if scope["file_type"] == "sql" or (not ids and about_subject):
            tables.append(table)
    return tables


# ---------------------------------------------------------------- per-step explanation

class Index:
    """Observations and activity rows grouped by the resource IDs they name."""

    def __init__(self, observations, activities):
        self.by_resource = defaultdict(list)
        self.mentioning = defaultdict(list)
        self.scopes = [o for o in observations if o["kind"] == "scope"]
        for o in observations:
            if o["kind"] == "scope":
                continue
            if o["resource_id"]:
                self.by_resource[o["resource_id"]].append(o)
            for r in o["related_resource_ids"]:
                self.mentioning[r].append(o)
        self.records_by_file = defaultdict(list)  # non-scope observations of each file
        for o in observations:
            if o["kind"] != "scope":
                self.records_by_file[o["file_id"]].append(o)
        self.activity = defaultdict(list)
        for a in activities:
            for r in {a["resource_id"], *a["mentioned_resource_ids"]}:
                self.activity[r].append(a)


def resolve_subject(email, cutoff):
    """(user_id, identifiers) at cutoff. An email that resolves to nobody (an identity gap) gets
    user_id None and no identifiers, so nothing ties to it."""
    user_id = identity.resolve_user(email, cutoff)
    if user_id is None:
        return None, {"emails": [], "phones": [], "email_hashes": [], "user_ids": []}
    return user_id, identity.subject_identifiers(user_id, cutoff)


def subject_values(ids):
    return set(evidence.flat_identifier_values(ids))


def ties_to(obs, values):
    """The observation's identifiers or subject references hold one of the subject's values."""
    return bool(values & (set(evidence.flat_identifier_values(obs["identifiers"])) | set(obs["subject_refs"])))


def file_about_subject(index, file_id, values, cutoff):
    """A record of the file eligible at cutoff ties to the subject (bundle membership, PLAN F3)."""
    return any(eligible(o, cutoff) and ties_to(o, values) for o in index.records_by_file[file_id])


def explain_link(index, resource_id, subject_ids, cutoff):
    values = subject_values(subject_ids)
    proven, lookup, worker, other_activity = [], [], [], []
    for o in index.by_resource[resource_id] + index.mentioning[resource_id]:
        if not eligible(o, cutoff):
            continue
        how = "names" if o["resource_id"] == resource_id else "mentions"
        if ties_to(o, values):
            proven.append(f"{o['obs_id']} ({how})")
        elif values & set(o["lookup_keys"]):
            lookup.append(f"{o['obs_id']} ({how})")
    for a in index.activity[resource_id]:
        if not at_or_before(a["event_time"], cutoff):
            continue
        if values & set(a["emails"] + a["user_ids"]):
            proven.append(a["activity_id"])
        elif a["source_system"] == "deletion_worker" and a["resource_id"] == resource_id:
            worker.append(f"{a['activity_id']} {a['event_type']} {a['reported_result']} {a['event_time']}")
        else:
            other_activity.append(a["activity_id"])
    if proven:
        link_type = "PROVEN"
    elif lookup:
        link_type = "LOOKUP-ONLY"
    elif worker:
        link_type = "INFERRED-CANDIDATE"
    else:
        link_type = "UNEXPLAINED"
    return {"type": link_type, "proven": proven, "lookup_only": lookup, "worker_rows": worker,
            "other_activity": other_activity}


def needed_states(step, case_status, remediated):
    """(states the resource must be in at audit_at, whether that is a violation state)."""
    op = step["operation"]
    if op in VIOLATION:
        return VIOLATION[op], True
    if case_status == "INCOMPLETE" and step["resource_id"] in remediated:
        return VIOLATION[remediated[step["resource_id"]]], True
    return COMPLIANT[op], False


def state_evidence(index, step, cutoff, needed, violation, subject):
    """Eligible observations of the resource with their states, the latest per physical copy, and
    whether the state is supported: some copy shows the violation, or every copy is compliant."""
    resource_id, system = step["resource_id"], step["system_id"]
    entries = []
    for o in index.by_resource[resource_id]:
        if eligible(o, cutoff):
            copy = copy_key(o["table"], o["copy"])
            entries.append({"time": obs_time(o), "copy": copy, "state": observed_state(o), "obs_id": o["obs_id"],
                            "file_id": o["file_id"], "names": True})
    for s in index.scopes:
        if s["system"] != system or not eligible(s, cutoff):
            continue
        for table in scope_absences(s, resource_id, file_about_subject(index, s["file_id"], subject, cutoff)):
            entries.append({"time": obs_time(s), "copy": copy_key(table, None), "state": "ABSENT",
                            "obs_id": s["obs_id"], "file_id": s["file_id"], "names": False})
    latest = {}
    for e in sorted(entries, key=entry_order):
        latest[e["copy"]] = e
    states = [e["state"] for e in latest.values()]
    if violation:
        supported = any(state in needed for state in states)
    else:
        # A copy that holds nothing never violates a redact/anonymize/disable/block/retain state.
        allowed = needed | {"ABSENT"}
        supported = any(state in needed for state in states) and all(state in allowed for state in states)
    return entries, latest, supported


def is_contact_record(obs):
    """A contact lookup record: email opt-in flag, or read from a contact index / contact query."""
    f = obs["fields"]
    sources = [f.get("source"), (f.get("request") or {}).get("source")] + (f.get("events") or [])
    return "email_opt_in" in f or any(v and str(v).startswith(("contact_", "legacy_contact")) for v in sources)


def classify_rule(resource_id, observations, rules):
    """Best-effort rule: an explicit record_type, else collection/field hints, else the ID prefix."""
    types = {o["record_type"] for o in observations if o["record_type"] in rules}
    if len(types) == 1:
        category, basis = types.pop(), "record_type"
    elif resource_id.startswith("data_flow_"):
        return None, "data flow (no data category; disabled as a recreation path)", "FLOW"
    elif any(is_contact_record(o) for o in observations):
        # Sample 12 (analytics contact lookup) and sample 10 (legacy_contact_index) are deleted, not
        # anonymized/redacted. rule_10 MARKETING_PROFILE is also DELETE; the data cannot tell them apart.
        category, basis = "SECONDARY_IDENTIFIER", "contact record (email_opt_in / contact index)"
    elif resource_id.startswith("analytics_"):
        category, basis = "ANALYTICS_PROFILE", "analytics profile"
    elif resource_id.startswith("support_"):
        attachment = any("attachment" in json.dumps(o["fields"]).lower() for o in observations)
        category = "SUPPORT_ATTACHMENT" if attachment else "SUPPORT_MESSAGE"
        basis = "attachment mentioned" if attachment else "support conversation"
    else:
        category = PREFIX_CATEGORY.get(resource_id.rsplit("_", 1)[0])
        basis = "resource ID prefix"
    if category is None:
        return None, "no category found", None
    action = rules[category]["required_action"]
    if category == "BACKUP_ARCHIVE":
        action = "RETAIN_BACKUP"
    return rules[category]["rule_id"] + " " + category, basis, action


# ---------------------------------------------------------------- citations

def entry_order(e):
    return to_time(e["time"]), e["obs_id"]


def citation_candidates(entries, needed):
    """file_id -> facts for the PLAN 5.8 hypotheses, over files with an observation proving the state.
    An observation counts only if no later eligible observation of the same physical copy contradicts
    it: a 404 followed by a present record is no candidate, even if another 404 comes later."""
    last_contrary = {}
    for e in entries:
        if e["state"] not in needed:
            last_contrary[e["copy"]] = max(last_contrary.get(e["copy"], entry_order(e)), entry_order(e))
    files = {}
    for e in entries:
        if e["state"] not in needed or (e["copy"] in last_contrary and entry_order(e) < last_contrary[e["copy"]]):
            continue
        f = files.setdefault(e["file_id"], {"names": False, "copies": set(), "obs": []})
        f["names"] |= e["names"]
        f["copies"].add(e["copy"])
        if e["obs_id"] not in f["obs"]:
            f["obs"].append(e["obs_id"])
    return files


def pick(candidates, order, captured, others):
    """Best candidate under a hypothesis order; ties broken by latest capture, then file_id."""
    def key(file_id):
        c = candidates[file_id]
        scores = {"a": c["names"], "b": len(c["copies"]) > 1 or "primary" not in c["copies"],
                  "c": others.get(file_id, 0)}
        return tuple(scores[h] for h in order) + (captured[file_id], file_id)
    ranked = sorted(candidates, key=key, reverse=True)
    return ranked[0] if ranked else None


# ---------------------------------------------------------------- ledger

def build():
    observations = evidence.load_observations()
    activities = activity.load_activity()
    index = Index(observations, activities)
    rules = load_rules()
    manifest = {f["file_id"]: f for f in evidence.load_manifest()}
    captured = {fid: f["captured_at"] for fid, f in manifest.items()}
    requests = {r["case_id"]: r for r in read_csv("sample_deletion_requests.csv")}
    expected = read_csv("sample_output.csv")

    rows = []
    for case in expected:
        req = requests[case["case_id"]]
        cutoff = req["audit_at"]
        user_id, subject_ids = resolve_subject(req["email"], cutoff)
        steps = json.loads(case["steps_json"])
        remediated = {s["resource_id"]: s["operation"] for s in steps if s["phase"] == "REMEDIATION"}
        case_rows = []
        for step in steps:
            needed, violation = needed_states(step, case["deletion_status"], remediated)
            entries, latest, supported = state_evidence(index, step, cutoff, needed, violation,
                                                        subject_values(subject_ids))
            own_obs = [o for o in index.by_resource[step["resource_id"]] if eligible(o, cutoff)]
            rule, basis, action = classify_rule(step["resource_id"], own_obs, rules)
            cited = step["evidence_id"]
            row = {
                "case_id": case["case_id"], "status": case["deletion_status"], "step_no": step["step_no"],
                "phase": step["phase"], "system_id": step["system_id"], "resource_id": step["resource_id"],
                "operation": step["operation"], "state": step["state"], "evidence_id": cited,
                "subject": user_id, "audit_at": cutoff,
                "link": explain_link(index, step["resource_id"], subject_ids, cutoff),
                # What the link would be if evidence after audit_at were allowed (never used for answers).
                "link_ignoring_cutoff": explain_link(index, step["resource_id"], subject_ids, NO_CUTOFF)["type"],
                "needed_now": sorted(needed),
                "latest_per_copy": {copy: {"state": e["state"], "obs_id": e["obs_id"], "time": e["time"]}
                                    for copy, e in latest.items()},
                "state_supported": supported,
                "rule": rule, "rule_basis": basis,
                "rule_consistent": action is not None and step["operation"] in ACTION_OPS[action],
                # An unsupported state has no proving file, so it takes no part in citation scoring.
                "candidates": citation_candidates(entries, needed) if supported else {},
            }
            if cited:
                row["cited_captured_at"] = captured.get(cited)
                row["cited_after_audit"] = not at_or_before(captured[cited], cutoff)
                row["cited_is_candidate"] = cited in row["candidates"]
            case_rows.append(row)
        # Hypothesis (c): how many of the case's other steps each file also proves.
        for row in case_rows:
            row["other_support"] = Counter(fid for other in case_rows if other is not row
                                           for fid in other["candidates"])
        rows += case_rows

    orders = ["".join(p) for p in itertools.permutations("abc")] + ["none"]
    hypothesis_hits = {o: [] for o in orders}
    for row in rows:
        if not row["evidence_id"]:
            continue
        row["picks"] = {}
        for order in orders:
            chosen = pick(row["candidates"], "" if order == "none" else order, captured, row["other_support"])
            row["picks"][order] = chosen
            if chosen == row["evidence_id"]:
                hypothesis_hits[order].append(f"{row['case_id']}#{row['step_no']}")
    window = window_test(observations, activities, requests, expected)
    return rows, hypothesis_hits, index, window, activities


def window_test(observations, activities, requests, expected):
    """Hypothesis from sample 17: evidence ties a resource to a request only when it falls inside that
    request's own window (requested_at .. audit_at). For every sample case, take each eligible
    observation or activity row that names a resource and ties it to the subject (proven or lookup-only),
    and count whether it is in the window and whether the resource is in the expected answer."""
    results = Counter()
    rows = []
    for case in expected:
        req = requests[case["case_id"]]
        cutoff = req["audit_at"]
        values = subject_values(resolve_subject(req["email"], cutoff)[1])
        answer = {s["resource_id"] for s in json.loads(case["steps_json"])}
        ties = []  # (kind, source id, resource id, time)
        for o in observations:
            if o["kind"] == "scope" or not o["resource_id"] or not eligible(o, cutoff):
                continue
            if ties_to(o, values):
                ties.append(("proven", o["obs_id"], o["resource_id"], obs_time(o)))
            elif values & set(o["lookup_keys"]):
                ties.append(("lookup", o["obs_id"], o["resource_id"], obs_time(o)))
        for a in activities:
            if at_or_before(a["event_time"], cutoff) and values & set(a["emails"] + a["user_ids"]):
                ties.append(("proven", a["activity_id"], a["resource_id"], a["event_time"]))
        for kind, source, resource_id, time in ties:
            in_window = at_or_before(req["requested_at"], time)
            in_answer = resource_id in answer
            results[f"{kind}: {'in' if in_window else 'out of'} window, {'in' if in_answer else 'not in'} answer"] += 1
            if in_window != in_answer:
                rows.append(f"{case['case_id']} {kind} {source} {resource_id} at {time}: "
                            f"window={in_window} answer={in_answer}")
    return dict(sorted(results.items())), rows


# ---------------------------------------------------------------- case 17

def case_17_explanation(index, activities):
    """Facts about user_17's two requests, eligible at case_17's audit_at, plus nearest requests in time."""
    requests = all_requests()
    by_case = {r["case_id"]: r for r in requests}
    c17, c46 = by_case["case_17"], by_case["case_46"]
    cutoff = c17["audit_at"]
    ids = identity.subject_identifiers("user_17", cutoff)
    lines = [
        "## Sample case_17 (user_17; same person as evaluation case_46)",
        "",
        f"- case_46: requested {c46['requested_at']}, reported {c46['reported_status']} at {c46['reported_at']}, "
        f"audit_at {c46['audit_at']}.",
        f"- case_17: requested {c17['requested_at']}, reported {c17['reported_status']} at {c17['reported_at']}, "
        f"audit_at {c17['audit_at']}.",
        f"- Identifiers at case_17's cutoff: {', '.join(evidence.flat_identifier_values(ids))}.",
        "",
    ]

    def nearest_requests(event_time, n=3):
        ranked = sorted(requests, key=lambda r: abs((to_time(r["reported_at"]) - to_time(event_time)).total_seconds()))
        return ", ".join(f"{r['case_id']} (reported {r['reported_status']} {r['reported_at']}, "
                         f"{(to_time(event_time) - to_time(r['reported_at'])).total_seconds() / 3600:+.2f} h)"
                         for r in ranked[:n])

    for resource_id in ("account_4092", "account_6523", "tombstone_17"):
        lines.append(f"### {resource_id}")
        for o in index.by_resource[resource_id]:
            lines.append(f"- {o['obs_id']} ({o['kind']}, captured {o['file_captured_at']}, eligible for case_17: "
                         f"{eligible(o, cutoff)}, for case_46: {eligible(o, c46['audit_at'])}): "
                         f"identifiers {evidence.flat_identifier_values(o['identifiers'])}, "
                         f"subject_refs {o['subject_refs']}, lookup_keys {o['lookup_keys']}")
        for a in index.activity[resource_id]:
            lines.append(f"- {a['activity_id']} {a['event_time']} {a['event_type']} {a['reported_result']}: "
                         f"\"{a['details']}\"; eligible for case_17: {at_or_before(a['event_time'], cutoff)}, "
                         f"for case_46: {at_or_before(a['event_time'], c46['audit_at'])}")
            if a["source_system"] == "deletion_worker":
                lines.append(f"  - requests whose reported_at is nearest this event: {nearest_requests(a['event_time'])}")
        lines.append("")
    lines += [
        "### evidence_42 (PNG, not parsed until milestone 4; read by hand for this ledger)",
        "- deleted_account_reference_20260310.png, origin identity_service, captured 2026-03-10T06:00:00Z.",
        "- Shows customer reference `user_17` as a \"Retained reference\" (Customer key, Account Platform, eu-west),",
        "  created 11 Jan 2024, most recent update 10 Mar 2026 07:18 UTC, linked to ORD-20263245, CS-3245, CS-3378,",
        "  FIN-7742. No name, email, phone or hash is shown: a clean tombstone view.",
        "",
        "### Explanation",
        "",
        "Proven (from eligible evidence):",
        "- account_4092: the only account_service lookup made with case_17's email during case_17's window",
        "  (evidence_01#3, 2026-09-15 11:51, inside requested_at..audit_at) returned account_4092 with 404. The",
        "  deletion-worker row activity_3383 (2026-08-22 11:14, FAILED) submitted account_4092 0.76 h before",
        "  case_17's reported_at (12:00, NOT_STARTED); FAILED matches NOT_STARTED. case_15 has the same",
        "  reported_at but reported COMPLETE (and its own account_4745), so time alone ties and status decides.",
        "  Its absence is shown by the 404 on the primary route. backup_3245 and audit_record_1797 in the same",
        "  answer are also tied to",
        "  user_17 only by lookup keys in evidence_01 (subject_key email, correlation_key user_17).",
        "- account_6523: tied to case_17's email only by evidence_67, a 404 readback captured 2026-03-10 06:18,",
        "  inside case_46's window (requested 2026-03-08, audit 2026-03-11), not case_17's. The only other row,",
        "  activity_3850 (2026-09-15 09:56 SUCCESS), carries no identity; it lies 24 days after case_17's",
        "  reported_at and its status (SUCCESS) contradicts case_17's NOT_STARTED. By time it falls 1.05 h before",
        "  the reported_at of case_03 (whose answer uses account_7566) and of evaluation case_44, so it most",
        "  likely belongs to case_44 (a guess: case_44 reported PARTIALLY_COMPLETE, not COMPLETE).",
        "- tombstone_17: linked to user_17 and the email by activity_4011 (2026-03-10 06:07, data catalog) and",
        "  shown clean in evidence_42 (2026-03-10). Both are inside case_46's window, none inside case_17's.",
        "",
        "Guessed (not proven by any participant file):",
        "- The expected answer scopes resources to the request: evidence created during an earlier request's",
        "  window (case_46: account_6523 readback, tombstone_17 link) belongs to that request, so case_17 only",
        "  covers resources observed in its own window. This is the simplest rule consistent with sample 17,",
        "  but one sample cannot confirm it.",
        "- Lookup-key links in the case's own audit-window sweep are what the answer relies on for case_17.",
        "  A blanket \"lookup key is never proof\" rule (PLAN 5.4) would drop account_4092, backup_3245 and",
        "  audit_record_1797 and break this sample. What separates account_4092 from account_6523 is time",
        "  (whose window the lookup falls in), not the lookup-key kind.",
    ]
    return lines


# ---------------------------------------------------------------- output

def resource_type(resource_id):
    return resource_id.rsplit("_", 1)[0]


def summarize(rows, hypothesis_hits, window):
    cited = [r for r in rows if r["evidence_id"]]
    out = {
        "steps": len(rows),
        "by_link": dict(Counter(r["link"]["type"] for r in rows)),
        "by_resource_type": {t: dict(Counter(r["link"]["type"] for r in rows if resource_type(r["resource_id"]) == t))
                             for t in sorted({resource_type(r["resource_id"]) for r in rows})},
        "lookup_only_steps": [f"{r['case_id']}#{r['step_no']} {r['resource_id']} via {', '.join(r['link']['lookup_only'])}"
                              for r in rows if r["link"]["type"] == "LOOKUP-ONLY"],
        "state_supported": sum(r["state_supported"] for r in rows),
        "rule_consistent": sum(r["rule_consistent"] for r in rows),
        "cited_steps": len(cited),
        "cited_is_candidate": sum(r["cited_is_candidate"] for r in cited),
        "cited_single_candidate": sum(len(r["candidates"]) == 1 for r in cited),
        "cited_after_audit": [f"{r['case_id']}#{r['step_no']} {r['evidence_id']}" for r in cited if r["cited_after_audit"]],
        "cited_not_candidate": [f"{r['case_id']}#{r['step_no']} {r['evidence_id']}" for r in cited if not r["cited_is_candidate"]],
        "hypothesis_orders": {o: len(hits) for o, hits in hypothesis_hits.items()},
        "unexplained": [f"{r['case_id']}#{r['step_no']} {r['resource_id']} {r['operation']} "
                        f"(ignoring cutoff: {r['link_ignoring_cutoff']})"
                        for r in rows if r["link"]["type"] == "UNEXPLAINED"],
        "state_unsupported": [f"{r['case_id']}#{r['step_no']} {r['resource_id']} needs {r['needed_now']}"
                              for r in rows if not r["state_supported"]],
        "rule_inconsistent": [f"{r['case_id']}#{r['step_no']} {r['resource_id']} {r['operation']} rule={r['rule']}"
                              for r in rows if not r["rule_consistent"]],
        # Identifier kinds with no value known at audit_at (snapshot newer than audit_at, no audit row).
        "unresolved_identities": {r["case_id"]: kinds for r in rows if r["subject"]
                                  for kinds in [identity.unresolved_kinds(r["subject"], r["audit_at"])] if kinds},
        # Cases whose request email resolves to no subject at audit_at.
        "identity_gaps": sorted({r["case_id"] for r in rows if r["subject"] is None}),
    }
    out["window_test"], out["window_test_disagreements"] = window
    best = max(out["hypothesis_orders"].values())
    out["best_orders"] = [o for o, n in out["hypothesis_orders"].items() if n == best]
    misses = {}
    for r in cited:
        order = out["best_orders"][0]
        if r["picks"][order] != r["evidence_id"]:
            misses[f"{r['case_id']}#{r['step_no']}"] = {
                "cited": r["evidence_id"], "picked": r["picks"][order], "candidates": sorted(r["candidates"])}
    out["best_order_misses"] = misses
    return out


def markdown(rows, summary, case17):
    lines = ["# Assertion ledger: sample steps explained", "",
             "Generated by `python code/evaluation/ledger.py` (development tooling; reads sample_output.csv).",
             "Only evidence eligible at each case's audit_at is used. Hypotheses for citations (PLAN 5.8): "
             + "; ".join(f"({k}) {v}" for k, v in HYPOTHESES.items())
             + "; ties broken by latest capture, then file_id.", "",
             "## Summary", "", "```", json.dumps(summary, indent=1), "```", ""]
    lines += case17 + [""]
    for case_id in dict.fromkeys(r["case_id"] for r in rows):
        mine = [r for r in rows if r["case_id"] == case_id]
        subject = mine[0]["subject"] or "**identity gap: email resolves to nobody at audit_at**"
        lines += [f"## {case_id} ({mine[0]['status']}, subject {subject}, audit_at {mine[0]['audit_at']})", "",
                  "| # | op | resource | cited | link | link evidence | needed now | latest per copy | rule | citation candidates / picks |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for r in mine:
            link = r["link"]
            link_ev = "; ".join(link["proven"] or link["lookup_only"] or link["worker_rows"]) or "-"
            latest = "; ".join(f"{c}: {v['state']} {v['obs_id']}" for c, v in r["latest_per_copy"].items()) or "-"
            flag = "" if r["state_supported"] else " **unsupported**"
            cites = "-"
            if r["evidence_id"]:
                picks = ", ".join(f"{o}={p}" for o, p in r["picks"].items())
                cites = f"{', '.join(sorted(r['candidates'])) or 'none'} / {picks}"
                if not r["cited_is_candidate"]:
                    cites = "**cited not a candidate** " + cites
            rule = f"{r['rule']} ({'ok' if r['rule_consistent'] else '**inconsistent**'})"
            lines.append(f"| {r['step_no']} | {r['operation']} | {r['system_id']}/{r['resource_id']} | "
                         f"{r['evidence_id'] or '-'} | {link['type']} | {link_ev} | {'/'.join(r['needed_now'])} | "
                         f"{latest}{flag} | {rule} | {cites} |")
        lines.append("")
    return "\n".join(lines) + "\n"


def main():
    rows, hypothesis_hits, index, window, activities = build()
    summary = summarize(rows, hypothesis_hits, window)
    case17 = case_17_explanation(index, activities)
    OUTPUT.mkdir(exist_ok=True)
    serial = []
    for r in rows:
        r = dict(r)
        r["candidates"] = {fid: {"names": c["names"], "copies": sorted(c["copies"]), "obs": c["obs"]}
                           for fid, c in r["candidates"].items()}
        r["other_support"] = dict(r["other_support"])
        serial.append(r)
    (OUTPUT / "ledger.json").write_text(json.dumps({"summary": summary, "steps": serial, "case_17": case17},
                                                   indent=1, ensure_ascii=False), encoding="utf-8")
    (OUTPUT / "ledger.md").write_text(markdown(rows, summary, case17), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "best_order_misses"}, indent=1))
    print("best order misses:", json.dumps(summary["best_order_misses"], indent=1))
    print("wrote output/ledger.md and output/ledger.json")


if __name__ == "__main__":
    main()
