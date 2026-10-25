"""Build the local, standalone diagnostic review: python code/review.py.

Reads the saved sample run, never runs predictions. Sample answers enter only this
review/evaluation layer. The generated page contains participant data; do not package it.
"""

import base64
import csv
import json
from pathlib import Path

import evidence
from evaluation.main import load, score

ROOT = Path(__file__).resolve().parent.parent


def rows(path):
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def script_json(value):
    """JSON in an HTML raw-text element must never contain a literal closing tag."""
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            .replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def build_data(diagnostic_trace=None):
    dataset, directory = ROOT / "dataset", ROOT / "output"
    trace = diagnostic_trace if diagnostic_trace is not None else json.loads((directory / "trace.json").read_text(encoding="utf-8"))
    if diagnostic_trace is None:
        scores = json.loads((directory / "sample_scores.json").read_text(encoding="utf-8"))
        predictions = load(directory / "sample_predictions.csv")
        expected = load(dataset / "sample_output.csv")
    else:
        scores = {"confusion": {}, "field_accuracy": {}, "step_multiset": {}}
        predictions, expected = {}, {}
    requests = {r["case_id"]: r for name in ("sample_deletion_requests.csv", "deletion_requests.csv")
                for r in rows(dataset / name)}
    snapshots = {r["user_id"]: r for r in rows(dataset / "user_identities.csv")}
    audits = rows(dataset / "user_audit_log.csv")
    activities = {r["activity_id"]: r for r in rows(dataset / "system_activity.csv")}
    observations = {o["obs_id"]: o for o in evidence.load_observations()}
    if "llm" in trace:
        from llm_tasks import fact_observations
        manifest = {f["file_id"]: f for f in evidence.load_manifest()}
        for fid, image in trace["llm"]["images"].items():
            if image["decision_used"] is not None:
                observations.update({o["obs_id"]: o for o in fact_observations(manifest[fid], image["decision_used"])})
    sample_ids = {r["case_id"] for r in rows(dataset / "sample_deletion_requests.csv")}
    warnings = []
    # Saved analytics are displayed verbatim; detect stale files instead of silently mixing runs.
    if diagnostic_trace is None and any(scores.get(k) != v for k, v in score(expected, predictions).items()):
        warnings.append("Saved sample scores disagree with the current sample CSV files. Rerun --samples.")
    if set(trace["cases"]) != set(requests):
        warnings.append("Trace case coverage differs from the participant request files.")
    sources, cases = {}, {}

    def add_source(key, ref=None):
        if key not in sources:
            if key in observations:
                sources[key] = dict(observations[key])
            elif key in activities:
                row = activities[key]
                sources[key] = {"row": row, "observed_at": row["event_time"],
                                "origin": "system_activity.csv", "source": row["details"]}
            else:
                sources[key] = {}
        if ref is not None:
            previous = sources[key].get("source")
            if previous is not None and previous != ref["source"]:
                warnings.append(f"Source representation differs between parser and trace: {key}.")
            sources[key]["source"] = ref["source"]
            sources[key]["trace_id"] = ref["id"]
        return key

    for cid, events in trace["cases"].items():
        stages = {e["stage"]: e["outputs"] for e in events}
        result = stages.get("steps", {})
        actual = predictions.get(cid)
        case_warnings = []
        if cid in expected and actual != (result.get("status"), result.get("steps")):
            case_warnings.append("Sample CSV and final trace output differ (or the export is missing).")
        user_id = stages.get("identity", {}).get("user_id")
        history = [a for a in audits if a["user_id"] == user_id]
        for row in history:
            sources[row["audit_id"]] = {"row": row, "origin": "user_audit_log.csv",
                                         "observed_at": row["event_time"]}
        if user_id:
            sources[user_id] = {"row": snapshots[user_id], "origin": "user_identities.csv",
                                "observed_at": snapshots[user_id]["updated_at"]}
        recorded = []
        case_sources = []
        for event in events:
            refs = [add_source(ref.get("obs_id", ref["id"]), ref) for ref in event["evidence"]]
            recorded.append({**event, "evidence": refs})
            case_sources.extend(refs)
        cutoff = stages.get("cutoff", {})
        excluded = sorted(set(cutoff.get("excluded_link_sources", []) +
                              cutoff.get("excluded_resource_observations", [])))
        moved = stages.get("linking", {}).get("moved_to_other_requests", [])
        for key in excluded + [link["source"] for link in moved]:
            add_source(key)
            case_sources.append(key)
        errors = [e for e in scores.get("validation_errors", []) if e.startswith(cid + ":")]
        cases[cid] = {
            "request": requests[cid], "kind": "sample" if cid in sample_ids else "evaluation",
            "events": recorded, "produced": actual if cid in expected else
            (result.get("status"), result.get("steps", [])),
            "expected": expected.get(cid), "identity_source": user_id,
            "history": [a["audit_id"] for a in history],
            "sources": list(dict.fromkeys(case_sources)), "excluded": excluded,
            "moved": moved, "warnings": case_warnings, "validation_errors": errors,
        }

    # One copy of each original artifact, independent of case membership. A manifest
    # entry alone never establishes relevance to a case. Restrict paths to participant evidence.
    files = {}
    evidence_root = (dataset / "evidence").resolve()
    for row in rows(dataset / "evidence_files.csv"):
        path = (dataset / row["file_path"]).resolve()
        if not path.is_relative_to(evidence_root):
            raise ValueError(f"Evidence path outside dataset/evidence: {row['file_id']}")
        item = dict(row)
        if path.suffix in (".png", ".pdf"):
            mime = "image/png" if path.suffix == ".png" else "application/pdf"
            item["data_url"] = f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")
        else:
            item["text"] = path.read_text(encoding="utf-8")
        files[row["file_id"]] = item
    return {"meta": {k: v for k, v in trace.items() if k != "cases"}, "scores": scores,
            "cases": cases, "sources": sources, "files": files,
            "rules": rows(dataset / "deletion_rules.csv"), "warnings": list(dict.fromkeys(warnings))}


def render(data):
    return PAGE.replace("__REVIEW_DATA__", script_json(data))


def write_llm_diagnostic(trace, path):
    path.write_text(render(build_data(trace)), encoding="utf-8")
    return path


PAGE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ghost Data review</title>
<style>
:root{
 color-scheme:light dark;
 --bg:#f3f5f8;--surface:#fff;--sunken:#eef1f5;--ink:#18222d;--muted:#566474;--line:#dce2e9;--accent:#2b5f94;
 --rules:#2f6b66;--llm:#6a4a9e;--inferred:#a85f00;
 --complete:#1d7547;--incomplete:#b0481a;--na:#5f6b79;--bad:#b8322a;--good:#1d7547;--warn:#8a5200;
 --font:system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;--mono:ui-monospace,"Cascadia Mono",Consolas,monospace;
}
@media(prefers-color-scheme:dark){:root{
 --bg:#11161c;--surface:#192029;--sunken:#212a35;--ink:#e5eaf0;--muted:#9ba8b6;--line:#2c3641;--accent:#85b4e4;
 --rules:#6cc2ba;--llm:#b99be8;--inferred:#e5a955;
 --complete:#5fcf93;--incomplete:#f0905f;--na:#a0abb8;--bad:#f27d73;--good:#5fcf93;--warn:#e5a955;
}}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;display:flex;flex-direction:column;background:var(--bg);color:var(--ink);font:14px/1.45 var(--font)}
button,input{font:inherit;color:inherit}
button{cursor:pointer}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
h1,h2,h3,p{margin:0}
.muted{color:var(--muted)}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.mono{font-family:var(--mono);font-size:12px}
.skip{position:absolute;left:8px;top:-48px;background:var(--surface);padding:8px 12px;border-radius:6px;z-index:5}
.skip:focus{top:8px}

/* top bar */
.top{display:flex;align-items:center;gap:20px;flex-wrap:wrap;padding:10px 20px;background:var(--surface);border-bottom:1px solid var(--line)}
.brand h1{font-size:16px;font-weight:650}
.brand p{font-size:12px;color:var(--muted)}
.tiles{display:flex;gap:8px;flex-wrap:wrap;flex:1}
.tile{padding:4px 14px;border-left:3px solid var(--line)}
.tile b{display:block;font-size:19px;font-weight:650;line-height:1.2;font-variant-numeric:tabular-nums}
.tile span{font-size:12px;color:var(--muted)}
.tile.alert{border-color:var(--bad)}.tile.alert b{color:var(--bad)}
.btn{border:1px solid var(--line);background:var(--surface);border-radius:6px;padding:5px 12px}
.btn:hover{background:var(--sunken)}
.banner{margin:0;padding:6px 20px;font-size:13px;background:color-mix(in srgb,var(--warn) 12%,var(--surface));border-bottom:1px solid var(--line);color:var(--ink)}
.banner.err{background:color-mix(in srgb,var(--bad) 12%,var(--surface))}
.banner summary{cursor:pointer}
.banner ul{margin:6px 0 2px;padding-left:20px}

/* layout */
.app{flex:1;min-height:0;display:grid;grid-template-columns:300px minmax(0,1fr)}
.side{display:flex;flex-direction:column;min-height:0;background:var(--surface);border-right:1px solid var(--line)}
.side-head{padding:12px;border-bottom:1px solid var(--line)}
.side-head input{width:100%;padding:7px 10px;border:1px solid var(--line);border-radius:6px;background:var(--bg)}
.chips{display:flex;flex-wrap:wrap;gap:5px;margin-top:8px}
.chip{border:1px solid var(--line);background:transparent;border-radius:99px;padding:2px 10px;font-size:12px;color:var(--muted)}
.chip[aria-pressed=true]{background:var(--ink);border-color:var(--ink);color:var(--surface)}
.count{margin-top:8px;font-size:12px;color:var(--muted)}
#case-list{list-style:none;margin:0;padding:0;overflow:auto;flex:1}
#case-list button{display:grid;grid-template-columns:10px 1fr auto;gap:2px 10px;align-items:center;width:100%;text-align:left;border:0;border-bottom:1px solid var(--line);background:transparent;padding:8px 12px;border-left:3px solid transparent}
#case-list button:hover{background:var(--sunken)}
#case-list button[aria-current=true]{background:var(--sunken);border-left-color:var(--accent)}
#case-list .id{font-weight:600}
#case-list .email{grid-column:2/4;font-size:12px;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.dot{width:9px;height:9px;border-radius:50%;border:2px solid var(--na)}
.dot.ok{background:var(--good);border-color:var(--good)}.dot.no{background:var(--bad);border-color:var(--bad)}
.main{overflow:auto;padding:20px 24px 48px;min-width:0;display:flex;flex-direction:column;gap:16px}
.main>*{width:100%;max-width:1000px;margin:0 auto;flex:none}

/* pills */
.pill{display:inline-block;border-radius:99px;padding:1px 9px;font-size:12px;font-weight:600;white-space:nowrap;
 color:var(--c);background:color-mix(in srgb,var(--c) 13%,var(--surface))}
.s-COMPLETE{--c:var(--complete)}.s-INCOMPLETE{--c:var(--incomplete)}.s-NOT_APPLICABLE,.s-MISSING{--c:var(--na)}
.tag{--c:var(--muted)}.tag.red{--c:var(--bad)}.tag.amber{--c:var(--warn)}

/* case header */
.case-head h2{font-size:22px;font-weight:650;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.facts{display:flex;flex-wrap:wrap;gap:4px 24px;margin-top:8px;font-size:13px;color:var(--muted)}
.facts b{color:var(--ink);font-weight:500}
.quote{margin-top:10px;padding:2px 0 2px 14px;border-left:3px solid var(--line);max-width:75ch}

/* flow */
.flow{list-style:none;margin:0;padding:0;display:flex;gap:6px;overflow-x:auto;padding-bottom:4px}
.flow li{flex:1 1 118px;max-width:170px;position:relative}
.flow li+li::before{content:"";position:absolute;left:-6px;top:50%;width:6px;border-top:1px solid var(--line)}
.stage{width:100%;height:100%;text-align:left;border:1px solid var(--line);border-top:3px solid var(--c);background:var(--surface);border-radius:6px;padding:7px 9px;display:block}
.stage:hover{background:var(--sunken)}
.stage[aria-pressed=true]{outline:2px solid var(--c);outline-offset:-1px}
.stage b{display:block;font-size:13px}
.stage span{display:block;font-size:12px;color:var(--muted);margin-top:2px}
.m-rules{--c:var(--rules)}.m-llm{--c:var(--llm)}.m-inferred{--c:var(--inferred)}
.legend{display:flex;gap:14px;font-size:12px;color:var(--muted);margin-top:6px}
.legend i{display:inline-block;width:10px;height:3px;background:var(--c);margin-right:5px;vertical-align:middle}
.stage-detail{margin-top:10px;background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:12px 14px}
.stage-detail h3{font-size:14px}
.stage-detail .row{display:flex;gap:6px;flex-wrap:wrap;margin:8px 0}

/* cards, folds */
.card{background:var(--surface);border:1px solid var(--line);border-radius:8px}
.card-head{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px 14px}
.card-head h3{font-size:15px}
.card-body{padding:0 14px 12px}
.seg{display:flex;border:1px solid var(--line);border-radius:6px;overflow:hidden}
.seg button{border:0;background:transparent;padding:3px 12px;font-size:12px;color:var(--muted)}
.seg button[aria-pressed=true]{background:var(--ink);color:var(--surface)}
details.fold{background:var(--surface);border:1px solid var(--line);border-radius:8px}
details.fold>summary{cursor:pointer;padding:9px 14px;font-weight:600;list-style:none;display:flex}
details.fold>summary::-webkit-details-marker{display:none}
details.fold>summary::after{content:"+";color:var(--muted);margin-left:auto}
details.fold[open]>summary::after{content:"\2212"}
details.fold>summary span{font-weight:400;color:var(--muted);margin-left:8px}
details.fold>.body{padding:0 14px 12px}
details.sub>summary{cursor:pointer;color:var(--muted);font-size:13px;padding:4px 0}
.body h4{font-size:12px;color:var(--muted);font-weight:600;margin:12px 0 4px}
.body ul.plain{margin:4px 0;padding-left:18px}

/* tables */
.tw{overflow-x:auto}
.tw+p{margin-top:8px}
table{border-collapse:collapse;width:100%;font-size:13px}
th{text-align:left;font-weight:600;font-size:12px;color:var(--muted);padding:5px 8px;border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:5px 8px;border-bottom:1px solid var(--line);vertical-align:top;overflow-wrap:break-word}
td.mono{font-size:12px}
tr.differs td:first-child,tr.extra td:first-child,tr.missing td:first-child{box-shadow:inset 3px 0 var(--warn)}
td.diff{background:color-mix(in srgb,var(--warn) 12%,var(--surface))}
del{display:block;color:var(--bad);text-decoration:line-through}
ins{display:block;color:var(--good);text-decoration:none}
ins::before{content:"expected ";color:var(--muted);font-size:11px}
.link{border:0;background:none;padding:0;color:var(--accent);text-decoration:underline;font:inherit;font-family:var(--mono);font-size:12px}
.srclist{list-style:none;margin:0;padding:0}
.srclist li{display:flex;gap:10px;align-items:baseline;padding:4px 0;border-bottom:1px solid var(--line);flex-wrap:wrap}
.srclist li:last-child{border:0}
pre{margin:6px 0 0;padding:10px;background:var(--sunken);border-radius:6px;font:12px/1.5 var(--mono);white-space:pre-wrap;overflow-wrap:anywhere;max-height:300px;overflow:auto}

/* timeline */
.timeline{max-height:200px;overflow:auto;background:var(--sunken);border-radius:6px;padding:6px}
.lane{position:relative;height:6px;margin-bottom:2px}
.lane i{position:absolute;top:0;bottom:0;border-radius:2px;background:var(--llm);min-width:2px}
.lane i.fail{background:var(--bad)}

/* dialogs */
dialog{border:1px solid var(--line);border-radius:10px;background:var(--surface);color:var(--ink);padding:0;max-height:90vh}
dialog::backdrop{background:rgba(10,16,24,.45)}
dialog.drawer{margin:0 0 0 auto;height:100vh;max-height:100vh;width:min(640px,100vw);border-radius:0;border-width:0 0 0 1px}
dialog.modal{width:min(820px,94vw)}
.dlg-head{position:sticky;top:0;z-index:1;display:flex;justify-content:space-between;align-items:center;gap:12px;padding:12px 18px;background:var(--surface);border-bottom:1px solid var(--line)}
.dlg-head h2{font-size:16px;overflow-wrap:anywhere}
.dlg-body{padding:14px 18px 22px}
.dlg-body>*+*{margin-top:12px}
.dlg-body img{max-width:100%;height:auto;border:1px solid var(--line);border-radius:6px}
.lines{margin:6px 0 0;padding:8px 8px 8px 44px;max-height:320px;overflow:auto;background:var(--sunken);border-radius:6px;font:12px/1.5 var(--mono)}
.lines li{white-space:pre-wrap;overflow-wrap:anywhere}
.note-ok{color:var(--good)}.note-bad{color:var(--bad)}.note-warn{color:var(--warn)}
.empty{color:var(--muted)}
@media(max-width:860px){
 body{height:auto}.app{display:block}.side{border-right:0;border-bottom:1px solid var(--line)}
 #case-list{max-height:260px}.main{padding:16px}
}
@media(prefers-reduced-motion:no-preference){.stage,.chip,.btn{transition:background .12s}}
</style></head><body>
<a class="skip" href="#case-detail">Skip to selected case</a>
<header class="top">
 <div class="brand"><h1>Ghost Data review</h1><p id="run-sub"></p></div>
 <div class="tiles" id="tiles"></div>
 <button class="btn" id="open-run" type="button">Run details</button>
</header>
<div id="banner"></div>
<div class="app">
 <nav class="side" aria-label="Cases">
  <div class="side-head">
   <input id="search" type="search" placeholder="Search case, email, resource" aria-label="Search case, email or resource" autocomplete="off">
   <div class="chips" id="chips"></div>
   <p class="count" id="case-count" role="status" aria-live="polite"></p>
  </div>
  <ul id="case-list"></ul>
 </nav>
 <main class="main" id="case-detail" tabindex="-1"></main>
</div>
<dialog class="drawer" id="run-dialog" aria-labelledby="run-title"><div class="dlg-head"><h2 id="run-title">Run details</h2><button class="btn" type="button" data-close>Close</button></div><div class="dlg-body" id="run-body"></div></dialog>
<dialog class="modal" id="src-dialog" aria-labelledby="src-title"><div class="dlg-head"><h2 id="src-title"></h2><button class="btn" type="button" data-close>Close</button></div><div class="dlg-body" id="src-body"></div></dialog>
<noscript><p class="banner err">Enable JavaScript to inspect the embedded review data. No network access is needed.</p></noscript>
<script id="review-data" type="application/json">__REVIEW_DATA__</script>
<script>
'use strict';
const D=JSON.parse(document.getElementById('review-data').textContent);
const $=id=>document.getElementById(id);
function el(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
function pre(value){return el('pre',typeof value==='string'?value:JSON.stringify(value,null,2));}
function btn(text,cls,onclick){const b=el('button',text,cls);b.type='button';b.addEventListener('click',onclick);return b;}
function fold(parent,title,count,open){const d=el('details',undefined,'fold'),s=el('summary',title),body=el('div',undefined,'body');if(count!==undefined)s.append(el('span',String(count)));d.append(s,body);d.open=!!open;parent.append(d);return body;}
function sub(parent,title,value){const d=el('details',undefined,'sub');d.append(el('summary',title),value instanceof Node?value:pre(value));parent.append(d);return d;}
function table(parent,headers,rows,mono){const w=el('div',undefined,'tw'),t=el('table'),tr=el('tr');for(const h of headers){const th=el('th',h);th.scope='col';tr.append(th);}const hd=el('thead');hd.append(tr);t.append(hd);const body=el('tbody');
 for(const row of rows){const r=el('tr');for(const v of row){const td=el('td',undefined,mono?'mono':'');td.append(v instanceof Node?v:document.createTextNode(String(v??'')));r.append(td);}body.append(r);}
 t.append(body);w.append(t);parent.append(w);return body;}
function card(parent,title){const c=el('section',undefined,'card'),h=el('div',undefined,'card-head'),body=el('div',undefined,'card-body');h.append(el('h3',title));c.append(h,body);parent.append(c);return {body,head:h};}
function list(parent,items,cls){const ul=el('ul',undefined,cls||'plain');for(const i of items)ul.append(el('li',i));parent.append(ul);}
const pct=n=>typeof n==='number'?(100*n).toFixed(1)+'%':'n/a';
const when=t=>t?String(t).slice(0,16).replace('T',' ')+'Z':'';
const LABEL={COMPLETE:'Complete',INCOMPLETE:'Incomplete',NOT_APPLICABLE:'N/A'};
const pill=s=>{const p=el('span',LABEL[s]||'Missing','pill s-'+(s||'MISSING'));p.title=s||'MISSING';return p;};
const caseIds=Object.keys(D.cases).sort((a,b)=>Number(a.split('_')[1])-Number(b.split('_')[1]));
const outputs=(c,stage)=>c.events.find(e=>e.stage===stage)?.outputs||{};
const stepFields=['step_no','phase','system_id','resource_id','operation','state','evidence_id'];
const mismatch=c=>Boolean(c.expected)&&(!c.produced||c.expected[0]!==c.produced[0]||c.expected[1].length!==c.produced[1].length||c.expected[1].some((s,i)=>stepFields.some(f=>s[f]!==c.produced[1][i]?.[f])));
const gaps=c=>outputs(c,'steps').gaps||[];
const notes=c=>[...c.validation_errors,...c.warnings];
const usesInferred=c=>Object.values(outputs(c,'steps').resource_links||{}).includes('inferred');
let selected=null;

/* ---------- evidence viewer ---------- */
function eligibility(key,c){
 const source=D.sources[key]||{},file=D.files[source.file_id||key],cutoff=Date.parse(c.request.audit_at),late=[];
 if(file&&Date.parse(file.captured_at)>cutoff)late.push('file captured '+when(file.captured_at));
 if(source.observed_at&&Date.parse(source.observed_at)>cutoff)late.push('record time '+when(source.observed_at));
 if(late.length)return {ok:false,tag:'After audit_at',text:'Not usable: '+late.join(' and ')+' is after audit_at '+when(c.request.audit_at)+'.'};
 if(c.excluded.includes(key))return {ok:false,tag:'Excluded',text:'Excluded by the pipeline cutoff. No exact reason was recorded.'};
 const timed=file||source.observed_at;
 return {ok:timed?true:null,tag:'',text:timed?'Available at audit_at. Timing alone does not prove it is relevant.':'No timestamp recorded for this item.'};
}
function srcButton(key){return btn(key,'link',()=>showSource(key));}
function showSource(key){
 const c=D.cases[selected],source=D.sources[key],file=D.files[source?.file_id||key],body=$('src-body');
 $('src-title').textContent=key;body.replaceChildren();
 if(c){const e=eligibility(key,c);body.append(el('p',e.text,e.ok===false?'note-bad':e.ok?'note-ok':'muted'));
  const moved=c.moved.filter(l=>l.source===key);if(moved.length)body.append(el('p','Assigned to '+[...new Set(moved.flatMap(l=>l.owners))].join(', ')+'.','note-warn'));}
 if(source?.source!==undefined){body.append(el('h3','Recorded text'),pre(source.source));}
 if(source){const {source:_,...meta}=source;sub(body,'Parsed fields',meta);}
 if(file){const {text,data_url,...meta}=file;body.append(el('p',file.file_path+' · captured '+when(file.captured_at)+' · '+file.source_origin,'muted'));
  if(data_url?.startsWith('data:image/png')){const img=el('img');img.src=data_url;img.alt=file.file_id+' screenshot';body.append(img);
   const status=D.meta.llm?.images?.[file.file_id]?.review_status;if(status)body.append(el('p','Screenshot facts: '+status,'muted'));}
  else if(data_url){const a=el('a','Save the original PDF');a.href=data_url;a.download=file.file_path.split('/').pop();body.append(a);}
  else{const d=el('details',undefined,'sub');d.append(el('summary','Original file text'));d.addEventListener('toggle',()=>{if(d.open&&d.childElementCount===1){const ol=el('ol',undefined,'lines');for(const line of text.split('\n'))ol.append(el('li',line));d.append(ol);}});body.append(d);}}
 if(!source&&!file)body.append(el('p','Not found in the trace or the participant manifest.','note-bad'));
 const dlg=$('src-dialog');if(!dlg.open)dlg.showModal();dlg.scrollTop=0;
}

/* ---------- run details drawer ---------- */
function usageTable(parent,byModel){table(parent,['Model','Calls','Input','Output','Total','Cost'],Object.entries(byModel||{}).map(([m,u])=>[m,u.calls,u.reported_input_tokens,u.reported_output_tokens,u.reported_total_tokens,u.estimated_cost_usd??'not priced']));}
function llmTimeline(parent,l){
 const attempts=Object.values(l.results).flatMap(r=>r.attempts.map(a=>({r,a}))).sort((x,y)=>x.a.started_at.localeCompare(y.a.started_at));
 if(!attempts.length){parent.append(el('p','No new calls in this run (everything came from the cache).','empty'));return;}
 const t0=Math.min(...attempts.map(x=>Date.parse(x.a.started_at))),t1=Math.max(...attempts.map(x=>Date.parse(x.a.ended_at))),span=Math.max(t1-t0,1);
 const box=el('div',undefined,'timeline');box.setAttribute('role','img');box.setAttribute('aria-label',attempts.length+' calls overlapping over '+(span/1000).toFixed(1)+' seconds');
 for(const {r,a} of attempts){const lane=el('div',undefined,'lane'),bar=el('i',undefined,a.error?'fail':'');bar.style.left=(100*(Date.parse(a.started_at)-t0)/span)+'%';bar.style.width=Math.max(.4,100*(Date.parse(a.ended_at)-Date.parse(a.started_at))/span)+'%';bar.title=r.task+' '+r.key.slice(0,10)+(a.error?' (failed)':'');lane.append(bar);box.append(lane);}
 parent.append(box,el('p',attempts.length+' calls over '+(span/1000).toFixed(1)+' s. Bars that overlap ran in parallel; red bars failed.','muted'));
 const t=el('div');table(t,['Task','Model','Try','Seconds','Tokens','Error'],attempts.map(({r,a})=>[r.task+' '+r.key.slice(0,8),a.response_model||r.model,a.number,a.duration_seconds??'',a.usage?.total_tokens??'unknown',a.error||'']));sub(parent,'Call table',t);
}
function buildRun(){
 const body=$('run-body'),s=D.scores,m=s.step_multiset||{},a=s.analytics||{},l=D.meta.llm;
 body.append(el('p','Trace '+when(D.meta.created_at)+' · stage '+D.meta.stage+' · '+caseIds.length+' cases','muted'));
 if(!D.meta.publication_blocked&&s.confusion){
  let b=fold(body,'Sample scores',undefined,true);
  const statuses=['COMPLETE','INCOMPLETE','NOT_APPLICABLE','MISSING'];
  b.append(el('h4','Expected (rows) vs predicted (columns)'));table(b,['',...statuses],Object.entries(s.confusion).map(([k,v])=>[k,...statuses.map(x=>v[x]||0)]));
  b.append(el('h4','Steps'));table(b,['Measure','Value'],[['Matched / expected / produced',m.matched+' / '+m.expected+' / '+m.predicted],['Multiset accuracy',pct(m.accuracy)],['Precision',pct(m.precision)],['Recall',pct(m.recall)],['Positional accuracy',pct(s.step_positional_accuracy)],['Sequence accuracy',pct(s.sequence_accuracy)]]);
  b.append(el('h4','Fields'));table(b,['Field','Accuracy'],Object.entries(s.field_accuracy).map(([k,v])=>[k,pct(v)]));
  b.append(el('h4','By link type'));table(b,['Link type','Matched / expected','Step accuracy','Sequence'],Object.entries(a.by_link_type||{}).map(([k,v])=>v?[k,v.step_multiset.matched+' / '+v.step_multiset.expected,pct(v.step_multiset.accuracy),pct(v.sequence_accuracy)]:[k,'not used','','']));
  if((s.validation_errors||[]).length){b.append(el('h4','Validator errors'));list(b,s.validation_errors);}
 }
 if(l){
  const b=fold(body,'LLM run',undefined,true);
  b.append(el('h4','This run'));usageTable(b,l.usage_current_by_model);
  b.append(el('h4','Cached from earlier runs'));usageTable(b,l.usage_cached_historical_by_model);
  b.append(el('h4','Call timeline'));llmTimeline(b,l);
  if(l.issues.length){b.append(el('h4','Needs review ('+l.issues.length+')'));table(b,['Item','Issue','Detail'],l.issues.map(i=>[i.file_id?srcButton(i.file_id):(i.case_id||''),(i.kind||'').replaceAll('_',' '),i.status||(i.missing||[]).join('; ')]));}
  sub(b,'Preflight',l.preflight);
 }
 const rules=fold(body,'Deletion rules',D.rules.length);if(D.rules.length)table(rules,Object.keys(D.rules[0]),D.rules.map(r=>Object.values(r)));
 const files=fold(body,'Evidence files',Object.keys(D.files).length);
 const sel=el('select');sel.setAttribute('aria-label','Open an evidence file');sel.append(new Option('Choose a file',''));for(const [id,f] of Object.entries(D.files))sel.append(new Option(id+' · '+f.file_path,id));
 sel.addEventListener('change',()=>{if(sel.value){showSource(sel.value);sel.value='';}});files.append(sel);
 list(fold(body,'Limitations',(D.meta.limitations||[]).length),D.meta.limitations||[]);
}

/* ---------- case page ---------- */
const STAGE_NAME={intent:'Intent',identity:'Identity',linking:'Linking',cutoff:'Cutoff',state:'State',policy:'Policy',steps:'Steps'};
function method(e){
 if(e.stage==='intent'&&/LLM/.test(e.decided_by||''))return 'llm';
 if(e.stage==='linking'&&(e.outputs.links||[]).some(l=>l.type==='inferred'))return 'inferred';
 if(e.stage==='steps'&&Object.values(e.outputs.resource_links||{}).includes('inferred'))return 'inferred';
 return 'rules';
}
function oneLine(e){const o=e.outputs;switch(e.stage){
 case 'intent':return (o.decision==='DELETE_AUTHORIZED'?'Deletion':o.decision||'?').replace('_',' ').toLowerCase();
 case 'identity':return o.user_id||'unresolved';
 case 'linking':return (o.links||[]).length+' links';
 case 'cutoff':return (o.excluded_resource_observations||[]).length+(o.excluded_link_sources||[]).length+' excluded';
 case 'state':return Object.keys(o).length+' resources';
 case 'policy':{const v=Object.values(o).filter(x=>x.verdict==='violation').length;return Object.keys(o).length+' resources, '+v+' violations';}
 case 'steps':return (o.status||'').toLowerCase()+', '+(o.steps||[]).length+' steps';
 default:return '';}}
function stageDetail(box,e,c){
 box.replaceChildren();
 const how={rules:'Rules',llm:'LLM',inferred:'Inferred link'}[method(e)];
 box.append(el('h3',STAGE_NAME[e.stage]||e.stage));
 box.append(el('p',how+(e.decided_by?' · '+e.decided_by:'')+' · '+when(e.started_at)+' to '+when(e.ended_at).slice(11),'muted'));
 if(e.evidence.length){const row=el('div',undefined,'row');for(const k of e.evidence)row.append(srcButton(k));box.append(row);}
 box.append(el('h4','Outputs'),pre(e.outputs));
 sub(box,'Inputs',e.inputs);
}
function flow(parent,c){
 const wrap=el('div'),ol=el('ol',undefined,'flow'),box=el('div',undefined,'stage-detail');box.hidden=true;let active=null;
 for(const e of c.events){const li=el('li'),b=el('button',undefined,'stage m-'+method(e));b.type='button';b.setAttribute('aria-pressed','false');
  b.append(el('b',STAGE_NAME[e.stage]||e.stage),el('span',oneLine(e)));
  b.addEventListener('click',()=>{const same=active===b;if(active)active.setAttribute('aria-pressed','false');active=same?null:b;box.hidden=same;if(!same){b.setAttribute('aria-pressed','true');stageDetail(box,e,c);}});
  li.append(b);ol.append(li);}
 const legend=el('div',undefined,'legend');for(const [cls,t] of [['rules','Rules'],['llm','LLM'],['inferred','Inferred link']]){const s=el('span',t,'m-'+cls);s.prepend(el('i'));legend.append(s);}
 wrap.append(ol,legend,box);parent.append(wrap);
}
function evCell(v){const td=el('td',undefined,'mono');if(v)td.append(srcButton(v));return td;}
function stepsTable(parent,produced,expected,mode){
 const compare=mode==='Compare',rowsP=mode==='Expected'?expected:produced;
 const n=compare?Math.max(produced.length,expected.length):rowsP.length;
 if(!n){parent.append(el('p','No steps.','empty'));return;}
 const w=el('div',undefined,'tw'),t=el('table'),tr=el('tr');
 const head=compare?['',...stepFields]:stepFields;for(const h of head){const th=el('th',h==='' ? undefined:h);if(h==='')th.append(el('span','Match','sr'));th.scope='col';tr.append(th);}
 const hd=el('thead');hd.append(tr);t.append(hd);const tb=el('tbody');
 for(let i=0;i<n;i++){
  const p=compare?produced[i]:rowsP[i],e=compare?expected[i]:null,r=el('tr');
  const differs=compare&&p&&e&&stepFields.some(f=>p[f]!==e[f]);
  if(compare){const state=!p?'missing':!e?'extra':differs?'differs':'';r.className=state;r.append(el('td',state,'muted'));}
  for(const f of stepFields){
   const pv=p?.[f],ev=e?.[f];
   if(compare&&p&&e&&pv!==ev){const td=el('td',undefined,'diff mono'),show=x=>x===''?'(empty)':String(x??'');
    td.append(el('del',show(pv)),el('ins',show(ev)));r.append(td);}
   else if(f==='evidence_id'){r.append(evCell(p?pv:ev));}
   else r.append(el('td',String((p?pv:ev)??''),f==='step_no'?'':'mono'));
  }
  tb.append(r);
 }
 t.append(tb);w.append(t);parent.append(w);
 if(compare)parent.append(el('p','Struck-through values are produced; green values are expected. Comparison is by array position.','muted'));
}
function stepsCard(parent,c){
 const {body,head}=card(parent,'Steps'),produced=c.produced?.[1]||[],expected=c.expected?.[1]||null;
 const out=el('div');
 if(!c.produced)body.append(el('p','No prediction was exported for this sample.','note-bad'));
 if(!expected){body.append(out);stepsTable(out,produced,[],'Produced');return;}
 const modes=['Compare','Produced','Expected'],seg=el('div',undefined,'seg');
 const draw=m=>{out.replaceChildren();stepsTable(out,produced,expected,m);for(const b of seg.children)b.setAttribute('aria-pressed',String(b.textContent===m));};
 for(const m of modes)seg.append(btn(m,'',()=>draw(m)));
 head.append(el('span',mismatch(c)?'Differs from expected':'Matches expected',mismatch(c)?'pill tag red':'pill tag'),seg);
 head.style.justifyContent='flex-start';head.firstChild.style.marginRight='auto';
 body.append(out);draw(mismatch(c)?'Compare':'Produced');
}
function llmFold(parent,c){
 const keys=c.events.map(e=>e.outputs.llm_key).filter(Boolean);if(!keys.length||!D.meta.llm)return;
 const b=fold(parent,'LLM calls',keys.length);
 for(const key of keys){const r=D.meta.llm.results[key];if(!r)continue;
  const att=r.attempts.length,tok=r.attempts.reduce((s,a)=>s+(a.usage?.total_tokens||0),0);
  b.append(el('p',r.task+' · '+r.model+' · '+(r.cache_hit?'cached':att+' attempt(s), '+tok+' tokens')));
  b.append(el('p',r.keyword_disagreement?'Disagrees with the keyword rules.':'Agrees with the keyword rules.',r.keyword_disagreement?'note-bad':'note-ok'));
  sub(b,'Prompt',r.prompt);sub(b,'Raw response',r.raw_response||'');sub(b,'Parsed',r.parsed);
  if(att){const t=el('div');table(t,['Try','Started','Seconds','Tokens','Error'],r.attempts.map(a=>[a.number,when(a.started_at),a.duration_seconds??'',a.usage?.total_tokens??'unknown',a.error||'']));sub(b,'Attempts',t);}}
}
function evidenceFolds(parent,c){
 const excluded=new Set(c.excluded),moved=new Set(c.moved.map(l=>l.source)),used=c.sources.filter(k=>!excluded.has(k)&&!moved.has(k));
 const e=fold(parent,'Evidence used',used.length);
 if(used.length){const ul=el('ul',undefined,'srclist');for(const k of used){const li=el('li'),s=D.sources[k]||{};li.append(srcButton(k),el('span',s.origin||'','muted'));const g=eligibility(k,c);if(g.tag)li.append(el('span',g.tag,'pill tag red'));ul.append(li);}e.append(ul);}
 else e.append(el('p','No evidence was recorded for this case.','empty'));
 const x=[...excluded],count=x.length+c.moved.length;
 const b=fold(parent,'Excluded or assigned elsewhere',count);
 if(!count)b.append(el('p','Nothing was excluded.','empty'));
 if(x.length)table(b,['Source','Reason'],x.map(k=>[srcButton(k),eligibility(k,c).text]));
 if(c.moved.length){b.append(el('h4','Assigned to another request'));table(b,['Resource','Request(s)','Source'],c.moved.map(l=>[l.resource_id,l.owners.join(', '),srcButton(l.source)]));}
}
function identityFold(parent,c){
 const id=outputs(c,'identity'),link=outputs(c,'linking');
 const b=fold(parent,'Identity and links',c.history.length+' history');
 if(!Object.keys(id).length){b.append(el('p','No identity was resolved.','empty'));return;}
 if(c.identity_source){b.append(el('h4','Current snapshot'));b.append(srcButton(c.identity_source));}
 const ids=id.identifiers||{};b.append(el('h4','Identifiers used'));
 table(b,['Kind','Values'],Object.entries(ids).map(([k,v])=>[k,v.map(x=>x.length>40?x.slice(0,16)+'...':x).join(', ')]),true);
 if(c.history.length){b.append(el('h4','Earlier values'));table(b,['Audit','Field','Old','New','Time'],c.history.map(a=>{const r=D.sources[a].row;return [srcButton(a),r.field_name,r.old_value,r.new_value,when(r.event_time)];}));}
 b.append(el('h4','Links'));
 if((link.links||[]).length)table(b,['Resource','Method','Time','Source'],link.links.map(l=>[l.resource_id,el('span',l.type,l.type==='inferred'?'pill tag amber':'pill tag'),when(l.time),srcButton(l.source)]));
 else b.append(el('p','No resources were linked.','empty'));
}
function analysisFold(parent,c){
 const a=D.scores.analytics?.per_case?.[selected];if(!a)return;
 const lines=[...(a.missing_resources.length?['Expected but not linked: '+a.missing_resources.join(', ')]:[]),...(a.extra_resources.length?['Linked but not expected: '+a.extra_resources.join(', ')]:[]),...a.likely_causes];
 if(lines.length)list(fold(parent,'Why it may differ',lines.length),lines);
}
function showCase(cid,focus){
 selected=cid;const c=D.cases[cid],main=$('case-detail'),r=c.request;main.replaceChildren();
 const head=el('section',undefined,'case-head'),h=el('h2',cid);h.append(pill(c.produced?.[0]),el('span',c.kind==='sample'?'Sample':'Evaluation','pill tag'));
 head.append(h);
 const facts=el('div',undefined,'facts');
 for(const [k,v] of [['Email',r.email],['Requested',when(r.requested_at)],['Audit cutoff',when(r.audit_at)],['Workflow claimed',r.reported_status+' ('+when(r.reported_at)+')']]){const s=el('span',k+' ');s.append(el('b',v));facts.append(s);}
 head.append(facts,el('p',r.request_text,'quote'));main.append(head);
 const all=[...notes(c).map(m=>['err',m]),...gaps(c).map(m=>['gap','Gap: '+m])];
 if(all.length){const d=el('details',undefined,'banner'+(notes(c).length?' err':''));d.append(el('summary',all.length+(all.length===1?' note':' notes')+' on this case'));list(d,all.map(x=>x[1]));main.append(d);}
 flow(main,c);stepsCard(main,c);
 analysisFold(main,c);evidenceFolds(main,c);identityFold(main,c);llmFold(main,c);
 for(const b of document.querySelectorAll('#case-list button'))b.setAttribute('aria-current',String(b.dataset.case===cid));
 main.scrollTop=0;if(focus)main.focus({preventScroll:true});
}

/* ---------- list and filters ---------- */
const filter={kind:'sample',status:'',flags:new Set()};
const FLAGS=[['mismatch','Mismatch',c=>mismatch(c)],['inferred','Inferred link',usesInferred],['disagree','LLM disagreed',c=>outputs(c,'intent').keyword_disagreement],['notes','Has notes',c=>gaps(c).length||notes(c).length]];
function matches(id){
 const c=D.cases[id],q=$('search').value.trim().toLowerCase();
 if(filter.kind&&filter.kind!==c.kind)return false;if(filter.status&&filter.status!==c.produced?.[0])return false;
 for(const f of FLAGS)if(filter.flags.has(f[0])&&!f[2](c))return false;
 return !q||[id,c.request.email,...Object.keys(outputs(c,'steps').resource_links||{})].join(' ').toLowerCase().includes(q);
}
function updateList(){
 const ids=caseIds.filter(matches),ul=$('case-list');ul.replaceChildren();$('case-count').textContent=ids.length+' of '+caseIds.length+' cases';
 for(const id of ids){const c=D.cases[id],li=el('li'),b=el('button');b.type='button';b.dataset.case=id;b.setAttribute('aria-current',String(id===selected));
  const dot=el('span',undefined,'dot'+(c.expected?(mismatch(c)?' no':' ok'):''));dot.title=c.expected?(mismatch(c)?'Differs from the sample answer':'Matches the sample answer'):'No expected answer';
  b.append(dot,el('span',id,'id'),pill(c.produced?.[0]),el('span',c.request.email,'email'));
  b.addEventListener('click',()=>{history.replaceState(null,'','#'+id);showCase(id,true);});li.append(b);ul.append(li);}
 if(!ids.length){selected=null;$('case-detail').replaceChildren(el('p','No cases match. Clear the search or a filter.','empty'));}
 else if(!ids.includes(selected))showCase(ids[0]);
}
function buildChips(){
 const box=$('chips'),groups=[
  [[['sample','Samples'],['evaluation','Evaluation']],'kind'],
  [[['COMPLETE','Complete'],['INCOMPLETE','Incomplete'],['NOT_APPLICABLE','N/A']],'status']];
 const draw=()=>{for(const b of box.children){const [g,v]=b.dataset.key.split(':');b.setAttribute('aria-pressed',String(g==='flag'?filter.flags.has(v):filter[g]===v));}updateList();};
 const add=(g,v,t)=>{const b=btn(t,'chip',()=>{if(g==='flag'){filter.flags.has(v)?filter.flags.delete(v):filter.flags.add(v);}else filter[g]=filter[g]===v?'':v;draw();});b.dataset.key=g+':'+v;box.append(b);};
 for(const [items,g] of groups)for(const [v,t] of items)add(g,v,t);
 for(const [v,t,test] of FLAGS)if((v!=='disagree'||D.meta.llm)&&(v!=='inferred'||caseIds.some(id=>test(D.cases[id]))))add('flag',v,t);
 return draw;
}

/* ---------- start ---------- */
function summary(){
 const s=D.scores,m=s.step_multiset||{},l=D.meta.llm,tiles=$('tiles');
 const tile=(v,label,alert)=>{const t=el('div',undefined,'tile'+(alert?' alert':''));t.append(el('b',v),el('span',label));tiles.append(t);};
 $('run-sub').textContent=l?'LLM run · '+caseIds.length+' cases':'Keyword-only diagnostic · '+caseIds.length+' cases';
 if(D.meta.publication_blocked)tile('Blocked','publication gate',true);
 else if(s.confusion){tile(pct(s.status_accuracy),'status accuracy');tile(m.matched+' / '+m.expected,'steps matched');tile(s.exact_cases+' / '+s.cases,'exact cases');tile(pct(s.sequence_accuracy),'sequence accuracy');}
 if(l){const u=Object.values(l.usage_current_by_model||{});tile(String(u.reduce((a,x)=>a+x.calls,0)),'LLM calls this run');tile(u.reduce((a,x)=>a+(x.reported_total_tokens||0),0).toLocaleString(),'tokens this run');}
 const problems=[...D.warnings,...(D.meta.publication_blocked?['Publication is blocked. This page is a diagnostic only and no new sample accuracy was produced.']:[])];
 if(problems.length){const d=el('details',undefined,'banner'+(D.warnings.length?' err':''));d.append(el('summary',problems.length+(problems.length===1?' warning':' warnings')+' on this run'));list(d,problems);$('banner').append(d);}
}
for(const dlg of document.querySelectorAll('dialog'))dlg.addEventListener('click',e=>{if(e.target===dlg||e.target.hasAttribute('data-close'))dlg.close();});
$('open-run').addEventListener('click',()=>{if(!$('run-body').childElementCount)buildRun();$('run-dialog').showModal();});
$('case-list').addEventListener('keydown',e=>{
 const b=e.target.closest('button');if(!b||!['ArrowDown','ArrowUp','Home','End'].includes(e.key))return;e.preventDefault();
 const items=[...$('case-list').querySelectorAll('button')],i=items.indexOf(b);
 const next=items[e.key==='Home'?0:e.key==='End'?items.length-1:Math.max(0,Math.min(items.length-1,i+(e.key==='ArrowDown'?1:-1)))];
 next.focus();history.replaceState(null,'','#'+next.dataset.case);showCase(next.dataset.case);
});
summary();
const initial=location.hash.slice(1);if(D.cases[initial]){selected=initial;filter.kind=D.cases[initial].kind;}
const redraw=buildChips();$('search').addEventListener('input',updateList);redraw();
if(selected)showCase(selected);
</script></body></html>'''


def main():
    data = build_data()
    path = ROOT / "output" / "review.html"
    path.write_text(render(data), encoding="utf-8")
    print(f"Wrote {path} ({len(data['cases'])} cases; {path.stat().st_size:,} bytes)")
    for warning in data["warnings"]:
        print("Review warning: " + warning)


if __name__ == "__main__":
    main()
