# Ghost Data Still Exists: Solution Plan

Status: agreed plan (revision 5: decisions F1 to F3 from the milestone 1 ledger; keyword intent frozen and LLM work moved forward), in implementation. Sources: our own analysis of the
participant dataset, an initial independent analysis by Codex `gpt-6-astra`, and two rounds of
independent plan review by Codex `gpt-6-astra` and Claude `claude-opus-5-5`. All review items were
adopted by the user. Checked against the 32 public samples.

## 1. Goal and honest ceiling

Target: every decision the evidence fully determines is exact (intent, cutoff state, policy,
ordering, citations). 32/32 exact matches on `dataset/sample_output.csv` is the target.

Known ceiling: some expected resource IDs (deleted accounts and already-anonymized analytics
records) are never written next to the subject's identifiers in any participant file. They are
recovered by inference (section 6), which can be wrong; `account_004` (sample 04) appears in no
participant file at all and cannot be recovered. The cutoff stays strict (decision): 7 sample
verification steps for security audit records (samples 09, 18, 20, 23, 24, 29, 30) can only be
linked through files captured after `audit_at` or not at all, so they will be missing. Therefore each sample gate reports two numbers:
accuracy on steps backed by proven links, and accuracy on steps that rely on inferred links.
Hidden-set accuracy cannot be measured, so it is never claimed.

Guard rails:

- `dataset/sample_output.csv` is read only by evaluation and review code (`code/evaluation/`,
  `code/review.py`), never by the pipeline, the matching or any rule.
- No case-specific override and no stored per-request verdict is ever added. When results are
  wrong, the fix goes into general rules, parsers or prompts.

## 2. Architecture

A deterministic Python pipeline. `code/main.py` reads `dataset/` and writes `output.csv` at the
repository root. An LLM is used in exactly two places:

| Where | Why an LLM | Guard rail |
|---|---|---|
| Request intent (deletion vs `NOT_APPLICABLE`) | 6 languages, negations ("marketing lists only"), verification-of-earlier-deletion requests | Cross-checked against keyword rules; the user reviews disagreements and fixes go into the prompt or the rules, never into stored verdicts |
| The 11 PNG screenshots | Facts are only in pixels | Extracted by the model, verified by hand, verified facts stored in a machine-readable Markdown file bound to each image's hash |

Everything else (parsing, identity, cutoff, policy, linking, steps, ordering, citations) is plain
code. The 4 PDFs have extractable text (`pypdf`), so they need no LLM.

## 3. LLM provider: Sign in with ChatGPT (SIWC)

The LLM runs on the user's ChatGPT subscription through OpenAI's Responses API, authenticated
with Sign in with ChatGPT. Implementation follows OpenAI's official SIWC DevKit tutorial; exact
endpoints and helpers come from that tutorial at implementation time.

Files:

- `code/llm_auth.py`: a separate sign-in helper, run once by the user (`python code/llm_auth.py`).
  - Host ID: generate a `urn:uuid:...` once, persist it, reuse it across restarts.
  - First sign-in: open the browser for OAuth with PKCE, `client_id=dynamic_agent_client`, and a
    local callback on `127.0.0.1`.
  - Callback: receive the authorization code and the issued client ID, exchange the code for
    tokens, validate the ID token, and confirm the `chatgpt.tokens.use.direct` permission.
  - Storage: issued client ID, access token, refresh token, expiry and host ID in the user's
    profile folder (outside the repository). Refresh before expiry.
  - Output: prints a safe sign-in confirmation. `--run <command>` launches the
    pipeline with the current access token in its child environment, without
    printing the token or changing the parent terminal's environment.
- `code/llm.py`: sends a Responses API request with `store: false` and `stream: true`, reads the
  stream, returns the text, and records token usage from the final completed event.

Credential handling (decided with the user):

- Dependency decision (2026-10-25): the user approved `PyJWT[crypto]` for ID-token
  signature/claim verification and the OpenAI Python SDK as a declared runtime
  dependency. Keep the Python helper and public Responses API integration.

- The pipeline reads the access token only from an environment variable, as AGENTS.md requires.
  It never reads the credential file and never opens a browser.
- On a cache miss with no token in the environment, the run stops with a clear message telling
  the user to sign in; it never guesses an answer.
- Nothing secret is logged, committed or packaged. The credential file lives outside the repo.
- Model: `gpt-6.1-sol`, overridable through an environment variable.

Cache and reviewed facts:

- `code/cache/llm_cache.json` stores raw LLM answers. The key is a hash of the complete request:
  model, prompt text, prompt version, output schema version and, for screenshots, the image bytes.
  It stores no tokens or headers. It ships in `code.zip` so a judge can run without signing in.
- The cache is never hand-edited.
- `code/cache/reviewed_facts.md` holds only hand-verified screenshot facts, in a fenced block the
  code parses, each entry bound to the image's SHA-256. If an image hash changes, its entry is
  stale and the run stops until it is re-reviewed. The same file has a plain-text changelog of
  intent prompt and rule changes and why (never per-request verdicts).
- Precedence: verified screenshot facts win over raw model output for screenshots. For intent,
  the model plus rules decide.
- Cold-run reconciliation: a `--refresh-llm` run compares its parsed results with the previous
  run and with the verified screenshot facts. On any difference it stops before writing
  `output.csv` and reports the differences. The trace records both the fresh answer and the
  decision used.

## 4. Parallel LLM calls

So the user does not wait for hundreds of sequential calls:

- Before calling, collect every distinct LLM request for the run (about 400 unique request texts
  across 550 requests, plus 11 screenshots) and drop duplicates by cache key.
- Send them concurrently through a bounded worker pool (`concurrent.futures.ThreadPoolExecutor`,
  standard library). Pool size comes from an environment variable (default 8).
- Timeouts: a connect timeout and a stream-idle timeout per attempt, plus an overall per-request
  limit.
  User-approved exception (2026-10-25): a blocking operating-system DNS lookup can delay
  cancellation and worker shutdown beyond these limits. Keep the existing transport and
  document this limitation; do not add a resolver or process-isolation layer for it.
- Retries: on rate limiting, transient HTTP errors, timeouts or a stream that breaks mid-way,
  retry with exponential backoff and jitter, honouring any `Retry-After` header, up to a fixed
  number of attempts. Retrying is safe because requests use `store: false`. A 401/403 is not
  retried: all workers stop and the run fails with a sign-in message.
- Results are keyed by cache key, so completion order never changes the output.
- The main thread is the only cache writer. It saves after each completed result with an atomic
  write (temporary file, then rename), and again in a `finally` block, so a crash or Ctrl-C keeps
  finished work.
- If any required request is still unresolved after retries, the run exits non-zero and does not
  write `output.csv`.
- Usage: every attempt's reported usage is counted, including retried attempts. An attempt whose
  stream ended before usage was reported is counted as "usage unknown", not zero.
- A progress line shows done / total / failed while the batch runs.
- The deterministic stages are fast and stay sequential. Resource linking (section 6) needs all
  cases at once, so it runs after the LLM batch.
- The trace records every attempt's start and end time, so the review page shows which calls ran
  in parallel.

## 5. Pipeline

The deterministic stages always run on all 550 requests (32 samples plus 518 evaluation)
because resource linking compares cases with each other. "Sample-first" (section 8) controls
what is reported and when `output.csv` is produced, not what is computed.

1. **Load and normalize all evidence** into one list of observations. Each observation keeps:
   system, collection, category hints (`data_store`, `collection`, `record_type`), resource,
   identifiers, field values, record-level time (`observed_at` etc.), file capture time,
   `file_id`, and the exact source text (JSON object, log lines, SQL row) for citation and review.
   - JSON (28): walk nested objects and carry parent context down to each record. Merge
     duplicate observations of the same resource in one file (for example a search query wrapper
     and its nested document in `evidence_92`).
   - Logs (26): group lines by `trace_id`; a lookup line plus its result line form one
     observation. Ignore infrastructure noise; a cache miss is not proof of absence.
   - SQL (24): load into in-memory SQLite; keep primary and every replica table separately, and
     read `snapshot_scope` tables.
   - TXT (1), PDF (4): parse text. PNG (11): verified screenshot facts.
   - **Absence is an observation.** Empty snapshot collections (`records: []`), snapshot scopes
     with no matching row, and 404 profile lookups are recorded explicitly with their scope
     (which system, which tables: primary only or primary plus replicas) and time.
   - **Absence scope (decision F3).** An empty collection in a JSON snapshot bundle proves absence
     only for the people that bundle covers (it holds an eligible record tied to them); bundles
     list only their own subjects. A SQL snapshot is complete only for the systems and physical
     tables it declares (`snapshot_scope` rows, named copy tables): `evidence_90` covers the
     account primary only, so it cannot clear the failover copy covered by `evidence_54`.
2. **Activity stream.** Sort `system_activity.csv` by `event_time`, not by `activity_id` (rows
   `activity_4000` to `activity_4011` are out of order). Extract identifiers and resource IDs
   from the `details` text too: data-catalog links (`activity_4000` to `activity_4011`, for
   example `account_5762` to case_34's email), "repopulated downstream resource" sync rows,
   `RESTORE_BACKUP` targets, and ownership statements such as "belongs only to" (the condition of
   `rule_06`). Operation results stay claims, never proof of state. `CLASSIFY_REQUEST` rows are
   not a pipeline input: they name accounts that can only be mapped to emails through
   `evidence_50` (captured Oct 1, after the cutoffs), so they serve only as an offline
   development check of intent.
3. **Intent.** Classify each unique `request_text`; non-deletion gives `NOT_APPLICABLE` with `[]`.
   Verification of an earlier deletion counts as a deletion audit (samples 21, 25, 27).
   Cross-check with keyword rules; disagreements go to the user, and fixes change the prompt or
   rules. Until the LLM exists (milestone 3), keyword rules alone decide intent. The keyword rules
   are frozen (user decision): after the third review round they change only to fix a
   misclassified dataset text or an ordinary non-deletion request that gets authorized; the LLM
   is the primary intent judge and the rules are its cross-check.
4. **Identity graph.** Request email resolves to `user_id`; add all current and historical
   emails, phones and hashes from `user_audit_log.csv`. Match records by exact identifiers,
   `subject_reference` / `customer_reference` (which may hold a `user_id` or an email, as in
   `evidence_50`), and shared trace IDs. Never match on similar-looking names or emails
   (sample 28 warns about a decoy).
   **Lookup-result links (decision F1).** A lookup made with the subject's identifier links the
   resource only when the completed result in the same trace returns that resource ID (for
   example `evidence_01` trace `tr-4708586`: case 17's email lookup returns `account_4092`). A
   resource ID that appears only in the request is not a link. These links keep the type
   **lookup** (not proven) so their provenance stays visible. Like every link they are subject to
   the per-request assignment in section 6.2: the `evidence_67` lookup of `account_6523` (10 Mar)
   falls in case 46's window, not case 17's. 24 expected sample steps in 6 samples depend on
   such links. Some people filed more than one request (user_07: cases 07
   and 61; user_17: cases 17 and 46; user_18: cases 33 and 52); the graph is per person and
   resources are then assigned to that person's requests (section 6).
5. **Cutoff, applied everywhere as a hard rule.** For a case, only information available at its
   `audit_at` counts: evidence files with manifest `captured_at <= audit_at`, record-level times
   `<= audit_at`, activity and audit-log rows with `event_time <= audit_at`. This applies to
   identity links, linking candidates and eliminations, not just to citations. Per resource and
   per physical copy, the latest eligible observation wins. A clean primary never hides a dirty
   replica (samples 05, 16). Example: `evidence_02` (captured Sept 17) cannot be used for a
   Sept 15 audit, not even for its older user_26 to account_5684 link.
6. **Policy.** Map each record to a `deletion_rules.csv` category by its content and collection
   context, not its service (sample 12: an analytics contact lookup is DELETE, an analytics
   profile is ANONYMIZE).
7. **Steps.**
   - Violations become findings: remaining record, exposed PII, active flow, restore risk.
   - `INCOMPLETE`: findings, remediation, then verification of every relevant resource,
     including ones already compliant (samples 06, 11). Remediation and verification cite nothing.
   - `COMPLETE`: verification steps only, each citing evidence chosen by the citation rule below.
8. **Citation rule.** Findings cite the eligible observation proving the violation. For
   compliant states, candidate files are those whose eligible observation proves the state,
   including absence observations, minus observations contradicted by a later eligible
   observation of the same copy. Preference order (fits all 75 sample citations; only 10 of them
   discriminate, so it is reported as sample-derived):
   1. a file that names the resource (sample 21 cites the 404 entry in `evidence_92`);
   2. the file that holds the case's other cited observations (sample 31 cites `evidence_51`);
   3. a file whose scope covers primary and replicas (sample 04 cites `evidence_54`, not the
      newer primary-only `evidence_90`);
   4. latest capture time, then `file_id`.
9. **Ordering and serialization.** Phase, then the operation order of the problem-statement
   table, then `system_id`, then `resource_id`. All 193 sample steps follow this. Number steps
   from 1. Rows are ordered by the numeric part of `case_id` (`case_33` ... `case_550`, the same
   order as the input), not as text. Deterministic JSON. The README notes why the strict sort is
   used instead of AGENTS.md's "downstream copies first" wording (samples 16 and 24).

## 6. Resource linking: proven first, inferred second

Problem: some required resources have no written link to their subject once processed.
Deleted accounts survive only in rows such as "Deletion worker submitted primary profile
account_4857" (sample 31 needs `account_4857` for `user_541`). Already-anonymized analytics
records (samples 04, 21, 25 to 28) are linked only by `ANONYMIZE_RECORD` activity rows.

Every link is tagged **proven** (written evidence ties it to the subject) or **inferred**.

1. **Proven links** from eligible evidence and from the activity `details` text (section 5.2),
   plus lookup-result links (section 5.4), never request-only lookup keys.
2. **Per person, then per request (decision F2, option B).** Links attach to a person
   (`user_id`) and keep all eligible history. Request windows (`requested_at` to `audit_at`) are
   used only to decide which of a person's several requests a link belongs to; they are not a
   general lower cutoff. Measured over all 550 requests: 544 people have one request and only 3
   of their links predate the request (all case 34, which needs them); the out-of-window links of
   multi-request people are exactly the ones the samples leave out (`object_19` for case 07,
   `account_6523` and `tombstone_17` for case 17, `export_1260` for case 33). A resource may be
   relevant to more than one of the same person's requests.
3. **Inferred links by assignment, solved per cutoff.** For each distinct `audit_at`, build an
   assignment problem using only information available at that cutoff (candidates, other cases'
   proven links and eliminations), solve it, and keep the answers for the cases with that
   cutoff. Later evidence therefore can never change an earlier case's answer. Solved separately
   per resource type (accounts, analytics, and any other type with unlinked follow-ups).
   - Hard rules: candidate events must be eligible at the case's cutoff; a resource proven to
     belong to someone else is excluded.
   - Soft cost signals: the deletion-worker event's time relative to the case's `reported_at`
     (sample account deletes fall between 1.25 h before and 0.16 h after; follow-ups have fixed
     offsets before `reported_at` for successes and 0.6 to 1.9 h after for failures); the
     event's `reported_result` matching the case's `reported_status` (COMPLETE/SUCCESS,
     PARTIALLY_COMPLETE/PARTIAL, NOT_STARTED/FAILED; true in 28 of 30 samples); consistency with
     the case's proven follow-up resources.
   - **No-match option:** every case can also take "no inferred link" at a threshold cost, so
     weak candidates are never forced. Some requests have no matching delete event at all
     (for example cases 46, 61, 33, 52), and status counts do not pair up exactly.
   - Ties are broken by a stable key (`case_id`, then `activity_id`), so shuffled input cannot
     change results. Near-ties are reported as ambiguous.
   - The report states how many assignments exist only because of the one-to-one constraint.
   - A case with no proven or inferred link for a required resource emits no step for it, and
     the trace records the gap.
4. Solver: `scipy.optimize.linear_sum_assignment` (new dependency, to be confirmed with the
   user at milestone 4), because greedy matching swapped neighbouring jobs in the prototype.
5. Tests: adding evidence or activity dated after a cutoff never changes that cutoff's answers;
   shuffled input gives identical links.

Prototype evidence (rough, not the final pipeline): reviewer prototype with time window plus
status and elimination resolved 243 requests, 17/32 sample accounts correct and 0 wrong;
adding crude explicit links gave 21/32 with 1 wrong (sample 17, a decoy that rule 5.4 removes).
The sample 25/26 and 27/28 analytics pairs are near-simultaneous and remain the hardest.
Before freezing these rules, the ledger must explain sample 17's links (likely related to
user_17's earlier request, case 46).

## 7. Run trace and visual review page

Purpose: let the user independently review every case and verify how the pipeline reached its
answer, including the output of each deterministic function and each LLM call.

How it works:

- **Trace recording.** Each pipeline stage appends one event to the case's trace: stage name,
  order, start/end time, inputs (summarized), outputs, and the evidence it relied on
  (`file_id` / `activity_id` / `audit_id` plus the exact record or line). LLM calls add model,
  prompt, raw response, parsed result, tokens, latency, cache hit/miss, every attempt's start and
  end time, and the decision used if it differs from the raw answer. Tracing only observes; it
  never changes the answer. Traces are written to `output/trace.json`.
- **Page generator** (`code/review.py`): turns the trace, the dataset and (for samples) the
  expected answers into one self-contained `output/review.html`. Plain HTML, CSS and vanilla
  JavaScript, no framework and no server. The 11 screenshots and all needed evidence are embedded
  (each evidence file stored once and referenced by `file_id`), so the file works on its own.

What the page shows:

- **Case list:** filter by status, by sample vs evaluation, by sample match/mismatch, by
  "uses an inferred link", and by "LLM disagreed with rules"; search by case or email.
- **Flow chart per case:** the stages as simple ordered boxes in run order: Intent (LLM + rules)
  → Identity graph → Evidence discovery → Cutoff filter → Policy mapping → Resource linking →
  Step builder → Citation choice → Ordering → Final output. Each box shows a one-line result and
  a colour for how it was decided (deterministic, LLM, inferred link). Clicking a box opens its
  inputs, outputs and timing.
- **Evidence panel:** for every claim, the exact evidence that supports it (JSON object, log
  lines, SQL row, PDF text, screenshot, or the absence observation and its scope), its capture
  time, and whether it was eligible at `audit_at`. Excluded evidence is listed with the reason.
- **Identity panel:** current and historical identifiers with the `audit_id` rows behind them,
  and every resource link marked proven or inferred with its signals (and ambiguity, if any).
- **Output panel:** the produced `steps_json` as a table; for samples, side by side with the
  expected answer and differences highlighted.
- **LLM panel:** every call for the case, plus a run-level timeline of the parallel batch.
- **Run summary page:** totals and the sample analytics from section 8.

The page contains dataset content, so it stays in `output/` and is not packaged into `code.zip`;
the generator script ships so anyone can rebuild it.

## 8. Validation

Sample-first gate: each gate reports sample analytics only. `output.csv` for the 518 evaluation
requests is produced only after the user has reviewed the sample analytics and approved the full
run (the stages still compute all 550 requests internally, as section 5 explains).

Sample analytics reported at each gate (in chat and on the review page summary):

- Status accuracy, with a confusion table (COMPLETE / INCOMPLETE / NOT_APPLICABLE).
- Step-level, field-level (system, resource, operation, state, evidence) and sequence accuracy.
- The same metrics split into steps backed by proven links and steps relying on inferred links.
- Per-stage breakdown: identity links found, resources discovered vs expected, links by method
  and their accuracy, ambiguous and unmatched links, cutoff exclusions, citation accuracy.
- Every mismatching case with a readable diff (expected vs produced steps) and a likely cause.
- LLM calls, attempts, tokens (including retries), cache hits, failures and wall-clock time of
  the parallel batch.

Checks:

- Assertion ledger: explain each of the 193 sample steps as subject, resource, observation,
  rule, operation, citation, link type. Unexplained steps stay listed, never patched with
  overrides. Sample 17's links must be explained before linking rules are frozen.
- Sample scoring script (`code/evaluation/main.py`): status, step-level, field-level and
  sequence accuracy, per-case diff.
- Custom cases (at least 3 required; plan for 8):
  1. Historical email/hash plus a similar-email decoy.
  2. Primary absent, replica present, worker reports success: still incomplete.
  3. Later contradictory evidence and a later-captured bundle with older observations.
  4. Clean versus exposed tombstone.
  5. Negated deletion and non-English non-deletion request: `NOT_APPLICABLE` with `[]`.
  6. Active flow plus retained security plus already-absent account: full verification order.
  7. Lookup-key-only decoy (a 404 lookup with the subject's email) is not linked.
  8. Same person with two requests at different times: each request gets the right resources.
- Intent: the obvious non-deletion requests in the evaluation set and the `CLASSIFY_REQUEST`
  rows are used offline to check the classifier; any fix changes the prompt or the rules.
- Robustness: shuffle input order and confirm identical output; run the LLM batch with a stub
  client that returns fixed answers after random delays, at several pool sizes, and confirm
  identical output.
- Final checks: 518 rows in numeric case order, valid JSON, allowed operation/state pairs only,
  no citation or link from after `audit_at`, empty citations on remediation and incomplete-case
  verification.

## 9. Deliverables

- `output.csv` at the repository root.
- `code/evaluation/evaluation_report.md`: metrics (proven vs inferred split), custom cases,
  failures and fixes.
- `code/evaluation/usage_report.md`: describes the final full-dataset run, which is run cold
  (`--refresh-llm`) so calls and tokens are real (decided with the user). Provider (OpenAI via
  SIWC), model, unique requests, call attempts including retries, input and output tokens, total
  and average per evaluation request (denominator: the 518 evaluation requests), wall-clock time.
  Cost: marginal cost is covered by the ChatGPT subscription; also report an estimated
  pay-as-you-go API equivalent, in total and per evaluation request.
- `code/cache/llm_cache.json` (shipped, no secrets) and `code/cache/reviewed_facts.md`.
- `README.md`: setup, sign-in, run commands, worker-pool setting, how to open the review page,
  ordering note, packaging.
- `output/review.html` and `output/trace.json`: local review artifacts (not packaged).
- `code.zip` built from `README.md` and `code/` only, then checked: no dataset, no credentials,
  no `__pycache__` or virtual environments, no nested archives.

## 10. Decisions taken

| Topic | Decision |
|---|---|
| Resource linking | Proven links first; inferred links by assignment solved per cutoff, per person then per request, with a no-match option; tagged and reported |
| Absence | Recorded as observations with scope; JSON bundles cover only their subjects, SQL only declared tables (F3) |
| Lookup links | Count only when the result in the same trace returns the resource; typed `lookup` (F1) |
| Request windows | Only to split a person's several requests, not a general lower cutoff (F2 option B) |
| Citation order | Names resource, then case's other cited file, then replica coverage, then latest capture |
| Cutoff | Hard rule on every input: files, record times, activity, audit log, linking, eliminations |
| Screenshots | Vision model extraction plus manual verification, facts in Markdown bound to image hash |
| Intent | LLM on unique texts plus frozen keyword cross-check; disagreements fixed in prompt or rules, no stored verdicts |
| Milestone order | LLM work (sign-in, intent, screenshots) is milestone 3, before inferred linking (milestone 4) |
| Remediation order | Strict sort (operation, system, resource), as all samples show |
| Row order | Numeric `case_id` order, same as the input |
| LLM provider | OpenAI Responses API via Sign in with ChatGPT, model `gpt-6.1-sol` |
| Credentials | Stored in the user profile folder by the sign-in helper; pipeline reads an env variable |
| LLM concurrency | Bounded parallel worker pool with timeouts, retry and backoff; output independent of order; no output on unresolved calls |
| Final run | Cold LLM run with reconciliation against reviewed results |
| Testing order | Samples-only reporting; full output only after user approval |
| Review | Per-case trace plus a self-contained HTML flow-chart page for independent verification |
| Build process | Sonnet (`claude-sonnet-5-5`) implements; Astra (`gpt-6-astra`) reviews accumulated work in one clean pass per batch; Opus orchestrates and commits, through T3 (section 12) |

## 11. Trade-offs and risks

- Inferred links can be wrong and may look like a shortcut; they are tagged, measured separately
  and documented openly. Some (for example `account_004`) are unrecoverable.
- The strict cutoff loses security audit record steps whose only link appears after `audit_at`
  (7 sample steps); accepted by the user to honour the contract.
- The citation preference for absence is learned from 32 samples and could differ on hidden cases.
- Manual screenshot review costs human time; reviewed facts are kept in Markdown so the run stays
  reproducible.
- SIWC needs an interactive browser sign-in; a fresh machine without a token can only do
  warm-cache runs with the shipped cache.
- The cold final run uses subscription quota (about 400 calls); parallel calls may hit rate
  limits, handled by backoff.
- `scipy` would be a new dependency (confirmed at milestone 4).
- Tuning to 32/32 risks overfitting; every rule must be backed by evidence and custom cases.
- The review page and trace add code to maintain and make `output/` larger; in return every
  answer can be checked by a person without reading the code.

## 12. Build process and working agreement

Roles (orchestrated through the T3 MCP `delegate_task` tool):

- **Orchestrator** (current T3 session the user talks to): splits each milestone into stages,
  briefs the coder and reviewer, checks results, runs the sample analytics, reports to the user.
- **Coder:** Claude `claude-sonnet-5-5`. Implements each batch and review fixes in the shared checkout.
- **Reviewer:** Codex `gpt-6-astra`. Reviews read-only and reports prioritized findings.
- **Orchestrator:** Claude `claude-opus-5-5`. Briefs, checks results, commits finished work to branch `ghost-data-agent`.

User decision on 2026-10-25 (Claude limits restored): Sonnet implements; reviews then moved to
Astra the same day. Reviews do not cover every Sonnet turn; several implementation turns build up and are then reviewed
in one clean pass. This replaces the Sol coder / Astra reviewer arrangement below. Sol
(`gpt-6.1-sol`) remains the pipeline's own LLM only.

User decision on 2026-10-25: use Sol for implementation and Astra for independent review
from this point onward. This replaces the original Opus coder and temporary Astra coder
fallback. Already-running Astra reviews continue. All scope guards, review gates and user
decision points remain in force.

User decision on 2026-10-25: combine related implementation steps to avoid review-driven
over-engineering. Use focused tests during implementation and one independent review of the
completed batch, rather than repeated reviews for each small stage. The remaining milestone 3
work is one batch: auth recovery, transport integration, intent, screenshot facts and tracing.

Loop per batch: Sonnet implements and tests; Astra reviews the completed work; Sonnet fixes blocking
non-architectural findings. Any follow-up review is targeted to those fixes, not a new broad
audit or another round of hypothetical edge cases. Findings that are architectural (they
would change this plan's design, add a dependency, change a decision in section 10, or trade
accuracy) are brought to the user before anything is changed. Proceed batch by batch until
the work is done. Low findings that do not affect current requirements are documented.

Scope guard (user instruction: prevent over-engineering, stay within scope):

- In scope: the challenge contract (problem_statement.md, AGENTS.md section 6), the participant
  dataset, and the features the user asked for (review page, parallel LLM calls, sample-first
  analytics). Nothing else is built.
- The coder builds the simplest version that is correct on the dataset and the contract: no
  speculative options, layers, configuration or generality for inputs that do not occur.
- Review severity: High/Medium only when it changes an output for the dataset, breaks the
  contract, breaks a user-requested feature, or leaks secrets or future evidence. Problems that
  need invented inputs outside the dataset are Low and are noted, not fixed (the keyword intent
  rounds showed the cost of chasing them).
- Each review also checks for over-engineering: code, options or tests that no current
  requirement needs are findings to remove.

The user stays in the loop:

- Before each milestone: a short note of what is about to be built and which files it touches.
- After each milestone: what was built, how to run it, review rounds and fixes, sample analytics
  (section 8) and the updated review page, open problems, and what comes next. Work then
  continues automatically.
- Work pauses for the user only for: architectural findings, new dependencies (`scipy`), the
  SIWC browser sign-in, screenshot and intent review, and approval of the full 518-case run.
- Failures and mismatches are reported as they are, with numbers; nothing is claimed as passing
  unless it was run.

## 13. Milestones

Each milestone is built in coherent batches through the loop in section 12 and ends with a report.

1. Assertion ledger, evidence parsers (including absence observations and parent context),
   activity `details` extraction, and trace recording. No LLM needed. Report: parser coverage
   per file type and how many of the 193 sample steps the ledger explains, including sample 17.
2. Identity graph, cutoff everywhere, policy, step builder, citation rule, keyword intent, plus
   the first version of the review page (`code/review.py`) and the sample scoring script.
   Report: first sample analytics, reviewable in `output/review.html`.
3. SIWC sign-in helper, LLM client with parallel calls, intent classification (LLM primary,
   frozen keyword rules as cross-check) and screenshot extraction. Moved before inferred linking
   by user decision. Pause: user signs in, reviews intent disagreements and screenshot facts.
4. Inferred linking by per-cutoff assignment, tuned on samples. Report: link accuracy by method
   and resource type. Pause: confirm the `scipy` dependency.
5. End-to-end run on the samples. Report: full sample analytics. Pause: user approves the full run.
6. Full 518-row cold run with reconciliation and all final checks. Report: run summary and
   validation results.
7. Evaluation and usage reports, README, `code.zip`. Report: final review before submission.
