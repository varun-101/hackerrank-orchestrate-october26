"""Run the LLM sample gate: python code/main.py --samples.

All requests are computed for person/request assignment; only samples are exported.
This stage has no full-submission mode or inferred linking.
"""

import argparse
import json

import evidence
import output
import pipeline
import run_trace
import llm_tasks
from evaluation.main import load, print_summary, sample_analytics, score


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", action="store_true", required=True)
    parser.add_argument("--refresh-llm", action="store_true", help="Refresh model results and stop on differences")
    parser.add_argument("--keyword-only-diagnostic", action="store_true",
                        help="Offline core regression only; excludes all LLM tasks and image facts")
    args = parser.parse_args(argv)
    if args.refresh_llm and args.keyword_only_diagnostic:
        parser.error("--refresh-llm requires the standard LLM path")
    directory = evidence.REPO_ROOT / "output"
    directory.mkdir(exist_ok=True)
    requests = pipeline.read_requests()
    context = None
    if not args.keyword_only_diagnostic:
        try:
            context = llm_tasks.prepare(requests, refresh=args.refresh_llm)
        except llm_tasks.ReviewRequired as exc:
            llm_tasks.write_diagnostics(exc.trace, directory)
            import review
            review.write_llm_diagnostic(exc.trace, directory / "llm_review.html")
            print(str(exc))
            print("See output/llm_review.html, llm_review.md, llm_diagnostic_trace.json and screenshot_review_draft.md")
            return 2
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
    print("\nSample diagnostic: proven/lookup links only; missing links remain trace gaps.")
    print("Wrote output/sample_predictions.csv, output/sample_scores.json, output/trace.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
