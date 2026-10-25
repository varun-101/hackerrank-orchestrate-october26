"""Run the pipeline: python code/main.py --samples | --full [--refresh-llm].

Both modes prepare the LLM results and compute all requests together (person/request assignment
needs them), then write the sample gate files in output/. --full also writes the root output.csv
for the evaluation requests (only after it validates) and output/usage.json for this run.
"""

import argparse
import json
import os
import time

import evidence
import output
import pipeline
import run_trace
import llm_tasks
from evaluation.main import load, print_summary, sample_analytics, score

PROVIDER = "OpenAI Responses API via Sign in with ChatGPT"
TOKEN_FIELDS = ("input_tokens", "output_tokens", "reasoning_tokens", "cached_tokens", "total_tokens")
COST_NOTE = ("Not priced here: the ChatGPT subscription covers the marginal cost. The pay-as-you-go "
             "estimate is filled in code/evaluation/usage_report.md.")


def write_submission(results, case_ids):
    """Write the root output.csv only if it validates; return the validation errors.

    The rows go to a temporary file first, so a failed run never leaves a partial or invalid
    output.csv and never replaces an earlier valid one.
    """
    final = evidence.REPO_ROOT / "output.csv"
    temp = final.with_name("output.csv.tmp")
    try:
        output.write_output(temp, [(case_id, *results[case_id]) for case_id in case_ids])
        errors = output.validate_output(temp, case_ids)
        if not errors:
            os.replace(temp, final)
    finally:
        temp.unlink(missing_ok=True)
    return errors


def _tokens(attempts):
    """Sum the reported usage of attempts; an attempt without usage is counted as unknown, not zero."""
    known = [a["usage"] for a in attempts if a["usage"] is not None]
    totals = {"attempts": len(attempts), "failed_attempts": sum(a["error"] is not None for a in attempts),
              "unknown_usage_attempts": len(attempts) - len(known)}
    for name in TOKEN_FIELDS:
        totals[name] = sum(u[name] or 0 for u in known)
    return totals


def _section(results, field, evaluation_count):
    """Totals over the attempts stored under `field` of each LLM result, with per-request averages."""
    def grouped(key):
        return {name: _tokens([a for r in results if r[key] == name for a in r[field]])
                for name in sorted({r[key] for r in results})}
    attempts = [a for r in results for a in r[field]]
    total = _tokens(attempts)
    return {"unique_requests": len(results),
            "response_models_seen": sorted({a["response_model"] for a in attempts if a["response_model"]}),
            "total": total, "by_task": grouped("task"), "by_model": grouped("model"),
            "per_evaluation_request": {name: round(total[name] / evaluation_count, 2)
                                       for name in ("attempts", *TOKEN_FIELDS)}}


def usage_summary(llm_info, refresh, evaluation_count, wall_seconds, llm_seconds):
    """Usage of this run. Cache hits made no call; their stored attempts are reported separately."""
    results = list(llm_info["results"].values())
    live = [r for r in results if not r["cache_hit"]]
    cached = [r for r in results if r["cache_hit"]]
    return {
        "mode": "cold" if refresh else "cached",
        "provider": PROVIDER,
        "models_requested": sorted({r["model"] for r in results}),
        "evaluation_requests": evaluation_count,
        "unique_llm_requests": {"total": len(results),
                                **{task: sum(r["task"] == task for r in results)
                                   for task in sorted({r["task"] for r in results})}},
        "cache_hits": len(cached),
        "live_requests": len(live),
        "failed_requests": sum(r["error"] is not None for r in results),
        "wall_clock_seconds": round(wall_seconds, 2),
        "llm_batch_seconds": round(llm_seconds, 2),
        "this_run": _section(live, "attempts", evaluation_count),
        "cache_built_not_this_run": {
            "note": "Attempts stored in the cache entries used by this run. They were made earlier, "
                    "when the cache was built, not by this run.",
            **_section(cached, "historical_attempts", evaluation_count)},
        "estimated_cost_usd": None,
        "estimated_cost_per_evaluation_request_usd": None,
        "cost_note": COST_NOTE,
    }


def main(argv=None):
    started = time.monotonic()
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--samples", action="store_true", help="Sample gate only; no root output.csv")
    mode.add_argument("--full", action="store_true", help="Also write the root output.csv and output/usage.json")
    parser.add_argument("--refresh-llm", action="store_true", help="Refresh model results and stop on differences")
    parser.add_argument("--keyword-only-diagnostic", action="store_true",
                        help="Offline core regression only; excludes all LLM tasks and image facts")
    args = parser.parse_args(argv)
    if args.refresh_llm and args.keyword_only_diagnostic:
        parser.error("--refresh-llm requires the standard LLM path")
    if args.full and args.keyword_only_diagnostic:
        parser.error("--full requires the standard LLM path")
    directory = evidence.REPO_ROOT / "output"
    directory.mkdir(exist_ok=True)
    requests = pipeline.read_requests()
    context = None
    llm_seconds = 0
    if not args.keyword_only_diagnostic:
        llm_started = time.monotonic()
        try:
            context = llm_tasks.prepare(requests, refresh=args.refresh_llm)
        except llm_tasks.ReviewRequired as exc:
            llm_tasks.write_diagnostics(exc.trace, directory)
            import review
            review.write_llm_diagnostic(exc.trace, directory / "llm_review.html")
            print(str(exc))
            print("See output/llm_review.html, llm_review.md, llm_diagnostic_trace.json and screenshot_review_draft.md")
            return 2
        llm_seconds = time.monotonic() - llm_started
    results, trace = pipeline.run(requests, llm_context=context)
    samples = output.read_rows(evidence.DATASET / "sample_deletion_requests.csv")
    predictions = {r["case_id"]: results[r["case_id"]] for r in samples}
    path = directory / "sample_predictions.csv"
    output.write_output(path, [(cid, *answer) for cid, answer in predictions.items()])
    errors = output.validate_output(path, predictions)
    # Expected answers enter only evaluation, after the pipeline has finished.
    expected = load(evidence.DATASET / "sample_output.csv")
    scores = score(expected, predictions)
    scores["analytics"] = sample_analytics(expected, predictions, trace)
    scores["validation_errors"] = errors
    (directory / "sample_scores.json").write_text(json.dumps(scores, indent=2), encoding="utf-8")
    run_trace.write_trace(trace, directory / "trace.json")
    print_summary(scores)
    for error in errors:
        print("Stage gap (not submission-ready): " + error)
    print("\nSample diagnostic: proven, lookup and inferred links; remaining gaps are recorded in the trace.")
    print("Wrote output/sample_predictions.csv, output/sample_scores.json, output/trace.json")
    if not args.full:
        return 0

    case_ids = [r["case_id"] for r in output.read_rows(evidence.DATASET / "deletion_requests.csv")]
    submission_errors = write_submission(results, case_ids)
    if submission_errors:
        print(f"\nFull run FAILED validation ({len(submission_errors)} error(s)); output.csv was not written:")
        for error in submission_errors[:20]:
            print("  " + error)
        return 1
    usage = usage_summary(context["trace"]["llm"], args.refresh_llm, len(case_ids),
                          time.monotonic() - started, llm_seconds)
    (directory / "usage.json").write_text(json.dumps(usage, indent=2) + "\n", encoding="utf-8")
    live = usage["this_run"]["total"]
    print(f"\nFull run ({usage['mode']}): wrote {len(case_ids)} rows to output.csv, validation passed.")
    empty = [c for c in case_ids if results[c][0] == "COMPLETE" and not results[c][1]]
    if empty:
        print(f"Warning: {len(empty)} COMPLETE row(s) with no steps (nothing linked had a usable state): "
              + ", ".join(empty))
    print(f"LLM this run: {usage['live_requests']} live requests, {live['attempts']} attempts, "
          f"{usage['cache_hits']} cache hits, {live['failed_attempts']} failed attempts, "
          f"{live['input_tokens']} input / {live['output_tokens']} output / {live['total_tokens']} total tokens.")
    print("Wrote output.csv and output/usage.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
