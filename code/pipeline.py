"""Deterministic deletion-audit pipeline (PLAN section 5). Links are proven, lookup (stage 2b) or
inferred (milestone 4, code/inference.py); milestone 3 accepts reviewed LLM context.

Per request, in order (each stage records one trace event, see code/run_trace.py):

1. intent     LLM primary via llm_context, or explicit core diagnostic classifier.
              NOT_APPLICABLE gives []. Any UNSURE request
              stops the whole run before predictions are written (UnsureIntent).
2. identity   request email -> user_id and every identifier known at audit_at (code/identity.py).
3. linking    resources tied to the person by eligible evidence:
              proven  an observation's identifiers / subject references hold a subject value (the
                      record and the resources it mentions), or an activity row's details name the
                      subject (data catalog links);
              lookup  (decision F1) a lookup keyed with a subject value whose completed result in
                      the same trace returns the resource. A resource named only on the request
                      line is not a link.
              Then (decision F2 option B) a person with several requests: a link belongs to the
              request whose window (requested_at .. audit_at) holds its time; a link outside every
              window belongs to the earliest request that can see it. Full history is kept otherwise.
              Finally (PLAN 6.3) an inferred link for a deleted account or an already-anonymized
              analytics record that the evidence never ties to the person: an activity row assigned
              to the request by code/inference.py, solved separately for each distinct audit_at.
4. state     per resource and physical copy, the latest eligible observation wins, including
              absence observations scoped per decision F3 (SQL snapshots cover their declared
              tables, a JSON bundle's empty collection only the people the bundle covers).
5. policy     deletion_rules.csv rule per resource (code/policy.py), then violation / compliant /
              gap from the latest state of every copy (a clean primary never hides a dirty replica).
6. steps      findings, remediation, verification (code/output.py order). COMPLETE cases cite the
              file chosen by the PLAN 5.8 citation order; INCOMPLETE cases cite findings only.

The cutoff is a hard rule everywhere: files captured after audit_at, records timed after it and
activity / audit-log rows after it are never used for that request.
"""

import csv
from collections import Counter, defaultdict

import activity
import evidence
import identity
import inference
import intent
import policy
import run_trace
from identity import at_or_before, to_time
from output import make_step, sort_steps

REQUEST_FILES = ["sample_deletion_requests.csv", "deletion_requests.csv"]

# Per rule action: violating states, compliant states, and the finding / fix / check operations.
VIOLATION_STATES = {"DELETE": {"EXPOSED", "CLEAN"}, "REDACT": {"EXPOSED"}, "ANONYMIZE": {"EXPOSED"},
                    "RETAIN": set(), "RETAIN_BACKUP": {"ACTIVE"}, "FLOW": {"ACTIVE"}}
COMPLIANT_STATES = {"DELETE": {"ABSENT"}, "REDACT": {"CLEAN"}, "ANONYMIZE": {"CLEAN"},
                    "RETAIN": {"EXPOSED", "CLEAN"}, "RETAIN_BACKUP": {"BLOCKED"}, "FLOW": {"DISABLED"}}
FINDING_OP = {"DELETE": "REPORT_REMAINING_RECORD", "REDACT": "REPORT_EXPOSED_PII", "ANONYMIZE": "REPORT_EXPOSED_PII",
              "RETAIN_BACKUP": "REPORT_RESTORE_RISK", "FLOW": "REPORT_ACTIVE_DATA_FLOW"}
FIX_OP = {"DELETE": "DELETE_RECORD", "REDACT": "REDACT_RECORD", "ANONYMIZE": "ANONYMIZE_RECORD",
          "RETAIN_BACKUP": "BLOCK_RESTORE", "FLOW": "DISABLE_DATA_FLOW"}
CHECK_OP = {"DELETE": "VERIFY_RECORD_ABSENT", "REDACT": "VERIFY_RECORD_REDACTED", "ANONYMIZE": "VERIFY_RECORD_ANONYMIZED",
            "RETAIN": "VERIFY_RECORD_PRESENT", "RETAIN_BACKUP": "VERIFY_RESTORE_BLOCKED", "FLOW": "VERIFY_DATA_FLOW_DISABLED"}
# PLAN 5.8 citation preference: (a) names the resource, (c) holds the case's other cited
# observations, (b) covers primary and replicas; then latest capture, then file_id.
CITATION_ORDER = "acb"


class UnsureIntent(Exception):
    """Some request texts could not be classified; the run must stop for review."""

    def __init__(self, cases):
        super().__init__(f"{len(cases)} request(s) with UNSURE intent: {', '.join(cases)}")
        self.cases = cases


# ---------------------------------------------------------------- observation helpers

def eligible(obs, cutoff):
    """Available at cutoff: the file was captured by then and the record is not timed after it."""
    return at_or_before(obs["file_captured_at"], cutoff) and \
        (obs["observed_at"] is None or at_or_before(obs["observed_at"], cutoff)) and \
        (not obs.get("alias_link_time") or at_or_before(obs["alias_link_time"], cutoff))


def obs_time(obs):
    return obs["observed_at"] or obs["file_captured_at"]


def copy_key(table, copy):
    """Physical copy name: 'primary' unless a replica table or copy label says otherwise."""
    if table:
        m = evidence.COPY_SUFFIX_RE.match(table)
        if m and m.group("copy") != "primary":
            return m.group("copy")
    return copy if copy and copy != "primary" else "primary"


def entry_order(e):
    # "evidence_04#10" must sort after "evidence_04#9", so the numeric suffix is compared as a number.
    file_id, _, number = e["obs_id"].partition("#")
    return to_time(e["time"]), file_id, int(number) if number.isdigit() else 0


def scope_absences(scope, resource_id, about_subject):
    """Tables of a scope proving resource_id absent (decision F3). SQL snapshot tables are complete.
    A JSON bundle collection is an extract about its own subjects (evidence_51 lists no profiles, yet
    evidence_92 shows account_5334 present at that time), so only an empty collection counts, and
    only in a bundle that holds a record tied to the subject (`about_subject`)."""
    tables = []
    for table, ids in scope["tables"].items():
        if resource_id in ids:
            continue
        if scope["file_type"] == "sql" or (not ids and about_subject):
            tables.append(table)
    return tables


def ties_to(obs, values):
    """The observation's identifiers or subject references hold one of the subject's values."""
    return bool(values & (set(evidence.flat_identifier_values(obs["identifiers"])) | set(obs["subject_refs"])))


class Index:
    """Observations and activity rows grouped by the resource IDs and identifier values they name."""

    def __init__(self, observations, activities):
        self.observations = {o["obs_id"]: o for o in observations}
        self.by_resource = defaultdict(list)
        self.by_value = defaultdict(list)  # identifier / subject ref / lookup key -> observations
        self.scopes = [o for o in observations if o["kind"] == "scope"]
        self.records_by_file = defaultdict(list)  # non-scope observations of each file
        aliases = defaultdict(list)
        for o in observations:
            if o["kind"] == "scope":
                continue
            self.records_by_file[o["file_id"]].append(o)
            if o["resource_id"]:
                self.by_resource[o["resource_id"]].append(o)
            values = set(evidence.flat_identifier_values(o["identifiers"])) | set(o["subject_refs"]) | set(o["lookup_keys"])
            for value in values:
                self.by_value[value].append(o)
            for alias in o["fields"].get("aliases", []):
                aliases[alias].append(o)
        self.activity = defaultdict(list)
        self.activity_by_value = defaultdict(list)
        for a in activities:
            for r in {a["resource_id"], *a["mentioned_resource_ids"]}:
                self.activity[r].append(a)
            for value in set(a["emails"] + a["user_ids"]):
                self.activity_by_value[value].append(a)
            # A catalog's explicit document alias binds an otherwise unnamed PDF to its resource.
            for o in aliases.get(a["link_alias"], []):
                if o["resource_id"] is None:
                    self.by_resource[a["resource_id"]].append(
                        dict(o, resource_id=a["resource_id"], system=a["target_system"],
                             alias_link_time=a["event_time"]))


def file_about_subject(index, file_id, values, cutoff):
    """A record of the file eligible at cutoff ties to the subject (bundle membership, PLAN F3)."""
    return any(eligible(o, cutoff) and ties_to(o, values) for o in index.records_by_file[file_id])


def state_entries(index, resource_id, system, cutoff, values):
    """Every eligible observation of the resource as {time, copy, state, obs_id, file_id, names},
    plus absences from snapshot scopes of `system` that do not list it."""
    entries = []
    for o in index.by_resource[resource_id]:
        if o["system"] == system and eligible(o, cutoff):
            entries.append({"time": obs_time(o), "copy": copy_key(o["table"], o["copy"]),
                            "state": policy.observed_state(o), "obs_id": o["obs_id"], "file_id": o["file_id"],
                            "names": not o.get("alias_link_time")})
    for s in index.scopes:
        if s["system"] != system or not eligible(s, cutoff):
            continue
        for table in scope_absences(s, resource_id, file_about_subject(index, s["file_id"], values, cutoff)):
            entries.append({"time": obs_time(s), "copy": copy_key(table, None), "state": "ABSENT",
                            "obs_id": s["obs_id"], "file_id": s["file_id"], "names": False})
    return entries


def latest_per_copy(entries):
    latest = {}
    for e in sorted(entries, key=entry_order):
        latest[e["copy"]] = e
    return latest


def citation_candidates(entries, needed):
    """file_id -> facts for the PLAN 5.8 preference, over files with an observation proving the state.
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
    """Best candidate under a preference order ("a" names the resource, "b" covers replicas, "c" also
    proves other states of the case), plus the actual comparison values used for the trace."""
    comparisons = {}
    labels = {"a": "names_resource", "b": "copy_coverage_preferred", "c": "other_resource_candidate_count"}
    def key(file_id):
        c = candidates[file_id]
        scores = {"a": c["names"], "b": len(c["copies"]) > 1 or "primary" not in c["copies"],
                  "c": others.get(file_id, 0)}
        comparison_key = tuple(scores[h] for h in order) + (captured[file_id], file_id)
        comparisons[file_id] = {"file_id": file_id, **{labels[h]: value for h, value in scores.items()},
                                "copies": sorted(c["copies"]), "observation_ids": sorted(c["obs"]),
                                "captured_at": captured[file_id], "comparison_key": list(comparison_key)}
        return comparison_key
    ranked = sorted(candidates, key=key, reverse=True)
    ranking = {"comparison_order": [labels[h] for h in order] + ["captured_at", "file_id"],
               "direction": "descending lexicographic",
               "copy_coverage_rule": "more than one copy OR primary not in copies",
               "ranked_candidates": [comparisons[fid] for fid in ranked]}
    return (ranked[0] if ranked else None), ranking


# ---------------------------------------------------------------- intent

def classify_intents(requests, classify=intent.classify):
    """Return case results and timings by unique text. Raises UnsureIntent if any is UNSURE."""
    texts, timings = {}, {}
    for number, text in enumerate(sorted({r["request_text"] for r in requests}), 1):
        started = run_trace.now()
        texts[text] = classify(text)
        timings[text] = {"started_at": started, "ended_at": run_trace.now(),
                         "computation_id": f"intent_{number}"}
    results = {r["case_id"]: texts[r["request_text"]] for r in requests}
    unsure = [case_id for case_id, result in results.items() if result["decision"] == "UNSURE"]
    if unsure:
        raise UnsureIntent(unsure)
    return results, timings


# ---------------------------------------------------------------- linking

def returns_resource(obs):
    """Decision F1: a lookup result returns the resource. For a log trace, a line naming the resource
    that is not the request line (event ending in _started); a JSON record is itself the result."""
    if obs["file_type"] != "log":
        return True
    for line in obs["source"].splitlines():
        parsed = evidence.parse_log_line(line)
        if parsed and parsed[3].get("resource_id") == obs["resource_id"] \
                and not str(parsed[3].get("event") or "").endswith("_started"):
            return True
    return False


def subject_links(index, values, cutoff):
    """(links, excluded) for a subject at cutoff. Each link is {resource_id, type, source, time, item}.
    `excluded` lists observations and activity rows that would link but come after the cutoff."""
    links, excluded = [], []
    seen = set()
    observations = [o for value in sorted(values) for o in index.by_value.get(value, [])]
    for o in observations:
        if o["obs_id"] in seen:
            continue
        seen.add(o["obs_id"])
        proven = ties_to(o, values)
        if not proven and not (o["resource_id"] and values & set(o["lookup_keys"]) and returns_resource(o)):
            continue
        if not eligible(o, cutoff):
            excluded.append(o["obs_id"])
            continue
        # Resources a proven record mentions are tied too; a lookup ties only the resource it returns.
        resource_ids = ([o["resource_id"]] if o["resource_id"] else []) + (o["related_resource_ids"] if proven else [])
        for resource_id in resource_ids:
            links.append({"resource_id": resource_id, "type": "proven" if proven else "lookup",
                          "source": o["obs_id"], "time": obs_time(o), "item": o})
    rows = {a["activity_id"]: a for value in values for a in index.activity_by_value.get(value, [])}
    for a in sorted(rows.values(), key=lambda a: (a["event_time"], activity.activity_number(a))):
        if not at_or_before(a["event_time"], cutoff):
            excluded.append(a["activity_id"])
            continue
        links.append({"resource_id": a["resource_id"], "type": "proven", "source": a["activity_id"],
                      "time": a["event_time"], "item": a})
    return links, excluded


def in_window(request, time):
    return at_or_before(request["requested_at"], time) and at_or_before(time, request["audit_at"])


def owning_requests(person_requests, time):
    """Decision F2 option B: the person's requests a link at `time` belongs to. Those whose window
    holds the time; otherwise the earliest request (by audit_at) that can see it."""
    inside = [r["case_id"] for r in person_requests if in_window(r, time)]
    if inside:
        return inside
    later = [r for r in person_requests if at_or_before(time, r["audit_at"])]
    return [min(later, key=lambda r: (to_time(r["audit_at"]), r["case_id"]))["case_id"]] if later else []


def assign_links(links, request, person_requests):
    """(kept, moved): links of this request, and links that belong to the person's other requests."""
    if len(person_requests) == 1:
        return links, []
    kept, moved = [], []
    for link in links:
        owners = owning_requests(person_requests, link["time"])
        if request["case_id"] in owners:
            kept.append(link)
        else:
            moved.append({**link, "owners": owners})
    return kept, moved


def request_links(request, user_id, values, cutoff, by_person, index, owners=None):
    """(kept, moved, excluded, person_requests): the request's proven and lookup links using only
    information available at cutoff, with the person's several requests split (decision F2).
    `owners` is identity.email_owners(cutoff) when the caller resolves many requests at one cutoff."""
    links, excluded = subject_links(index, values, cutoff)
    # Another request's identity must also be known at THIS cutoff before it can split history.
    person_requests = [r for r in by_person.get(user_id, [request])
                       if r is request or (at_or_before(r["requested_at"], cutoff)
                                           and identity.resolve_user(r["email"], cutoff, owners) == user_id)]
    kept, moved = assign_links(links, request, person_requests)
    return kept, moved, excluded, person_requests


# ---------------------------------------------------------------- inferred links (PLAN 6.3)

# The deletion worker's rows for a request's other resources fall within a few hours of reported_at.
FOLLOW_UP_EVENTS = {"DELETE_RECORD", "ANONYMIZE_RECORD", "REDACT_RECORD", "BLOCK_RESTORE",
                    "DISABLE_DATA_FLOW", "CHECK_DATA_FLOW", "CHECK_RESTORE_ACCESS"}
FOLLOW_UP_HOURS = 3


def owned_resources(index, cutoff):
    """Resource IDs that evidence available at cutoff ties to some person in user_identities.csv.
    A resource tied to anyone is never a candidate: it is proven for someone, not inferred."""
    owned = set()
    for user_id in identity.load_identities():
        values = set(evidence.flat_identifier_values(identity.subject_identifiers(user_id, cutoff)))
        owned.update(l["resource_id"] for l in subject_links(index, values, cutoff)[0])
    return owned


def followup_side(reported_at, resource_ids, index, cutoff):
    """-1 or +1 when the deletion worker's follow-up rows for these resources fall before or after
    reported_at; 0 when there are none, they are neutral, or (never in the data) on both sides."""
    sides = set()
    for resource_id in resource_ids:
        for a in index.activity.get(resource_id, []):
            if a["event_type"] in FOLLOW_UP_EVENTS and a["target_system"] != "account_service" \
                    and at_or_before(a["event_time"], cutoff):
                hours = (to_time(a["event_time"]) - to_time(reported_at)).total_seconds() / 3600
                if abs(hours) <= FOLLOW_UP_HOURS:
                    sides.add(inference.side_of(hours))
    sides.discard(0)
    return sides.pop() if len(sides) == 1 else 0


def infer_links(requests, intents, by_person, index, activities):
    """Inferred links for deletion requests, solved once per distinct audit_at with only the
    information available then: activity rows up to the cutoff, every request reported by then as
    a competitor, and every resource the evidence at that time ties to a person left out.
    Returns {case_id: {"links": [...], "kinds": {kind: {"status": "has_link" | "inferred" | "no_match", ...}}}}."""
    deletions = [r for r in requests if intents[r["case_id"]]["decision"] != "NOT_APPLICABLE"]
    rows = {kind: [{"activity_id": a["activity_id"], "resource_id": a["resource_id"], "item": a,
                    "time": to_time(a["event_time"]), "result": a["reported_result"]}
                   for a in activities if a["event_type"] == spec["event_type"]
                   and a["target_system"] == spec["target_system"] and a["resource_id"].startswith(spec["prefix"])]
            for kind, spec in inference.KINDS.items()}
    inferred = {}
    for cutoff in sorted({r["audit_at"] for r in deletions}, key=to_time):
        limit, owned, email_owners = to_time(cutoff), owned_resources(index, cutoff), identity.email_owners(cutoff)
        held, sides = {}, {}
        participants = [r for r in deletions if at_or_before(r["reported_at"], cutoff)]
        for r in participants:
            user_id = identity.resolve_user(r["email"], cutoff, email_owners)
            kept = []
            if user_id:
                values = set(evidence.flat_identifier_values(identity.subject_identifiers(user_id, cutoff)))
                kept = request_links(r, user_id, values, cutoff, by_person, index, email_owners)[0]
            held[r["case_id"]] = {l["resource_id"] for l in kept}
            sides[r["case_id"]] = followup_side(r["reported_at"], held[r["case_id"]], index, cutoff)
        targets = [r for r in participants if r["audit_at"] == cutoff]
        for r in targets:
            inferred[r["case_id"]] = {"links": [], "kinds": {}}
        for kind, spec in inference.KINDS.items():
            cases = [{"case_id": r["case_id"], "reported_at": to_time(r["reported_at"]),
                      "status": r["reported_status"], "side": sides[r["case_id"]]}
                     for r in participants if not any(h.startswith(spec["prefix"]) for h in held[r["case_id"]])]
            candidates = [row for row in rows[kind] if row["time"] <= limit and row["resource_id"] not in owned]
            outcomes = inference.assign(kind, cases, candidates)
            for r in targets:
                found = inferred[r["case_id"]]
                if r["case_id"] not in outcomes:
                    found["kinds"][kind] = {"status": "has_link"}
                    continue
                outcome = outcomes[r["case_id"]]
                found["kinds"][kind] = {"status": "inferred" if outcome["row"] else "no_match",
                                        "candidates": outcome["candidates"], "ambiguous": outcome["ambiguous"],
                                        "one_to_one_only": outcome["one_to_one_only"]}
                if outcome["row"]:
                    row = outcome["row"]
                    found["links"].append({
                        "resource_id": row["resource_id"], "type": "inferred", "source": row["activity_id"],
                        "time": row["item"]["event_time"], "item": row["item"],
                        "signals": {"kind": kind, "cost": outcome["cost"], "ambiguous": outcome["ambiguous"],
                                    "one_to_one_only": outcome["one_to_one_only"], **outcome["signals"]}})
    return inferred


# ---------------------------------------------------------------- policy and steps

def judge(action, latest):
    """'violation', 'compliant' or 'gap' (state unknown or not one the rule can reach) from the latest
    state of every physical copy."""
    states = {e["state"] for e in latest.values()}
    if action is None or not states:
        return "gap"
    if states & VIOLATION_STATES[action]:
        return "violation"
    compliant = COMPLIANT_STATES[action]
    if states & compliant and states <= compliant | {"ABSENT"}:
        return "compliant"
    return "gap"


def resource_system(index, resource_id, link_items, cutoff):
    own = sorted((o for o in index.by_resource[resource_id] if eligible(o, cutoff)),
                 key=lambda o: (to_time(obs_time(o)), o["obs_id"]), reverse=True)
    systems = [o["system"] for o in own]
    systems += [i["target_system"] for i in sorted(link_items, key=lambda i: i.get("activity_id", ""))
                if "activity_id" in i]
    systems += [a["target_system"] for a in sorted(index.activity[resource_id], key=activity.activity_number)
                if a["resource_id"] == resource_id and at_or_before(a["event_time"], cutoff)]
    return systems[0] if systems else None


def evaluate_resource(index, rules, resource_id, links, cutoff, values):
    """Everything the steps need for one linked resource."""
    system = resource_system(index, resource_id, [l["item"] for l in links], cutoff)
    own = [o for o in index.by_resource[resource_id] if eligible(o, cutoff)]
    rule, basis, action = policy.classify_rule(resource_id, own, rules)
    entries = state_entries(index, resource_id, system, cutoff, values)
    latest = latest_per_copy(entries)
    verdict = judge(action, latest) if system else "gap"
    if verdict == "violation":
        needed = VIOLATION_STATES[action]
    elif verdict == "compliant":
        needed = COMPLIANT_STATES[action]
    else:
        needed = set()
    return {"resource_id": resource_id, "system": system, "rule": rule, "basis": basis, "action": action,
            "link_type": next(t for t in ("proven", "lookup", "inferred") if any(l["type"] == t for l in links)),
            "links": [l["source"] for l in links], "latest": latest, "verdict": verdict,
            "state_observation_ids": sorted({e["obs_id"] for e in entries}),
            "policy_observation_ids": sorted({o["obs_id"] for o in own}),
            "candidates": citation_candidates(entries, needed) if needed else {}}


def build_steps(resources, captured):
    """(status, steps, citations) for a deletion case from its evaluated resources."""
    usable = [r for r in resources if r["verdict"] in ("violation", "compliant")]
    status = "INCOMPLETE" if any(r["verdict"] == "violation" for r in usable) else "COMPLETE"
    steps, citations = [], {}
    for r in usable:
        others = Counter(fid for other in usable if other is not r for fid in other["candidates"])
        cited, ranking = pick(r["candidates"], CITATION_ORDER, captured, others)
        cited = cited or ""
        system, resource_id, action = r["system"], r["resource_id"], r["action"]
        if r["verdict"] == "violation":
            steps += [make_step(FINDING_OP[action], system, resource_id, cited),
                      make_step(FIX_OP[action], system, resource_id),
                      make_step(CHECK_OP[action], system, resource_id)]
        else:
            steps.append(make_step(CHECK_OP[action], system, resource_id, cited if status == "COMPLETE" else ""))
        if r["verdict"] == "violation" or status == "COMPLETE":
            citations[resource_id] = {"cited": cited, "candidates": sorted(r["candidates"]), "ranking": ranking}
    return status, sort_steps(steps), citations


# ---------------------------------------------------------------- run

def read_requests():
    requests = []
    for name in REQUEST_FILES:
        with open(evidence.DATASET / name, encoding="utf-8", newline="") as f:
            requests += list(csv.DictReader(f))
    return requests


def subject_of(request):
    """(user_id, identifiers) at audit_at; an email resolving to nobody gets no identifiers."""
    user_id = identity.resolve_user(request["email"], request["audit_at"])
    if user_id is None:
        return None, {"emails": [], "phones": [], "email_hashes": [], "user_ids": []}
    return user_id, identity.subject_identifiers(user_id, request["audit_at"])


def run(requests=None, classify=intent.classify, *, llm_context=None):
    """Audit every request. Returns ({case_id: (status, steps)}, trace). Raises UnsureIntent before
    any work if some request intent is UNSURE. Without llm_context this is the keyword-only
    core diagnostic API. The CLI always prepares LLM context unless explicitly diagnostic."""
    requests = read_requests() if requests is None else requests
    trace = llm_context["trace"] if llm_context is not None else run_trace.new_trace()
    if llm_context is not None:
        classify = llm_context["decisions"].__getitem__
    intents, intent_timings = classify_intents(requests, classify)
    if llm_context is not None:
        intent_timings = llm_context["timings"]
    observations = evidence.load_observations()
    if llm_context is not None:
        observations += llm_context["observations"]
    activities = activity.load_activity()
    index = Index(observations, activities)
    rules = policy.load_rules()
    captured = {f["file_id"]: f["captured_at"] for f in evidence.load_manifest()}
    subjects = {}
    for r in requests:
        started = run_trace.now()
        user_id, ids = subject_of(r)
        cutoff = r["audit_at"]
        used_audits = [a for a in identity.load_audit_log()
                       if a["user_id"] == user_id and at_or_before(a["event_time"], cutoff)]
        subjects[r["case_id"]] = {
            "outputs": {"user_id": user_id, "identifiers": ids,
                        "unresolved_kinds": identity.unresolved_kinds(user_id, cutoff) if user_id else [],
                        "audit_ids": identity.audit_ids(user_id, cutoff) if user_id else []},
            "evidence": unique_refs(used_audits), "started_at": started, "ended_at": run_trace.now()}
    by_person = defaultdict(list)
    for r in requests:
        user_id = subjects[r["case_id"]]["outputs"]["user_id"]
        if user_id:
            by_person[user_id].append(r)

    inferred = infer_links(requests, intents, by_person, index, activities)
    trace["stage"] = "milestone_4_inferred_links" if llm_context is not None else "keyword_only_diagnostic"
    trace["submission_ready"] = False
    trace["inference"] = summarize_inference(inferred)
    trace["limitations"] = ["Inferred links can be wrong; each is tagged with its signals and measured separately.",
                            "Gaps are recorded per case; a missing link does not prove completeness."]
    results = {}
    for request in requests:
        results[request["case_id"]] = audit_case(request, intents[request["case_id"]],
                                                 intent_timings[request["request_text"]], subjects[request["case_id"]],
                                                 by_person, index, rules, captured, trace,
                                                 inferred.get(request["case_id"], {"links": [], "kinds": {}}))
    return results, trace


def summarize_inference(inferred):
    """Run-level counts per resource type: requests that needed an inferred link and what happened."""
    summary = {}
    for kind in inference.KINDS:
        found = [f["kinds"][kind] for f in inferred.values()]
        needing = [f for f in found if f["status"] != "has_link"]
        summary[kind] = {"deletion_requests": len(found), "had_link": len(found) - len(needing),
                         "inferred": sum(f["status"] == "inferred" for f in needing),
                         "no_match_with_candidates": sum(f["status"] == "no_match" and f["candidates"] > 0 for f in needing),
                         "no_candidates": sum(f["status"] == "no_match" and f["candidates"] == 0 for f in needing),
                         "ambiguous": sum(f["status"] == "inferred" and bool(f["ambiguous"]) for f in needing),
                         "one_to_one_only": sum(f["status"] == "inferred" and f["one_to_one_only"] for f in needing)}
    return summary


def audit_case(request, intent_result, intent_timing, subject, by_person, index, rules, captured, trace, inferred):
    case_id, cutoff = request["case_id"], request["audit_at"]
    llm_details = {}
    if "llm_key" in intent_result:
        llm_details["llm"] = trace["llm"]["results"][intent_result["llm_key"]]
    run_trace.record(trace, case_id, "intent", inputs={"request_text": request["request_text"]},
                     outputs=intent_result, decided_by=intent_result.get("method", "keyword rules (diagnostic)"),
                     **intent_timing, **llm_details)
    if intent_result["decision"] == "NOT_APPLICABLE":
        run_trace.record(trace, case_id, "steps",
                         inputs={"intent_decision": intent_result["decision"],
                                 "intent_computation_id": intent_timing["computation_id"]},
                         outputs={"status": "NOT_APPLICABLE", "steps": []})
        return "NOT_APPLICABLE", []

    user_id, ids = subject["outputs"]["user_id"], subject["outputs"]["identifiers"]
    values = set(evidence.flat_identifier_values(ids))
    gaps = []
    if user_id is None:
        gaps.append("identity: the request email resolves to nobody at audit_at")
    run_trace.record(trace, case_id, "identity", inputs={"email": request["email"], "audit_at": cutoff},
                     **subject)

    started = run_trace.now()
    kept, moved, excluded, person_requests = request_links(request, user_id, values, cutoff, by_person, index)
    kept = kept + inferred["links"]  # owned resources are never candidates, so no overlap
    by_resource = defaultdict(list)
    for link in kept:
        by_resource[link["resource_id"]].append(link)
    run_trace.record(
        trace, case_id, "linking",
        inputs={"identifier_values": len(values), "identifiers_source": "identity.outputs.identifiers",
                "audit_at": cutoff, "person_requests": sorted(r["case_id"] for r in person_requests)},
        outputs={"links": [{k: v for k, v in l.items() if k != "item"} for l in kept],
                 "moved_to_other_requests": [{k: v for k, v in l.items() if k != "item"} for l in moved],
                 "excluded_after_cutoff": excluded, "inference": inferred["kinds"]},
        evidence=unique_refs(l["item"] for l in kept), started_at=started)

    started = run_trace.now()
    resources = [evaluate_resource(index, rules, rid, by_resource[rid], cutoff, values) for rid in sorted(by_resource)]
    excluded_state = sorted({o["obs_id"] for rid in by_resource for o in index.by_resource[rid]
                             if not eligible(o, cutoff)})
    run_trace.record(trace, case_id, "cutoff", inputs={"audit_at": cutoff},
                     outputs={"excluded_link_sources": excluded,
                              "excluded_resource_observations": excluded_state})
    state_obs = {e["obs_id"] for r in resources for e in r["latest"].values()}
    run_trace.record(trace, case_id, "state", started_at=started,
                     inputs={"audit_at": cutoff, "identifiers_source": "identity.outputs.identifiers",
                             "resources": {r["resource_id"]: {"system": r["system"], "link_sources": r["links"],
                                            "observation_ids": r["state_observation_ids"]} for r in resources}},
                     evidence=unique_refs(index.observations[oid] for oid in sorted(state_obs)),
                     outputs={r["resource_id"]: {
        "system": r["system"], "latest_per_copy": {c: {"state": e["state"], "obs_id": e["obs_id"], "time": e["time"]}
                                                   for c, e in r["latest"].items()}} for r in resources})
    run_trace.record(trace, case_id, "policy",
                     inputs={"rules_source": "dataset/deletion_rules.csv",
                             "rule_ids": sorted(rule["rule_id"] for rule in rules.values()),
                             "resources": {r["resource_id"]: {
                                 "observation_ids": r["policy_observation_ids"],
                                 "latest_copy_observations": {c: e["obs_id"] for c, e in r["latest"].items()}}
                                 for r in resources}},
                     outputs={r["resource_id"]: {
        "rule": r["rule"], "basis": r["basis"], "action": r["action"], "verdict": r["verdict"]} for r in resources})

    for r in resources:
        if r["verdict"] == "gap":
            gaps.append(f"{r['resource_id']}: no usable state for rule action {r['action']} "
                        f"(latest {sorted({e['state'] for e in r['latest'].values()})})")
    if not any(r["action"] == "DELETE" and (r["system"] == "account_service") for r in resources):
        gaps.append("no account_service profile linked (no proven, lookup or inferred link)")
    started = run_trace.now()
    status, steps, citations = build_steps(resources, captured)
    if not steps:
        gaps.append("no steps: nothing linked has a usable state")
    cited_obs = []
    for r in resources:
        cited = citations.get(r["resource_id"], {}).get("cited")
        if cited:
            cited_obs.extend(r["candidates"][cited]["obs"])
    run_trace.record(trace, case_id, "steps", started_at=started,
                     inputs={"policy_source": "policy.outputs", "capture_times_source": "dataset/evidence_files.csv",
                             "resources": {r["resource_id"]: {"system": r["system"], "action": r["action"],
                                            "verdict": r["verdict"], "candidate_file_ids": sorted(r["candidates"])}
                                           for r in resources}},
                     outputs={"status": status, "steps": steps, "citations": citations,
                              "resource_links": {r["resource_id"]: r["link_type"] for r in resources}, "gaps": gaps},
                     evidence=unique_refs(index.observations[oid] for oid in cited_obs))
    return status, steps


def unique_refs(items):
    refs, seen = [], set()
    for item in items:
        ref = run_trace.evidence_ref(item)
        key = ref.get("obs_id") or ref["id"]
        if key not in seen:
            seen.add(key)
            refs.append(ref)
    return refs
