"""Policy mapping (PLAN section 5.6): which deletion_rules.csv rule applies to a record, and what
state an observation shows it in.

A record is mapped by its content and collection context (an explicit record_type, a contact
lookup, a support attachment), with the resource ID prefix only as the last fallback; never by
the service alone (sample 12: an analytics contact lookup is DELETE, an analytics profile is
ANONYMIZE). Observed states:

    ABSENT    the record is not there (404, rows=0, missing from a complete snapshot)
    EXPOSED   present with direct identifiers (name, email, phone, email hash)
    CLEAN     present without direct identifiers (redacted, anonymized, a clean tombstone)
    ACTIVE / DISABLED   a data flow; ACTIVE / BLOCKED   a backup's restore path
"""

import csv
import json

from evidence import DATASET

NAME_FIELDS = {"full_name", "name", "display_name", "requester_name", "customer_name"}
PREFIX_CATEGORY = {
    "account": "USER_PROFILE", "search": "SEARCH_INDEX_ENTRY", "export": "USER_EXPORT",
    "audit_record": "SECURITY_AUDIT_LOG", "backup": "BACKUP_ARCHIVE", "tombstone": "ACCOUNT_TOMBSTONE",
    "cache": "CACHE_ENTRY", "marketing": "MARKETING_PROFILE", "email_delivery": "EMAIL_DELIVERY_LOG",
    "legal_hold": "LEGAL_HOLD_RECORD",
    # object_<n> is the object-storage copy of a support attachment (activity_4007 "belongs only to").
    "object": "SUPPORT_ATTACHMENT",
}


def load_rules():
    with open(DATASET / "deletion_rules.csv", encoding="utf-8", newline="") as f:
        return {r["data_category"]: r for r in csv.DictReader(f)}


def truthy(value):
    return str(value).lower() in ("true", "1")


def has_direct_identifiers(obs):
    """Name, email, phone or email hash in the record. A user_id alone is the permitted stable
    reference (account tombstones), so it does not count."""
    ids = obs["identifiers"]
    names = any(obs["fields"].get(k) for k in NAME_FIELDS)
    nested = any(isinstance(v, dict) and any(v.get(k) for k in NAME_FIELDS) for v in obs["fields"].values())
    return bool(ids["emails"] or ids["phones"] or ids["email_hashes"] or names or nested)


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
    if f.get("decision") == "deny" and "restore_authorization_completed" in f.get("events", []):
        return "BLOCKED"
    return "EXPOSED" if has_direct_identifiers(obs) else "CLEAN"


def is_contact_record(obs):
    """A contact lookup record: email opt-in flag, or read from a contact index / contact query."""
    f = obs["fields"]
    sources = [f.get("source"), (f.get("request") or {}).get("source")] + (f.get("events") or [])
    return "email_opt_in" in f or any(v and str(v).startswith(("contact_", "legacy_contact")) for v in sources)


def classify_rule(resource_id, observations, rules):
    """(rule label, basis, action) for a resource from its eligible observations. Action is a
    rule's required_action, or RETAIN_BACKUP (a backup archive whose restore path can be blocked),
    or FLOW (a data flow, which has no data category but can recreate data). Basis says why."""
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
