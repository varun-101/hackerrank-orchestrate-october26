"""Load every evidence file listed in dataset/evidence_files.csv into one flat list of observations.

An observation is a plain dict describing what one evidence file says about one resource
(or, for "scope" observations, which resources a snapshot of a collection contains):

    obs_id              "<file_id>#<n>", stable within a run
    file_id, file_type, file_captured_at (manifest captured_at)
    kind                "record"   the resource was seen
                        "absence"  a lookup for the resource returned 404 / rows=0 / NOT_FOUND
                        "scope"    a snapshot of a system's collection(s); "tables" maps each
                                   table/collection (one per physical copy) to the resource IDs it
                                   contains, so an empty list proves nothing of that system exists there
    system              output system_id (grounded service aliases normalized, see SERVICE_ALIASES)
    raw_system          the service name exactly as the evidence wrote it
    collection, data_store, record_type, table, resource_id
    copy                physical copy ("failover_replica", "support_replica", ...); None for the primary
    fields              the record's own values (JSON object, merged log key/values, SQL row). Logs add
                        fields["trace_id"] (the parsed trace, if the line had one) and
                        fields["request"] (values of the request lines, lookup-only)
    identifiers         {"emails", "phones", "email_hashes", "user_ids"} found in the returned content
    subject_refs        identifiers held by explicit subject-association fields (subject_reference,
                        customer_reference, ...) in the returned content; kept on absences too
    lookup_keys         identifiers that were only used to look the record up (never proof of ownership)
    related_resource_ids  other resource IDs mentioned in the record
    observed_at         record-level time (observed_at, updated_at, log timestamp, ...) or None
                        (None: only the manifest captured_at dates it)
    source              exact source text (JSON object, the trace's log lines, the SQL INSERT line)

Run `python code/evidence.py --report` for a per-file coverage report.
"""

import csv
import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

from pypdf import PdfReader

REPO_ROOT = Path(__file__).resolve().parent.parent
DATASET = REPO_ROOT / "dataset"

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
USER_ID_RE = re.compile(r"\buser_\d+\b")
EMAIL_HASH_RE = re.compile(r"^[0-9a-fA-F]{64}$")
PHONE_RE = re.compile(r"\+\d[\d ()\-]{6,}\d")
# Resource-ID families seen in the participant data (user_<n> is an identity, not a resource).
RESOURCE_ID_RE = re.compile(
    r"\b(?:account|analytics|search|support|export|data_flow|backup|audit_record|legal_hold|"
    r"tombstone|cache|email_delivery|marketing|object)_\d+\b"
)
# Fields whose value is the key a lookup was made with, not something the record contains.
LOOKUP_FIELDS = {"lookup_key", "match_key", "subject_key", "scan_key", "owner_key",
                 "correlation_key", "query", "lookup_value"}
# Fields stating which subject a record (or an absence result) belongs to.
SUBJECT_FIELDS = {"subject_reference", "customer_reference", "subject_ref", "subject_selector", "user_id"}
TIME_KEYS = ("observed_at", "updated_at", "recorded_at", "indexed_at", "captured_at")

# Service names that are aliases of an output system_id, with the physical copy they denote.
# Only aliases the participant data grounds are listed; others stay raw (see the coverage report).
SERVICE_ALIASES = {
    # system_activity.csv: every profile_service row updates an account_* resource whose
    # target_system is account_service.
    "profile_service": ("account_service", None),
    # support_replica.log: a replica of support_platform (activity targets support_platform for
    # support_002); kept as its own physical copy, never merged into the primary.
    "support_replica": ("support_platform", "support_replica"),
}

# Inventory SQL tables and the system each one holds (the schema does not say it explicitly).
TABLE_SYSTEMS = {
    "customer_profiles": "account_service",
    "customer_search_documents": "search_service",
    "search_documents": "search_service",
    "customer_analytics_profiles": "analytics_platform",
    "support_records": "support_platform",
    "customer_exports": "export_service",
    "connector_subscriptions": "integration_service",
    "backup_restore_entries": "backup_service",
    "security_audit_records": "security_service",
}
COPY_SUFFIX_RE = re.compile(
    r"^(?P<base>.+?)_(?P<copy>primary|read_replica|reporting_replica|failover_replica|replica_[a-z_]+)$"
)


# ---------------------------------------------------------------- shared helpers

def load_manifest():
    with open(DATASET / "evidence_files.csv", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def string_values(value, key=None):
    """Yield (key, string) for every string inside a JSON-like value."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield from string_values(v, k)
    elif isinstance(value, list):
        for item in value:
            yield from string_values(item, key)
    elif isinstance(value, str):
        yield key, value


def extract_identifiers(value):
    """Return normalized identifiers found anywhere in a JSON-like value or a string."""
    found = {"emails": set(), "phones": set(), "email_hashes": set(), "user_ids": set()}
    for key, text in string_values(value):
        found["emails"].update(e.lower() for e in EMAIL_RE.findall(text))
        found["user_ids"].update(USER_ID_RE.findall(text))
        if EMAIL_HASH_RE.match(text):
            found["email_hashes"].add(text.lower())
        phones = PHONE_RE.findall(text)
        if not phones and key and "phone" in key and text not in ("null", ""):
            phones = [text]
        found["phones"].update(normalize_phone(p) for p in phones)
    return {k: sorted(v) for k, v in found.items()}


def normalize_phone(text):
    return "+" + re.sub(r"\D", "", text)


def merge_identifiers(*groups):
    merged = {"emails": set(), "phones": set(), "email_hashes": set(), "user_ids": set()}
    for group in groups:
        for k, values in group.items():
            merged[k].update(values)
    return {k: sorted(v) for k, v in merged.items()}


def flat_identifier_values(identifiers):
    return sorted(v for values in identifiers.values() for v in values)


def is_absent(fields):
    """A lookup result that proves the resource was not there."""
    status = str(fields.get("status_code") or fields.get("http_status") or "")
    return status == "404" or str(fields.get("rows")) == "0" or fields.get("result") == "NOT_FOUND"


def subject_values(content):
    """Identifiers held by explicit subject-association fields, at any nesting depth."""
    return flat_identifier_values(extract_identifiers(
        [text for key, text in string_values(content) if key in SUBJECT_FIELDS]))


def make_observation(file, resource_id, fields, source, *, system=None, collection=None,
                     data_store=None, record_type=None, table=None, copy=None,
                     observed_at=None, kind=None):
    """Build one observation. Identifiers are split into returned content, explicit subject
    references and lookup keys; fields["request"] (log request lines) is lookup-only."""
    content = {k: v for k, v in fields.items() if k not in LOOKUP_FIELDS and k != "request"}
    identifiers = extract_identifiers(content)
    subject_refs = subject_values(content)
    lookup = set(flat_identifier_values(extract_identifiers(
        [fields.get(k) for k in LOOKUP_FIELDS if isinstance(fields.get(k), str)] + [fields.get("request")])))
    if kind is None:
        kind = "absence" if is_absent(fields) else "record"
    if kind == "absence":
        # An absent record exposes nothing. An explicit subject reference still says whose record was
        # looked for; any other identifier on it was only the lookup key.
        lookup.update(set(flat_identifier_values(identifiers)) - set(subject_refs))
        identifiers = {k: [] for k in identifiers}
    raw_system = system
    system, alias_copy = SERVICE_ALIASES.get(system, (system, None))
    related = sorted(set(RESOURCE_ID_RE.findall(json.dumps(fields))) - {resource_id})
    return {
        "obs_id": None,  # numbered per file by load_file
        "file_id": file["file_id"],
        "file_type": file_type(file),
        "file_captured_at": file["captured_at"],
        "kind": kind,
        "system": system,
        "raw_system": raw_system,
        "collection": collection,
        "data_store": data_store,
        "record_type": record_type or fields.get("record_type"),
        "table": table,
        "copy": copy or alias_copy,
        "resource_id": resource_id,
        "fields": fields,
        "identifiers": identifiers,
        "subject_refs": subject_refs,
        "lookup_keys": sorted(lookup),
        "related_resource_ids": related,
        "observed_at": observed_at,
        "source": source,
    }


def make_scope(file, system, tables, observed_at, source, *, collection=None, data_store=None):
    """A snapshot listing: for each table/copy, the resource IDs of `system` it contains."""
    return {
        "obs_id": None,
        "file_id": file["file_id"],
        "file_type": file_type(file),
        "file_captured_at": file["captured_at"],
        "kind": "scope",
        "system": system,
        "collection": collection,
        "data_store": data_store,
        "tables": {name: sorted(ids) for name, ids in tables.items()},
        "observed_at": observed_at,
        "source": source,
    }


def file_type(file):
    return Path(file["file_path"]).suffix.lstrip(".").lower()


# ---------------------------------------------------------------- JSON

def parse_json(file, path):
    data = json.loads(path.read_text(encoding="utf-8"))
    observations, stats = [], Counter()

    def resolve_system(record, ctx):
        if isinstance(record.get("system"), str):
            return record["system"]
        if ctx.get("data_store"):
            return ctx["data_store"]
        route = ctx.get("path", ())
        if "service_exports" in route:
            return route[route.index("service_exports") + 1]
        return file["origin"]  # e.g. evidence_56 / evidence_59: the exporting service itself

    def resolve_collection(ctx):
        route = ctx.get("path", ())
        if ctx.get("collection"):
            return ctx["collection"]
        return route[-1] if route and route[-1] != "records" else None

    def record_time(record, ctx):
        for key in TIME_KEYS:
            if isinstance(record.get(key), str):
                return record[key]
        return ctx.get("captured_at")

    def emit(record, ctx):
        nested = list(nested_records(record))
        same = [n for n in nested if n["resource_id"] == record["resource_id"]]
        stats["merged_nested"] += len(same)
        observations.append(make_observation(
            file, record["resource_id"], record, json.dumps(record, ensure_ascii=False),
            system=resolve_system(record, ctx), collection=resolve_collection(ctx),
            data_store=ctx.get("data_store"), observed_at=record_time(record, ctx)))
        for child in nested:
            if child["resource_id"] != record["resource_id"]:
                emit(child, ctx)

    def walk(node, ctx):
        if isinstance(node, list):
            for item in node:
                walk(item, ctx)
            return
        if not isinstance(node, dict):
            return
        if "resource_id" in node:
            emit(node, ctx)
            return
        ctx = dict(ctx)
        for key in ("data_store", "collection", "captured_at"):
            if isinstance(node.get(key), str):
                ctx[key] = node[key]
        if isinstance(node.get("records"), list) and node.get("collection"):
            ids = [r["resource_id"] for r in node["records"] if isinstance(r, dict) and "resource_id" in r]
            summary = {k: v for k, v in node.items() if k != "records"}
            summary["record_count"] = len(node["records"])
            observations.append(make_scope(
                file, resolve_system({}, ctx), {node["collection"]: ids}, ctx.get("captured_at"),
                json.dumps(summary, ensure_ascii=False), collection=node["collection"],
                data_store=ctx.get("data_store")))
        for key, value in node.items():
            if isinstance(value, (dict, list)):
                walk(value, {**ctx, "path": ctx.get("path", ()) + (key,)})

    walk(data, {})
    return observations, stats


def nested_records(value):
    """Yield dicts with a resource_id nested inside `value` (not descending into them)."""
    children = value.values() if isinstance(value, dict) else value
    for child in children:
        if isinstance(child, dict) and "resource_id" in child:
            yield child
        elif isinstance(child, (dict, list)):
            yield from nested_records(child)


# ---------------------------------------------------------------- logs

KV_RE = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|\S+)')
LOG_FORMATS = [
    # 2026-07-28T05:51:18Z [DEBUG] [account_service] [tr-6074163] profile_lookup_started: k="v" ...
    ("bracket", re.compile(r"^(?P<ts>\S+) \[(?P<level>\w+)\] \[(?P<service>[\w-]+)\] \[(?P<trace>[\w-]+)\] (?P<event>\w+): ?(?P<kv>.*)$")),
    # 2026-07-03T07:51:30Z | DEBUG | security_service | tr-5156005 | event | k="v" ...
    ("pipe", re.compile(r"^(?P<ts>\S+) \| (?P<level>\w+) \| (?P<service>[\w-]+) \| (?P<trace>[\w-]+) \| (?P<event>\w+) \| ?(?P<kv>.*)$")),
    # 2026-03-25T08:51:15Z <15>1 node-1 support_platform tr-7458912 - - level=DEBUG k="v" ...
    ("syslog", re.compile(r"^(?P<ts>\S+) <\d+>1 \S+ (?P<service>[\w-]+) (?P<trace>[\w-]+) - - (?P<kv>.*)$")),
    # 2026-04-19T06:51:13Z {"severity": ..., "trace_id": ...}
    ("json", re.compile(r"^(?P<ts>\S+) (?P<json>\{.*\})$")),
    # 2026-09-30T11:51:03Z requestId=tr-1 level=DEBUG function=svc message="k=\"v\" ..."
    ("cloud", re.compile(r'^(?P<ts>\S+) requestId=(?P<trace>\S+) level=(?P<level>\w+) function=(?P<service>\w+) message="(?P<kv>.*)"$')),
    # 2026-03-15T07:00:00Z INFO service=x event=record_lookup seq=1 system="..." ...
    ("level_kv", re.compile(r"^(?P<ts>\S+) (?P<level>[A-Z]+) (?P<kv>\w+=.*)$")),
    # 2026-02-18T05:47:12Z level=INFO service=x trace_id=rb-1 event=y k=v ...
    ("kv", re.compile(r"^(?P<ts>\S+) (?P<kv>\w+=.*)$")),
]


def parse_kv(text):
    values = {}
    for key, raw in KV_RE.findall(text):
        if raw.startswith('"'):
            raw = raw[1:-1].replace('\\"', '"')
        values[key] = None if raw == "null" else raw
    return values


def parse_log_line(line):
    """Return (timestamp, service, trace, values) for a log line, or None if no format matches."""
    for name, pattern in LOG_FORMATS:
        m = pattern.match(line)
        if not m:
            continue
        parts = m.groupdict()
        if name == "json":
            values = {k: (None if v == "null" else v) for k, v in json.loads(parts["json"]).items()}
        else:
            values = parse_kv(parts["kv"].replace('\\"', '"') if name == "cloud" else parts["kv"])
        if parts.get("event"):
            values["event"] = parts["event"]
        service = parts.get("service") or values.get("service")
        trace = parts.get("trace") or values.pop("trace_id", None)  # parse_log stores it once per trace
        return parts["ts"], service, trace, values
    return None


def is_meaningful(values):
    return bool(values.get("resource_id")) or any(values.get(k) for k in LOOKUP_FIELDS | {"subject_ref"})


def parse_log(file, path):
    """Group lines by trace; each resource touched by a trace becomes one observation."""
    groups, order, stats = {}, [], Counter()
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        parsed = parse_log_line(line)
        if parsed is None:
            stats["unparsed_lines"] += 1
            continue
        ts, service, trace, values = parsed
        key = trace or f"line-{n}"  # lines without a trace stand alone; this key is never stored
        if key not in groups:
            groups[key] = (trace, [])
            order.append(key)
        groups[key][1].append({"ts": ts, "service": service, "values": values, "text": line})

    observations = []
    for key in order:
        trace, lines = groups[key]
        useful = [ln for ln in lines if is_meaningful(ln["values"])]
        if not useful:
            stats["noise_lines"] += len(lines)  # e.g. collector, dns, webhook lines; a cache miss is not absence
            continue
        resource_ids = sorted({ln["values"]["resource_id"] for ln in useful if ln["values"].get("resource_id")})
        if len(resource_ids) > 1:
            stats["multi_resource_traces"] += 1
        for resource_id in resource_ids or [None]:
            mine = [ln for ln in useful if ln["values"].get("resource_id") in (resource_id, None)]
            fields, request, events = {}, {}, []
            for ln in mine:
                values = dict(ln["values"])
                events.append(values.pop("event", None))
                # A line without resource_id is a request line: its identifiers are only lookup keys.
                target = fields if values.get("resource_id") else request
                target.update({k: v for k, v in values.items() if v is not None or k not in target})
            if request:
                fields["request"] = request
            if trace:
                fields["trace_id"] = trace
            fields["events"] = events
            last = mine[-1]
            observations.append(make_observation(
                file, resource_id, fields, "\n".join(ln["text"] for ln in lines),
                system=fields.get("system") or request.get("system") or last["service"],
                observed_at=last["ts"]))
    return observations, stats


# ---------------------------------------------------------------- SQL

INSERT_RE = re.compile(r"""^INSERT INTO "?(\w+)"? VALUES ?\('([^']*)'""")


def parse_sql(file, path):
    text = path.read_text(encoding="utf-8")
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(text)
    insert_lines = {}
    for line in text.splitlines():
        m = INSERT_RE.match(line)
        if m:
            insert_lines[(m.group(1), m.group(2))] = line
    header_time = re.search(r"Captured at: (\S+)", text)
    header_time = header_time.group(1) if header_time else None

    tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    scope_systems = {}
    if "snapshot_scope" in tables:
        scope_systems = {r["origin_system"]: r["captured_at"] for r in db.execute("SELECT * FROM snapshot_scope")}

    observations, stats = [], Counter()
    listed = {}  # (system, base) -> {table: [resource ids]}
    for table in tables:
        if table == "snapshot_scope":
            continue
        m = COPY_SUFFIX_RE.match(table)
        base, copy = (m.group("base"), m.group("copy")) if m else (table, None)
        for system in [TABLE_SYSTEMS.get(base)] if base in TABLE_SYSTEMS else []:
            listed.setdefault((system, base), {})[table] = []
        for row in db.execute(f'SELECT * FROM "{table}"'):
            row = dict(row)
            if "record_json" in row:
                # Generic replica container: the row says which system and collection it copies.
                fields = json.loads(row["record_json"])
                fields["updated_at"] = row["updated_at"]
                system, collection = row["origin_system"], row["collection_name"]
            else:
                fields, system, collection = row, TABLE_SYSTEMS.get(base), base
            listed.setdefault((system, base), {}).setdefault(table, []).append(row["resource_id"])
            source = insert_lines.get((table, row["resource_id"]), json.dumps(row, ensure_ascii=False))
            observations.append(make_observation(
                file, row["resource_id"], fields, f"[{table}] {source}", system=system,
                collection=collection, table=table, copy=copy,
                observed_at=fields.get("updated_at") or fields.get("indexed_at") or header_time))

    # Scope observations: every copy table of a system, including empty ones.
    for (system, base), per_table in sorted(listed.items(), key=lambda kv: (str(kv[0][0]), kv[0][1])):
        all_tables = {t: [] for t in tables if t == base or t.startswith(base + "_")}
        all_tables.update(per_table)
        if scope_systems:
            if system not in scope_systems:
                continue  # this inventory capture did not cover that system
            observed_at = scope_systems[system]
            source = f"snapshot_scope: {system} captured {observed_at}; tables {sorted(all_tables)}"
        else:
            observed_at = header_time
            source = f"database snapshot captured {header_time}; tables {sorted(all_tables)}"
        observations.append(make_scope(file, system, all_tables, observed_at, source, collection=base))
        if system is None:
            stats["scope_without_system"] += 1
    return observations, stats


# ---------------------------------------------------------------- TXT and PDF

TXT_ROW_RE = re.compile(r"^- (.+?) \| (\w+) \| (\w+) \| (\w+) \| (\w+) \| (.+)$")


def parse_txt(file, path):
    """Shift handover: '- time | system | resource_id | record_type | result | subject' rows."""
    observations, stats = [], Counter()
    for line in path.read_text(encoding="utf-8").splitlines():
        m = TXT_ROW_RE.match(line.strip())
        if not m:
            if line.strip():
                stats["unparsed_lines"] += 1  # title and header lines
            continue
        recorded_at, system, resource_id, record_type, result, subject = m.groups()
        fields = {"recorded_at": recorded_at, "system": system, "resource_id": resource_id,
                  "record_type": record_type, "result": result, "subject_reference": subject}
        observations.append(make_observation(file, resource_id, fields, line.strip(), system=system,
                                             record_type=record_type, observed_at=recorded_at))
    return observations, stats


def pdf_text(path):
    return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)


MONTHS = {m: n for n, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}
PDF_SPLIT_TIME_RE = re.compile(r"(\d{2})\n(\d{2}) UTC")  # pypdf breaks "06:30 UTC" as "06\n30 UTC"
# "issued 05 Sep 2026 06:30 UTC", "received 18 Jun 2026", "Retain until 04 May 2033"
PDF_TIME_RE = re.compile(r"^(?P<label>[A-Za-z][A-Za-z ]*?) (?P<day>\d{1,2}) (?P<month>[A-Z][a-z]{2}) "
                         r"(?P<year>\d{4})(?: (?P<hm>\d{2}:\d{2}) UTC)?$", re.M)
PDF_SUBJECT_RE = re.compile(r"^subject reference (\S+)$", re.M)
# Business references (statement, order, ticket, journal numbers) and file names, kept for linking
# (data_catalog activity rows name aliases such as NST-2026-0718-1842).
PDF_ALIAS_RE = re.compile(r"(?<![\w-])[A-Z]{2,4}-\d[\d-]*\d(?![\w-])|\b[\w.-]+\.pdf\b")
# Labels giving the time a document shows its content as of, in order of preference. Other labels
# (recorded, submitted, retain_until, ...) describe the business event and keep their own meaning.
PDF_AS_OF_LABELS = ("captured", "issued", "generated")


def pdf_times(text):
    """Labeled dates and times as {label: ISO date or datetime}, first occurrence per label."""
    times = {}
    for m in PDF_TIME_RE.finditer(text):
        month = MONTHS.get(m["month"])
        if month is None:
            continue
        date = f"{m['year']}-{month:02d}-{int(m['day']):02d}"
        times.setdefault(m["label"].lower().replace(" ", "_"), f"{date}T{m['hm']}:00Z" if m["hm"] else date)
    return times


def parse_pdf(file, path):
    """A register ('a | b | c' rows) gives one observation per row, each carrying the document's
    title, labeled times and context lines. Any other document is one observation."""
    text = PDF_SPLIT_TIME_RE.sub(r"\1:\2 UTC", pdf_text(path))
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    rows = [ln for ln in lines if ln.count(" | ") >= 2]
    title = lines[0] if lines else None
    times = pdf_times(text)
    as_of = next((times[label] for label in PDF_AS_OF_LABELS if label in times), None)
    chunks = []
    if rows:
        # Row-number lines ("1", "2", ...) are layout, not context.
        document = {"title": title, "times": times,
                    "context": [ln for ln in lines if ln not in rows and not ln.isdigit()]}
        for row in rows:
            columns = row.split(" | ")
            chunks.append((row, f"{title}\n{row}", {
                "columns": columns, "times": pdf_times("\n".join(columns)),
                "aliases": sorted(set(PDF_ALIAS_RE.findall(row))), "document": document}))
    else:
        fields = {"title": title, "text": text, "times": times,
                  "aliases": sorted(set(PDF_ALIAS_RE.findall(text)))}
        subject = PDF_SUBJECT_RE.search(text)
        if subject:
            fields["subject_reference"] = subject.group(1)
        chunks.append((text, text, fields))

    observations = []
    for chunk, source, fields in chunks:
        resource_ids = sorted(set(RESOURCE_ID_RE.findall(chunk)))
        resource_id = resource_ids[0] if len(resource_ids) == 1 else None
        observations.append(make_observation(file, resource_id, fields, source, system=file["origin"],
                                             observed_at=as_of))
    return observations, Counter()


# ---------------------------------------------------------------- loading

PARSERS = {"json": parse_json, "log": parse_log, "sql": parse_sql, "txt": parse_txt, "pdf": parse_pdf}


def load_file(file):
    """Return (observations, stats) for one manifest row. PNG files are not parsed yet."""
    kind = file_type(file)
    if kind not in PARSERS:
        return [], Counter({"pending": 1})
    observations, stats = PARSERS[kind](file, DATASET / file["file_path"])
    for n, obs in enumerate(observations, start=1):
        obs["obs_id"] = f"{file['file_id']}#{n}"
    return observations, stats


def load_observations():
    observations = []
    for file in load_manifest():
        observations += load_file(file)[0]
    return observations


# ---------------------------------------------------------------- coverage report

def coverage_report():
    rows, totals, systems = [], {}, Counter()
    for file in load_manifest():
        observations, stats = load_file(file)
        systems.update((o["system"], o.get("raw_system", o["system"])) for o in observations)
        kind = file_type(file)
        records = [o for o in observations if o["kind"] != "scope"]
        scopes = [o for o in observations if o["kind"] == "scope"]
        row = {
            "file_id": file["file_id"], "type": kind,
            "obs": len(observations),
            "absent": sum(o["kind"] == "absence" for o in observations),
            "scopes": len(scopes),
            "empty_scope_tables": sum(1 for s in scopes for ids in s["tables"].values() if not ids),
            "no_resource_id": sum(o["resource_id"] is None for o in records),
            "no_identifiers": sum(not flat_identifier_values(o["identifiers"]) and not o["subject_refs"]
                                  and not o["lookup_keys"] for o in records),
            "subject_refs": sum(bool(o["subject_refs"]) for o in records),
            "unparsed": stats["unparsed_lines"],
            "noise": stats["noise_lines"],
            "merged": stats["merged_nested"],
            "multi_res": stats["multi_resource_traces"],
            "pending": stats["pending"],
        }
        rows.append(row)
        total = totals.setdefault(kind, Counter())
        total["files"] += 1
        total.update({k: v for k, v in row.items() if isinstance(v, int)})

    columns = ["obs", "absent", "scopes", "empty_scope_tables", "no_resource_id", "no_identifiers", "subject_refs",
               "unparsed", "noise", "merged", "multi_res", "pending"]
    def line(first, second, values):
        return f"{first:<12}{second:<6}" + "".join(f"{v:>{len(c) + 2}}" for c, v in zip(columns, values))

    print("Per file (absent = 404/rows=0/NOT_FOUND observations; empty_scope_tables = snapshot "
          "tables/collections listing nothing for their system)")
    print(line("file_id", "type", columns))
    for row in rows:
        print(line(row["file_id"], row["type"], [row[c] for c in columns]))
    print("\nPer file type")
    print(line("type", "files", columns))
    for kind in sorted(totals):
        print(line(kind, totals[kind]["files"], [totals[kind][c] for c in columns]))
    pending = [r["file_id"] for r in rows if r["pending"]]
    print(f"\nPNG requires milestone 3 hash-bound reviewed facts: {len(pending)} files: {', '.join(pending)}")

    # Systems an output step can name: schema tables plus systems that activity rows target.
    with open(DATASET / "system_activity.csv", encoding="utf-8", newline="") as f:
        known = {row["target_system"] for row in csv.DictReader(f)} | set(TABLE_SYSTEMS.values())
    print("\nService aliases normalized (raw -> system: observations)")
    for (system, raw), n in sorted(systems.items(), key=str):
        if raw != system:
            print(f"  {raw} -> {system}: {n}")
    print("Systems left raw (not a schema system or activity target_system; not grounded as an alias):")
    for (system, raw), n in sorted(systems.items(), key=str):
        if raw == system and system not in known:
            print(f"  {system}: {n}")


if __name__ == "__main__":
    if "--report" in sys.argv:
        coverage_report()
    else:
        print(f"{len(load_observations())} observations. Use --report for per-file coverage.")
