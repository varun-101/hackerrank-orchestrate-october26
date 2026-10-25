# Token Usage and Cost Report

This report covers the final full-dataset run that produced the submitted `output.csv`, and the
model calls that built the answers that run used.

Source: `output/usage.json`, written by `python code/main.py --full`, and the call records stored
in `code/cache/llm_cache.json`.

## Summary

| Item | Value |
|---|---|
| Model provider | OpenAI (Responses API, signed in with a ChatGPT subscription) |
| Model | `gpt-6.1-sol` (the only model used, requested and served) |
| Final run | `python code/main.py --full`, cached, 2026-10-25 |
| Model calls in the final run | 0 (all 409 distinct model requests answered from the cache) |
| Model calls behind the cached answers | 412 attempts for 409 requests (3 network timeouts, each retried) |
| Input tokens | 101,652 |
| Output tokens | 25,523 (of which 676 reasoning tokens) |
| Total tokens | 127,175 |
| Average tokens per evaluation request | 245.5 (196.2 input, 49.3 output) |
| Estimated cost, pay-as-you-go | $0.46 total, $0.00089 per evaluation request |
| Actual marginal cost | $0 (covered by the ChatGPT subscription) |
| Wall-clock time of the final run | 15.3 s (model step 2.1 s, read from the cache) |

## Why the final run made no calls

The pipeline uses the model in exactly two places (see `README.md` and `code/PLAN.md` section 2):

- **Request intent:** whether the request text authorizes deletion. There is one call per
  distinct request text, and 398 distinct texts cover all 550 requests (518 evaluation plus 32
  samples).
- **Screenshot facts:** facts read from the 11 PNG evidence images, then checked by hand.

Every answer is cached under a hash of the full request: model, prompt, prompt version, schema
and, for images, the image bytes. The final run found all 409 requests in the cache, so it made
no live calls and used no tokens.

The token figures in this report are those recorded by the calls that built the cache entries
the final run used. That is the model work behind `output.csv`.

Before the final run, a cold run (`--full --refresh-llm`) on 2026-10-25, 07:22 to 07:28 UTC,
re-asked all 409 requests in 415 attempts. Every intent decision matched the cache. But 225 intent
explanations were worded differently, and all 11 screenshot readings differed from the
hand-checked facts. The review gate therefore stopped the run before it wrote `output.csv`, as
designed, and none of its new answers were used.

For the 173 requests whose answers came back identical, that run stored its call records in the
cache in place of the earlier ones. Their token counts were unchanged. The totals above therefore
mix those 173 cold-run attempts with the original calls for the other 236 requests. The token use
of the 236 differing answers was not kept in the repository and is not counted here.

## Per task

| Task | Distinct requests | Attempts | Input tokens | Output tokens | Total tokens | Avg tokens per evaluation request | Estimated cost |
|---|---|---|---|---|---|---|---|
| Intent | 398 | 401 (3 timeouts retried) | 72,623 | 13,293 | 85,916 | 165.9 | $0.278 |
| Screenshot facts | 11 | 11 | 29,029 | 12,230 (676 reasoning) | 41,259 | 79.7 | $0.180 |
| **Total** | **409** | **412** | **101,652** | **25,523** | **127,175** | **245.5** | **$0.459** |

Only one model was used, so the per-model total equals the overall total:

| Model | Attempts | Input tokens | Output tokens | Total tokens | Estimated cost |
|---|---|---|---|---|---|
| `gpt-6.1-sol` | 412 | 101,652 | 25,523 | 127,175 | $0.459 |
| **All models** | **412** | **101,652** | **25,523** | **127,175** | **$0.459** |

The 3 timed-out attempts reported no usage and are counted as zero tokens. No cached input
tokens were reported (0).

## Averages

The denominator is the 518 evaluation requests in `dataset/deletion_requests.csv`. The token
totals also cover the 32 sample requests, because identical request texts share one call.

| Per evaluation request | Value |
|---|---|
| Model attempts | 0.80 |
| Input tokens | 196.2 |
| Output tokens | 49.3 |
| Total tokens | 245.5 |
| Estimated cost | $0.00089 |

Per distinct model request (409): 310.9 tokens and $0.0011 on average.

## Cost estimate

Calls run on a ChatGPT subscription, so the actual marginal cost is $0. The pay-as-you-go
equivalent uses the published standard API price for `gpt-6.1-sol`: $2.00 per million input tokens
and $10.00 per million output tokens. Reasoning tokens are billed as output and are already
included in the output count.

| | Tokens | Price per million | Cost |
|---|---|---|---|
| Input | 101,652 | $2.00 | $0.2033 |
| Output | 25,523 | $10.00 | $0.2552 |
| **Total** | **127,175** | | **$0.4585** |
| Per evaluation request (518) | | | $0.000885 |

Price source: OpenAI, "Introducing GPT-6.1 Sol" (https://openai.com/index/introducing-gpt-6-1-sol/),
checked 2026-10-25. Prompts here are far below the long-context threshold, so the standard rate applies.

## Token efficiency

- One call per distinct request text, not per request: 398 intent calls serve 550 requests.
- Everything else runs as plain code with no model: parsing, identity, cutoff, policy, linking,
  steps, ordering and citations. That includes the 4 PDFs, read with `pypdf`.
- Cached answers make re-runs free and repeatable. A cold run (`--refresh-llm`) re-asks every
  request and stops before writing `output.csv` if any answer differs from the cache or from the
  reviewed screenshot facts.

## Reproduce

```bash
python code/main.py --full          # cached run: writes output.csv and output/usage.json
```

`output/usage.json` lists this run's live usage under `this_run` (zero for a cached run). It
lists the cache-building calls separately under `cache_built_not_this_run`, the source of the
figures above. No API keys, tokens or credentials appear in this report or in the cache.
