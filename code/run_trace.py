"""Per-case run trace, written to output/trace.json for the review page.

Tracing only observes: stages call record() after deciding, and nothing reads the trace back
to make a decision. Event data is deep-copied when recorded, so later changes by a stage never
alter an earlier event. (Named run_trace so it does not shadow the standard-library `trace`.)

    trace = new_trace()
    started = now()
    ...stage work...
    record(trace, "case_33", "identity", inputs={...}, outputs={...},
           evidence=[evidence_ref(obs)], started_at=started)
    write_trace(trace)
"""

import copy
import json
from datetime import datetime, timezone

from evidence import REPO_ROOT

TRACE_PATH = REPO_ROOT / "output" / "trace.json"


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def new_trace():
    return {"created_at": now(), "cases": {}}


def record(trace, case_id, stage, *, inputs=None, outputs=None, evidence=(), started_at=None,
           ended_at=None, **extra):
    """Append one stage event to a case. `extra` holds optional fields (e.g. LLM call details)."""
    events = trace["cases"].setdefault(case_id, [])
    ended_at = ended_at or now()
    event = {
        "order": len(events) + 1,
        "stage": stage,
        "started_at": started_at or ended_at,
        "ended_at": ended_at,
        "inputs": {} if inputs is None else inputs,
        "outputs": {} if outputs is None else outputs,
        "evidence": list(evidence),
        **extra,
    }
    event = copy.deepcopy(event)
    events.append(event)
    return event


def evidence_ref(item):
    """Citation entry for an observation (file evidence) or an activity / audit-log row."""
    if "obs_id" in item:
        return {"id": item["file_id"], "obs_id": item["obs_id"], "source": item["source"]}
    if "activity_id" in item:
        return {"id": item["activity_id"], "source": item.get("details", "")}
    return {"id": item["audit_id"], "source": json.dumps(item, ensure_ascii=False)}


def write_trace(trace, path=TRACE_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(trace, indent=1, ensure_ascii=False), encoding="utf-8")
    return path
