"""Resolve a request email to its subject and the subject's identifiers at a cutoff time.

The request email may be the person's current email (user_identities.csv) or an older one
(user_audit_log.csv). Only audit-log rows with event_time <= cutoff are used, plus the current
snapshot when its updated_at <= cutoff (a later snapshot may hold values the person did not have yet).
unresolved_kinds() names the identifier kinds with no value known at the cutoff. Identifiers are normalized the same way as evidence.extract_identifiers, so they can be
compared with an observation's identifiers directly.

    user_id = resolve_user("leonie.g9352@outlook.com", "2026-09-15T12:00:00Z")
    ids = subject_identifiers(user_id, "2026-09-15T12:00:00Z")
    # {"emails": [...], "phones": [...], "email_hashes": [...], "user_ids": ["user_17"]}
"""

import csv
from datetime import datetime, timezone
from functools import cache

from evidence import DATASET, normalize_phone

# user_audit_log field_name -> identifier kind
AUDIT_FIELDS = {"email": "emails", "phone": "phones", "email_hash": "email_hashes"}


def to_time(text):
    """Parse an ISO timestamp or date ("2026-09-05") as an aware UTC datetime."""
    value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def at_or_before(text, cutoff):
    return to_time(text) <= to_time(cutoff)


@cache
def load_identities():
    with open(DATASET / "user_identities.csv", encoding="utf-8", newline="") as f:
        return {row["user_id"]: row for row in csv.DictReader(f)}


@cache
def load_audit_log():
    with open(DATASET / "user_audit_log.csv", encoding="utf-8", newline="") as f:
        return tuple(csv.DictReader(f))


@cache
def audit_by_user():
    rows = {}
    for row in load_audit_log():
        rows.setdefault(row["user_id"], []).append(row)
    return rows


def audit_rows(user_id):
    return audit_by_user().get(user_id, [])


def normalize(kind, value):
    if kind == "phones":
        return normalize_phone(value)
    return value.strip().lower()


def snapshot_known(row, cutoff):
    """The current snapshot describes the person at cutoff only if it was last updated by then."""
    return at_or_before(row["updated_at"], cutoff)


def email_owners(cutoff):
    """{normalized email: {user_id}} known at cutoff: snapshot emails (snapshot updated by then) and
    every old / new email of an audit row at or before it."""
    owners = {}
    for uid, row in load_identities().items():
        if snapshot_known(row, cutoff):
            owners.setdefault(normalize("emails", row["email"]), set()).add(uid)
    for row in load_audit_log():
        if row["field_name"] == "email" and at_or_before(row["event_time"], cutoff):
            for value in filter(None, (row["old_value"], row["new_value"])):
                owners.setdefault(normalize("emails", value), set()).add(row["user_id"])
    return owners


def resolve_user(email, cutoff, owners=None):
    """user_id whose snapshot email (snapshot updated at or before cutoff), or an email in an audit row
    at or before cutoff, is `email`. Returns None when nobody matches. Raises if two different people
    match (never seen in the data). Callers resolving many emails at one cutoff pass owners=email_owners(cutoff)."""
    email = normalize("emails", email)
    matches = (email_owners(cutoff) if owners is None else owners).get(email, set())
    if len(matches) > 1:
        raise ValueError(f"{email} matches several users: {sorted(matches)}")
    return next(iter(matches), None)


def subject_identifiers(user_id, cutoff):
    """Snapshot identifiers (if the snapshot is not newer than cutoff) plus every old/new value from
    audit rows at or before cutoff. The latest eligible new_value is the value held at cutoff."""
    found = {"emails": set(), "phones": set(), "email_hashes": set(), "user_ids": {user_id}}
    row = load_identities()[user_id]
    if snapshot_known(row, cutoff):
        for field, kind in AUDIT_FIELDS.items():
            if row[field]:
                found[kind].add(normalize(kind, row[field]))
    for audit in load_audit_log():
        if audit["user_id"] == user_id and audit["field_name"] in AUDIT_FIELDS and \
                at_or_before(audit["event_time"], cutoff):
            kind = AUDIT_FIELDS[audit["field_name"]]
            found[kind].update(normalize(kind, v) for v in (audit["old_value"], audit["new_value"]) if v)
    return {k: sorted(v) for k, v in found.items()}


def audit_ids(user_id, cutoff):
    """audit_id of every identity change used for the subject at the cutoff (for citations/trace)."""
    return [a["audit_id"] for a in load_audit_log()
            if a["user_id"] == user_id and at_or_before(a["event_time"], cutoff)]


def unresolved_kinds(user_id, cutoff):
    """Identifier kinds whose value at cutoff is unknown: the snapshot is newer than cutoff and no
    audit row at or before cutoff records the field."""
    if snapshot_known(load_identities()[user_id], cutoff):
        return []
    known = {a["field_name"] for a in load_audit_log()
             if a["user_id"] == user_id and at_or_before(a["event_time"], cutoff)}
    return [kind for field, kind in AUDIT_FIELDS.items() if field not in known]
