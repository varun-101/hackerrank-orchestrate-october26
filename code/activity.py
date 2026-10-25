"""Load dataset/system_activity.csv as a list of dicts sorted by event_time.

Each row keeps its CSV columns (reported_result stays a claim, never proof of state) plus
values extracted from the free-text `details`:

    emails, user_ids         identities named in the text (e.g. data_catalog link rows)
    mentioned_resource_ids   every resource ID named in the text
    downstream_resource_id   "... repopulated downstream resource X" (SYNC_RECORD, RESTORE_BACKUP)
    target_resource_id       "... Target resource: X"
    link_alias               the parenthesised alias in a data_catalog link, e.g. "NST-2026-0718-1842"
    exclusive_owner          True for "belongs only to ..." statements (rule_06 condition)

Run `python code/activity.py --report` for a short extraction summary.
"""

import csv
import re
import sys
from collections import Counter

from evidence import DATASET, EMAIL_RE, RESOURCE_ID_RE, USER_ID_RE

DOWNSTREAM_RE = re.compile(r"repopulated downstream resource (\w+)")
TARGET_RE = re.compile(r"Target resource: (\w+)")
LINK_ALIAS_RE = re.compile(r"^(?:Data|Attachment) catalog (?:linked|confirmed) \w+ \(([^)]*)\)")


def parse_details(details):
    downstream = DOWNSTREAM_RE.search(details)
    target = TARGET_RE.search(details)
    alias = LINK_ALIAS_RE.search(details)
    return {
        "emails": sorted({e.lower() for e in EMAIL_RE.findall(details)}),
        "user_ids": sorted(set(USER_ID_RE.findall(details))),
        "mentioned_resource_ids": sorted(set(RESOURCE_ID_RE.findall(details))),
        "downstream_resource_id": downstream.group(1) if downstream else None,
        "target_resource_id": target.group(1) if target else None,
        "link_alias": alias.group(1) if alias else None,
        "exclusive_owner": "belongs only to" in details,
    }


def activity_number(row):
    return int(row["activity_id"].split("_")[1])


def load_all_activity():
    """Every row, CLASSIFY_REQUEST included. Offline development checks only, never the pipeline."""
    with open(DATASET / "system_activity.csv", encoding="utf-8", newline="") as f:
        rows = [{**row, **parse_details(row["details"])} for row in csv.DictReader(f)]
    # activity_id order is not time order (activity_4000..4011 were appended later).
    rows.sort(key=lambda row: (row["event_time"], activity_number(row)))
    return rows


def load_activity():
    """Pipeline input: CLASSIFY_REQUEST rows are excluded (PLAN 5.2); they name accounts that only
    evidence captured after every cutoff maps to emails."""
    return [row for row in load_all_activity() if row["event_type"] != "CLASSIFY_REQUEST"]


def report():
    rows = load_activity()
    excluded = sum(r["event_type"] == "CLASSIFY_REQUEST" for r in load_all_activity())
    print(f"{len(rows)} pipeline activity rows ({excluded} CLASSIFY_REQUEST rows excluded), "
          f"{rows[0]['event_time']} .. {rows[-1]['event_time']}")
    print("By event_type:", dict(sorted(Counter(r["event_type"] for r in rows).items())))
    print("Rows naming an email:", sum(bool(r["emails"]) for r in rows))
    print("Rows naming a user_id:", sum(bool(r["user_ids"]) for r in rows))
    print("Rows with downstream_resource_id:", sum(bool(r["downstream_resource_id"]) for r in rows))
    print("Rows with target_resource_id:", sum(bool(r["target_resource_id"]) for r in rows))
    print("Rows with exclusive_owner:", sum(r["exclusive_owner"] for r in rows))
    print("Data catalog link rows:")
    for r in rows:
        if r["event_type"] == "LINK_RESOURCE_IDENTITY":
            print(f"  {r['activity_id']} {r['event_time']} {r['resource_id']} alias={r['link_alias']} "
                  f"user_ids={r['user_ids']} emails={len(r['emails'])} exclusive={r['exclusive_owner']}")


if __name__ == "__main__":
    if "--report" in sys.argv:
        report()
    else:
        print(f"{len(load_activity())} activity rows. Use --report for an extraction summary.")
