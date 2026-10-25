"""Inferred resource links by assignment (PLAN section 6.3).

Some resources a request needs are written next to nobody: a deleted account survives only in a
row such as "Deletion worker submitted primary profile account_4857", and an already-anonymized
analytics record only in an ANONYMIZE_RECORD row. assign() decides which request such a row
belongs to. It is a pure function of the cases and rows it is given (no dataset reads, no sample
answers), so the caller controls exactly which information is available.

    outcomes = assign("account", cases, rows)
    # {case_id: {"row": <row or None>, "cost": 0.0, "signals": {...}, "ambiguous": [...],
    #            "one_to_one_only": False}}

"ambiguous" lists the other choices (activity ids or "no match") whose best total cost is within
AMBIGUOUS_MARGIN of the chosen total; "one_to_one_only" is True when the row differs from the one
the case would take alone (lowest cost, then earliest), i.e. the sharing rule changed the answer.

case: {"case_id", "reported_at": datetime, "status": reported_status, "side": -1 | 0 | 1}
row:  {"activity_id", "resource_id", "time": datetime, "result": reported_result}

Cost of giving a row to a case (hours are the row's event time minus the case's reported_at):

    time      TIME_WEIGHT per hour outside the type's window. The samples' account rows lie between
              -1.23 h and +0.13 h of reported_at and their anonymized analytics rows between
              -0.48 h and -0.11 h; the windows add a margin and are the same for every case.
    status    account rows only: STATUS_WEIGHT when reported_result is not the result implied by
              reported_status (COMPLETE/SUCCESS, PARTIALLY_COMPLETE/PARTIAL, NOT_STARTED/FAILED).
              Samples: 29 of 31 account rows agree. Analytics rows ignore it: 3 of the 7 sample
              rows (cases 25, 26, 28) are SUCCESS under NOT_STARTED.
    side      analytics rows only: SIDE_WEIGHT when the row and the case's proven follow-up rows
              (deletion worker rows of its other resources, caller-computed) fall on opposite
              sides of reported_at. In the dataset no request has follow-up rows on both sides (393
              of 393). Account rows are exempt: they precede the follow-ups of every sample.

A case can always take "no inferred link" at NO_MATCH, so a row is used only if its total cost is
below it: 0.5 h outside the window, or a side conflict, rejects the row on its own. Costs are
integers in hundredths so equal candidates tie exactly; ties are broken by (case number, then
activity time and number), earliest first. Rows are shared one-to-one, solved per connected group
of cases and rows with scipy.optimize.linear_sum_assignment.
"""

from bisect import bisect_left, bisect_right
from datetime import timedelta

from scipy.optimize import linear_sum_assignment

UNIT = 100                # costs are integers in hundredths of a cost unit
NO_MATCH = 1 * UNIT
AMBIGUOUS_MARGIN = UNIT // 10   # another choice within 0.10 of the best total makes a link ambiguous
TIME_WEIGHT = 2.0         # cost units per hour outside the window
STATUS_WEIGHT = 0.5
SIDE_WEIGHT = 1.5
NEUTRAL_HOURS = 0.25      # within this of reported_at a row counts as on neither side

# Per resource type: the activity row that names it, its time window, and which soft costs apply.
KINDS = {
    "account": {"prefix": "account_", "event_type": "DELETE_RECORD", "target_system": "account_service",
                "window": (-1.5, 0.5), "status": True, "side": False},
    "analytics": {"prefix": "analytics_", "event_type": "ANONYMIZE_RECORD", "target_system": "analytics_platform",
                  "window": (-0.75, 0.0), "status": False, "side": True},
}
STATUS_RESULT = {"COMPLETE": "SUCCESS", "PARTIALLY_COMPLETE": "PARTIAL", "NOT_STARTED": "FAILED"}


def side_of(hours):
    """-1 before reported_at, +1 after, 0 when within NEUTRAL_HOURS of it."""
    return 0 if abs(hours) <= NEUTRAL_HOURS else (1 if hours > 0 else -1)


def pair_cost(kind, case, row):
    """(cost in hundredths, signals) of giving the row to the case."""
    spec = KINDS[kind]
    hours = (row["time"] - case["reported_at"]).total_seconds() / 3600
    low, high = spec["window"]
    cost = TIME_WEIGHT * max(low - hours, hours - high, 0)
    status_agrees = STATUS_RESULT[case["status"]] == row["result"]
    side_conflict = case["side"] * side_of(hours) == -1
    if spec["status"] and not status_agrees:
        cost += STATUS_WEIGHT
    if spec["side"] and side_conflict:
        cost += SIDE_WEIGHT
    signals = {"hours_from_reported_at": round(hours, 2), "result": row["result"],
               "status_agrees": status_agrees, "followup_side": case["side"], "side_conflict": side_conflict}
    return round(cost * UNIT), signals


def optimum(case_ids, activity_ids, cost):
    """Lowest total cost when each case takes one distinct row or no match (missing pairs cost NO_MATCH)."""
    if not case_ids:
        return 0
    matrix = [[cost.get((c, a), NO_MATCH) for a in activity_ids] + [NO_MATCH] * len(case_ids) for c in case_ids]
    rows, columns = linear_sum_assignment(matrix)
    return int(sum(matrix[i][j] for i, j in zip(rows, columns)))


def groups(edges):
    """Connected groups of ([case_id], [activity_id]) in the case-row graph, in stable order."""
    parent = {}
    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for case_id, candidates in edges.items():
        for activity_id in candidates:
            parent[find(("c", case_id))] = find(("r", activity_id))
    found = {}
    for kind, name in list(parent):
        found.setdefault(find((kind, name)), ([], []))[0 if kind == "c" else 1].append(name)
    return list(found.values())


def solve_group(case_ids, activity_ids, cost):
    """({case_id: chosen activity_id or None}, optimum). Among equally cheap assignments the lowest
    case takes the earliest row it can (case_ids and activity_ids arrive in tie-break order)."""
    best = optimum(case_ids, activity_ids, cost)
    chosen, spent = {}, 0
    cases_left, rows_left = list(case_ids), list(activity_ids)
    for case_id in case_ids:
        cases_left.remove(case_id)
        for activity_id in [a for a in rows_left if (case_id, a) in cost] + [None]:
            price = cost[(case_id, activity_id)] if activity_id else NO_MATCH
            rest = [a for a in rows_left if a != activity_id]
            if spent + price + optimum(cases_left, rest, cost) == best:
                chosen[case_id], spent, rows_left = activity_id, spent + price, rest
                break
    return chosen, best


def assign(kind, cases, rows):
    """Outcome for every case (see the module docstring); rows are shared one-to-one."""
    cases = sorted(cases, key=lambda c: (_number(c["case_id"]), c["case_id"]))
    rows = sorted(rows, key=lambda r: (r["time"], _number(r["activity_id"]), r["activity_id"]))
    by_id = {r["activity_id"]: r for r in rows}
    times = [r["time"] for r in rows]
    low, high = KINDS[kind]["window"]
    reach = timedelta(hours=NO_MATCH / UNIT / TIME_WEIGHT)  # farther outside the window costs >= NO_MATCH
    cost, signals, edges = {}, {}, {}
    for case in cases:
        start = bisect_left(times, case["reported_at"] + timedelta(hours=low) - reach)
        stop = bisect_right(times, case["reported_at"] + timedelta(hours=high) + reach)
        edges[case["case_id"]] = []
        for row in rows[start:stop]:
            price, why = pair_cost(kind, case, row)
            if price < NO_MATCH:
                key = (case["case_id"], row["activity_id"])
                cost[key], signals[key] = price, why
                edges[case["case_id"]].append(row["activity_id"])

    order = {r["activity_id"]: i for i, r in enumerate(rows)}
    outcomes = {c["case_id"]: {"row": None, "cost": None, "signals": {}, "ambiguous": [], "one_to_one_only": False,
                               "candidates": len(edges[c["case_id"]])} for c in cases}
    case_order = {c["case_id"]: i for i, c in enumerate(cases)}
    for group_cases, group_rows in groups({c: a for c, a in edges.items() if a}):
        group_cases.sort(key=case_order.get)
        group_rows.sort(key=order.get)
        chosen, best = solve_group(group_cases, group_rows, cost)
        for case_id in group_cases:
            activity_id = chosen[case_id]
            options = edges[case_id] + [None]
            alternatives = []
            for other in options:
                if other == activity_id:
                    continue
                price = cost[(case_id, other)] if other else NO_MATCH
                rest_cases = [c for c in group_cases if c != case_id]
                forced = price + optimum(rest_cases, [a for a in group_rows if a != other], cost)
                if forced - best <= AMBIGUOUS_MARGIN:
                    alternatives.append(other or "no match")
            own = cost[(case_id, activity_id)] if activity_id else NO_MATCH
            outcomes[case_id].update(
                row=by_id[activity_id] if activity_id else None, cost=own / UNIT, ambiguous=alternatives,
                signals=signals.get((case_id, activity_id), {}),
                one_to_one_only=bool(activity_id) and activity_id != min(
                    edges[case_id], key=lambda a: (cost[(case_id, a)], order[a])))
    return outcomes


def _number(identifier):
    return int(identifier.rsplit("_", 1)[1])
