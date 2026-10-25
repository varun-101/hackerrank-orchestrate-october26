# Evaluation Report

Ghost Data Still Exists: deletion-audit agent. This report covers how accurate the agent is on
the 32 public samples, the eight custom cases we wrote, the failures we found while building it,
and what we cannot know about the 518 evaluation requests. Every number below was produced by
the commands in the last section.

## 1. Summary

| Question | Answer |
|---|---|
| Status accuracy on the 32 public samples | 32 of 32 (100%) |
| Step-level accuracy on the samples (multiset) | 96.9% (187 of 193 expected steps matched; precision 98.4%, recall 96.9%) |
| Step-level accuracy on the samples (positional) | 94.3% |
| Field-level accuracy on the samples | 96.9% to 98.4% per field (section 3.3) |
| Sequence accuracy on the samples | 81.2% (26 of 32 cases exact) |
| Custom cases we wrote | 8 cases, 13 requests; all 13 match the hand-written answers (section 5) |
| Accuracy on the 518 evaluation requests | Not measurable: there are no answers to compare with. We do not claim a number. |

The statuses are robust on every case we can check. The remaining errors on the samples are all
resource IDs: six expected steps cannot be reproduced, and three of those are replaced by a wrong
guess (section 3.5).

How the agent works, in one paragraph: a deterministic Python pipeline reads `dataset/`, resolves
the request email to a person and all their old and new identifiers, links evidence and activity
rows to that person, works out each resource's state at `audit_at`, applies `deletion_rules.csv`,
and writes the steps. A language model is used in two places only: deciding whether the request
text asks for deletion (cross-checked against keyword rules), and reading the 11 screenshots
(checked by hand). See `README.md` and `code/PLAN.md`.

## 2. How the metrics are defined

These are the definitions in the docstring of `code/evaluation/main.py`, which is the code that
produced every sample number here. A case missing from the predictions counts as status MISSING
with no steps and earns no credit in any metric.

- **Status accuracy:** predicted `deletion_status` equals the expected one, divided by the number
  of expected cases. The confusion table counts (expected, predicted) pairs.
- **Step key:** a step's (phase, system_id, resource_id, operation, state, evidence_id), that is
  every field except `step_no`, which only encodes position.
- **Step-level accuracy, multiset:** per case, matched steps are the multiset intersection of
  expected and predicted step keys. Over all cases, precision = matched / predicted steps,
  recall = matched / expected steps, accuracy = matched / sum over cases of
  max(expected steps, predicted steps). Order does not matter.
- **Step-level accuracy, positional:** predicted step i equals expected step i in all 7 fields
  (including `step_no`), divided by the same denominator. Order matters.
- **Field-level accuracy, per field:** each expected step is paired with the first unpaired
  predicted step with the same (phase, operation, system_id, resource_id); leftovers are paired
  by position. A field matches when the pair has equal values for it. Matches are summed and
  divided by the same denominator. Citations swapped between two records count as two
  `evidence_id` misses, while a correct plan in a different order keeps 100%.
- **Sequence accuracy:** predicted cases whose parsed `steps_json` array equals the expected array
  exactly (same steps, same order, same `step_no`), divided by the number of expected cases.
  `NOT_APPLICABLE` cases match when both arrays are `[]`.

## 3. Results on the 32 public samples

The samples are the only cases with answers (`dataset/sample_output.csv`). That file is read by
the scoring code only after the pipeline has finished; the pipeline never reads it.

### 3.1 Case-level status accuracy

32 of 32 = 100%. Rows are the expected status, columns the predicted one.

| Expected \ Predicted | COMPLETE | INCOMPLETE | NOT_APPLICABLE | MISSING |
|---|---|---|---|---|
| COMPLETE | 10 | 0 | 0 | 0 |
| INCOMPLETE | 0 | 22 | 0 | 0 |
| NOT_APPLICABLE | 0 | 0 | 0 | 0 |

The samples contain no `NOT_APPLICABLE` request, so this table does not test that status. Two
other checks do: the 10 non-deletion requests of the evaluation set (case_533 to case_542) are
all answered `NOT_APPLICABLE`, and the custom cases include four more (section 5).

### 3.2 Step-level accuracy

| Metric | Value |
|---|---|
| Expected steps / predicted steps / matched | 193 / 190 / 187 |
| Multiset accuracy | 96.9% |
| Multiset precision | 98.4% |
| Multiset recall | 96.9% |
| Positional accuracy | 94.3% |

### 3.3 Field-level accuracy

| Field | Accuracy |
|---|---|
| phase | 98.4% |
| system_id | 98.4% |
| resource_id | 96.9% |
| operation | 98.4% |
| state | 98.4% |
| evidence_id | 98.4% |

`resource_id` is lowest because the three wrong guesses and the three unrecoverable resources hit
it; the other fields lose a step only when the resource is missing or wrong. Citations: 73 of the
75 expected cited steps have the right cited file (97.3%).

### 3.4 Sequence accuracy

81.2%: 26 of 32 cases exactly equal the expected array, in order. The six that do not are listed
in section 3.5; each differs from the expected answer by exactly one resource.

### 3.5 The six cases that are not exact

| Case | What is wrong | Cause | Recoverable? |
|---|---|---|---|
| case_04 | Missing `VERIFY_RECORD_ABSENT` for `account_004` | `account_004` appears in no participant file at all (only in `sample_output.csv`) | No |
| case_18 | Missing `VERIFY_RECORD_PRESENT` for `audit_record_6645` | The ID appears in no participant file | No |
| case_20 | Missing `VERIFY_RECORD_PRESENT` for `audit_record_5569` | The ID appears in no participant file | No |
| case_23 | `account_7015` instead of `account_410` | Three equally good failed-delete rows; no file mentions any of them | No |
| case_31 | `account_1810` instead of `account_4857` | A batch of requests was reported at the same moment, with five identical delete rows. Only `evidence_03` separates them, and it is dated 2026-10-01, after the case's `audit_at` of 2026-09-15 | Not without using later evidence |
| case_32 | `account_2381` instead of `account_8858` | Same batch and same reason as case_31 | Not without using later evidence |

All three wrong picks (cases 23, 31, 32) are deliberate best guesses made from the timing of the
deletion worker's rows, and are marked `ambiguous` in the trace. A wrong account pick swaps one
`VERIFY_RECORD_ABSENT` step for another; it never changed a status.

### 3.6 Proven, lookup and inferred links

Every resource in an answer is tied to the person in one of three ways, and the scoring code
splits the steps by that:

- **proven:** the evidence or an activity row names the person's identifier next to the resource
  (an email, hash, phone, `user_id`, or a subject reference).
- **lookup:** a log trace looked the resource up with the person's identifier and its completed
  result returned that resource ID. A resource named only on the request line does not count.
- **inferred:** nothing written ties the resource to the person (a deleted account that survives
  only in a row "Deletion worker submitted primary profile account_4857", or an
  already-anonymized analytics record). The agent assigns such rows to requests by timing and by
  the deletion worker's reported result, one row per request, solved separately for each
  `audit_at` so later evidence cannot change an earlier answer. It may also decide "no link".

| Link type | Cases with steps | Expected steps | Predicted steps | Matched | Step accuracy | Sequence accuracy |
|---|---|---|---|---|---|---|
| proven | 26 | 143 | 143 | 143 | 100% | 100% |
| lookup | 7 | 23 | 23 | 23 | 100% | 100% |
| inferred | 20 | 21 | 24 | 21 | 87.5% | 85% |
| no link at all | 6 | 6 | 0 | 0 | 0% | 0% |

A case can appear in several rows because it can have steps of several link types. Link accuracy
(resources linked, and how many of them belong in the sample answer):

| Method | Linked | Correct | By resource type |
|---|---|---|---|
| proven | 69 | 69 | audit_record 17/17, search 11/11, support 10/10, data_flow 9/9, account 8/8, analytics 5/5, backup 5/5, export 2/2, tombstone 2/2 |
| lookup | 15 | 15 | account 6/6, audit_record 5/5, analytics 1/1, backup 1/1, search 1/1, support 1/1 |
| inferred | 24 | 21 | account 14/17, analytics 7/7 |

Of the 24 inferred links, 11 were ambiguous (another choice was almost as good) and 8 of those 11
were right; the other 13 were unambiguous and all 13 were right. Four links exist only because
of the one-to-one rule (3 of them right). In total 105 of the 111 resources in the sample answers
were found.

## 4. The honest ceiling

Some expected steps cannot be derived from participant data, and we have not tried to fake them:

1. **Resources that appear in no participant file:** `account_004`, `audit_record_6645`,
   `audit_record_5569` (three steps). Only the answer key names them.
2. **Tied inferred accounts:** cases 23, 31 and 32. The candidate rows are identical in every
   field the pipeline may use, and the only file that separates two of them is dated after
   `audit_at`. Using it would break the rule that later evidence must not change an answer.
3. **Inferred links in general:** they can be wrong. On the samples 8 of 11 tied links were right
   and 13 of 13 untied links were right. This is a small sample, so treat the rates as rough.

Why the pipeline still guesses in the tied cases: the guess keeps the status and every other
step correct, and each one is labelled `inferred` and `ambiguous` in `output/trace.json` and on
the review page so a person can see it. The alternative is to drop every tied link. On the
current samples that would remove the 3 wrong steps but also the 8 correct tied links (and the
steps that depend on them), so we kept the guesses. This is a trade-off, not a proof; the user
chose it.

## 5. Custom evaluation cases

The challenge asks for at least three cases written by the team. We wrote eight. Each is a small,
fully fictional dataset in `code/evaluation/custom_cases/<case>/` (same file layout as
`dataset/`, with invented people, emails and IDs that do not occur in `dataset/`). Each folder
also has `expected.json`: the answer written by hand from the rules in `AGENTS.md` before the
pipeline was run on the case.

How they run (`code/evaluation/custom_cases.py`):

- For each case the runner copies the folder into a temporary directory, adds the real
  `deletion_rules.csv`, points the pipeline at that directory, runs the real pipeline, and
  compares the result with `expected.json` using the same `score()` as section 2.
- Intent is decided by the keyword rules. There is no model call, no cache and no network. The
  model-based intent check and the screenshot reading are therefore not exercised by these
  cases; the screenshots and intent texts of the real dataset are covered by the samples.
- Pointing the pipeline at another folder works by replacing the `DATASET` path in the four
  modules that read files (`evidence`, `identity`, `activity`, `policy`) for the duration of a
  run and clearing the identity caches before and after. We chose this over adding a parameter
  to the pipeline because each of those modules holds its own copy of the path, so one
  parameter would not reach them all, and the pipeline code stays untouched.

| # | Case folder | Requests | Tests | Status expected / actual | Result |
|---|---|---|---|---|---|
| 1 | `01_historical_email_decoy` | 1 | Request uses an old email; old email and old email hash still expose a search document and an analytics profile; a similar-looking email of another person is ignored | INCOMPLETE / INCOMPLETE | PASS |
| 2 | `02_replica_drift` | 1 | Primary has no profile, read replica still has it, worker reports SUCCESS | INCOMPLETE / INCOMPLETE | PASS |
| 3 | `03_later_evidence_ignored` | 1 | Everything is deleted at `audit_at`; a snapshot captured 6 days later shows the data back | COMPLETE / COMPLETE | PASS |
| 4 | `04_tombstone_clean_vs_exposed` | 2 | Clean tombstone versus a tombstone still holding name, email, phone and hash | COMPLETE and INCOMPLETE / same | PASS |
| 5 | `05_non_deletion_requests` | 4 | Negated deletion, data copy request, name correction, email correction (English, Spanish, Japanese, French) | NOT_APPLICABLE x4 / same | PASS |
| 6 | `06_full_plan_order` | 1 | Every kind of leftover at once; full phase and operation ordering | INCOMPLETE / INCOMPLETE | PASS |
| 7 | `07_request_only_lookup_decoy` | 1 | A resource named only on a request line with the person's email is not linked | COMPLETE / COMPLETE | PASS |
| 8 | `08_record_time_rule` | 2 | Log captured after `audit_at` holding lines timed before it: links ownership, is never cited | INCOMPLETE and COMPLETE / same | PASS |

Metrics over all eight cases together (13 requests; actual equals expected step for step):

| Metric | Value |
|---|---|
| Status accuracy | 13 of 13 (100%) |
| Confusion | COMPLETE 4/4, INCOMPLETE 5/5, NOT_APPLICABLE 4/4, no off-diagonal |
| Steps (multiset) | 51 expected, 51 predicted, 51 matched: accuracy, precision, recall all 100% |
| Steps (positional) | 100% |
| Fields | phase, system_id, resource_id, operation, state, evidence_id all 100% |
| Sequence accuracy | 100% (13 of 13) |

Each case's request rows, the data it contains and the full expected answer are in Appendix A.

### Do the cases actually discriminate?

All eight passed the first time they were run. To make sure that is not because the tests are
too easy, we edited copies of the case folders (in a scratch directory, not in the repository)
so that the thing under test is broken, and ran the pipeline on each copy. Every edit changed the
answer, so each case can fail:

| Edit | Result |
|---|---|
| Case 7: put the resource ID on the completed result line too | Answer changed (decoy linked) |
| Case 3: date the later export before `audit_at` | Answer changed (INCOMPLETE) |
| Case 8: move the late line from the 17th to before `audit_at` | Answer changed (extra check step) |
| Case 8: date the late log before `audit_at` | Answer changed (late file now cited) |
| Case 1: date the email change after `audit_at` | Answer changed (old email no longer resolves) |
| Case 2: remove the replica row | Answer changed (COMPLETE) |
| Case 4: remove name, email, phone and hash from the second tombstone | Answer changed (COMPLETE) |

These edits were a one-off check and are not stored as tests.

### Limits of the custom cases

- They test the deterministic core and the keyword intent rules. Intent for new texts in other
  languages (for example German) is not supported by the keyword rules; such a text stops the
  run as `UNSURE` instead of being guessed. No such text exists in the dataset.
- They use the evidence shapes of the real dataset (JSON exports and bundles, key-value logs, SQL
  snapshots). PDFs and screenshots are not covered by custom cases.
- One behavior found while building case 8 and left as it is: a record that policy says must be
  deleted, linked only through a line in a file captured after `audit_at`, produces no step in
  either status, because no eligible file shows its state. Retained records (audit records) do
  get a check. This is the documented trade-off of the record-time rule.

## 6. Failures found and improvements made

In the order they happened. "Samples" means the 32 public samples; steps are out of 193 expected.
Numbers come from `log.txt` and `code/PLAN.md`.

| # | Stage | Failure or finding | Improvement | Effect |
|---|---|---|---|---|
| 1 | Evidence parsing and identity (milestone 1) | An assertion ledger explained each of the 193 sample steps: 135 proven, 24 lookup-only, 26 needing inference, 8 unexplained. It showed three traps: a lookup made with the person's email says nothing unless its result returns the resource; people with two requests (users 07, 17, 18) need their records split by request; a JSON bundle's empty list proves absence only for the people it covers | Decisions F1 (lookup counts only when the result returns the resource, typed `lookup`), F2 (request windows only split a person's several requests), F3 (absence scoped to the systems and tables a snapshot declares) | Prevented wrong links before any scoring existed. Review rounds also fixed the identity cutoff, the bundle scope and the citations |
| 2 | Intent (keyword rules) | Three review rounds with invented phrasings kept finding new edge cases (negations, "marketing only", conditionals, questions) with diminishing returns | Rules made conservative (unclear text is `UNSURE` and stops the run), then frozen; the language model became the primary judge with the rules as a cross-check | Dataset split unchanged by every round: 540 deletion, 10 not applicable, 0 unsure |
| 3 | First full sample gate (milestone 2) | Samples 01, 02, 04 had empty steps; 34 expected steps had no link | Baseline recorded: 32/32 status, 159/193 steps, precision 100%, 10 of 32 exact | Starting point |
| 4 | Model-based intent and screenshots (milestone 3) | Screenshots name some resources by display name (`user_11`, `CS-5034`, a PDF title) instead of catalog ID; one image is superseded by a later log; case_55's backup image has no restore state | Alias canonicalization using catalog rows, a superseded-image skip, and an image backup with unknown restore state recorded as a gap instead of guessed. All 11 screenshot readings were checked by hand against the images (no transcription errors) and stored with the image hash | The model and the keyword rules agreed on all 398 distinct request texts. Sample scores unchanged (159/193) |
| 5 | Inferred links (milestone 4) | 34 expected steps unlinked, mostly deleted accounts and already-anonymized analytics records that no file ties to a person | Assignment of activity rows to requests per `audit_at` (scipy `linear_sum_assignment`) with a "no match" option and tie-breaking by `case_id` then `activity_id` | Steps 159 to 182 of 193; exact cases 10 to 22; status still 32/32; precision fell from 100% to 98.4% because of three wrong tied picks (cases 23, 31, 32) |
| 6 | Cutoff rule | The strict cutoff dropped valid links: `evidence_06` (a log captured 2026-09-18) holds lines timed 2026-09-16 08:52 that tie `audit_record_4902` to a person, but sample 09 is audited at 2026-09-16 09:00 | Record-time rule: a record's own time decides ownership; the file capture time still decides state and what may be cited. A late file is never cited | Steps 182 to 187 of 193; exact cases 22 to 26; 120 of the 518 evaluation answers changed, none of them in status |
| 7 | Output validation | The validator rejected a `COMPLETE` row with no steps (case_55, whose only linked resource, a backup, has no usable state). The user said to ignore case_55 | The validator allows it and `--full` prints a warning | `output.csv` is written; case_55 stays a known weak case |
| 8 | Final run | A fresh run that re-asks the model (the user ran `--full --refresh-llm`) returned valid answers for all 409 requests, but 225 intent results had different explanation wording (same decision) and all 11 screenshot readings differed in grouping or wording from the approved facts. The publication gate stopped it, as designed | The submission uses the approved cache; a person must review any difference before it replaces approved facts | Final `python code/main.py --full`: 0 live model calls, 518 rows, validation passed: 354 INCOMPLETE, 154 COMPLETE, 10 NOT_APPLICABLE |

Two points the table does not show. First, the sample set was used to tune the rules in steps 4
to 6, so the sample scores are not an unbiased estimate of accuracy on hidden cases; every rule
was written as a general rule backed by evidence, not as a per-case override, and no sample
answer is read by the pipeline. Second, the custom cases (section 5) were written afterwards,
without tuning the pipeline to them.

## 7. Known limits on the 518 evaluation requests

There is no answer key for these requests, so none of the figures in section 3 can be computed for
them. What we can say from the run itself (`output/trace.json`, `output.csv`):

| Item | Count |
|---|---|
| Rows in `output.csv` | 518, sorted, validation passed |
| INCOMPLETE / COMPLETE / NOT_APPLICABLE | 354 / 154 / 10 |
| Requests that are deletion requests | 508 |
| Deletion answers using at least one inferred link | 316 of 508 |
| Deletion answers using at least one tied (ambiguous) inferred link | 143 of 508 |
| Account links: proven or lookup / inferred (of which tied) / none | 236 / 262 (110) / 10 |
| Analytics links: proven or lookup / inferred (of which tied) / none | 73 / 117 (53) / 318 |
| Cases with a gap recorded in the trace | 16 |
| Deletion cases with no `account_service` step | 14 (10 with no account link at all) |

What this means:

- **Status is the most reliable output.** On the samples no tied pick ever changed a status, and
  the record-time rule changed none of the 518 statuses.
- **Resource IDs of deleted accounts and anonymized analytics records are the weakest part.**
  More than a quarter of the evaluation answers (143 of 508) contain a tied pick. If hidden tied
  picks were right as often as the 11 tied sample picks (8 of 11), roughly 40 of those 143 answers
  would carry one wrong account or analytics step. That is an illustration from a very small
  sample, not a measurement.
- **case_55** is `COMPLETE` with an empty `steps_json`: its only linked resource is a backup
  whose restore state cannot be established, and no account could be linked. The user asked us to
  ignore this case; it is flagged here rather than hidden.
- The 10 `NOT_APPLICABLE` answers are all clear non-deletion requests (marketing opt-out,
  correction, access or copy requests, login help). The model and the rules agree on them.

## 8. How to reproduce every number

Run from the repository root. Python 3.11 or newer.

```bash
python -m pip install -r code/requirements.txt
```

| What | Command | Output |
|---|---|---|
| Sample scores (sections 2 to 4; reads the model answers from `code/cache/llm_cache.json`, makes no model call) | `python code/main.py --samples` | Console, `output/sample_scores.json`, `output/sample_predictions.csv`, `output/trace.json` |
| Re-score saved sample predictions only | `python code/evaluation/main.py --out output/rescored.json` | Console and `output/rescored.json` (the same metrics without the link analytics; without `--out` it would overwrite `output/sample_scores.json`) |
| Custom cases (section 5) | `python code/evaluation/custom_cases.py` | Console, `output/custom_scores.json`; exit code 1 if any case differs |
| All unit tests, including the custom cases | `python -m unittest discover -s code/tests` | Pass or fail per test |
| Final submission file (section 7) | `python code/main.py --full` | `output.csv`, `output/usage.json` |
| Evaluation-set counts in section 7 | Open `output/trace.json` after a run: each case's `linking` event holds `outputs.inference` with `status`, `ambiguous` and `one_to_one_only` per resource type | |

`output/` is generated and not packaged. Token use and cost of the final run are in
`evaluation/usage_report.md`.

## Appendix A. Custom cases: inputs and expected answers

The case folders hold the full files; this appendix repeats what each case contains so the report
stands on its own. The expected answers were written by hand from `AGENTS.md`, not copied from
pipeline output. `-` in the evidence column means an empty `evidence_id`.

### 01_historical_email_decoy

The request uses the person's OLD email. The old email and the old email hash (from user_audit_log.csv) still expose a search document and an analytics profile; a search document of a different person with a similar email must not be touched.

Requests:

| case_id | email | reported_status | audit_at | request_text |
|---|---|---|---|---|
| case_9101 | `inge.valdemarsen@nordpost.example` | COMPLETE | 2026-09-20T12:00:00Z | Please delete my account and all personal data associated with it. |

Evidence files (`evidence_files.csv`): `evidence_9101` captured 2026-09-19T12:00:00Z, `evidence_9102` captured 2026-09-19T11:00:00Z.

What the data says:

- User `user_9101` (current email `ingeborg.valdemarsen@nordpost.example`); audit log: email and email hash changed on 2026-06-02 from `inge.valdemarsen@nordpost.example` (and its hash).
- Another person, `user_9102`, has the similar email `inge.valdemarsen2@nordpost.example`.
- `evidence_9101` (JSON export): `search_91011` with the old email; `search_91012` with the other person's email and `customer_reference` `user_9102`; `analytics_91011` with only the old email hash; `audit_record_91011` for `user_9101`.
- `evidence_9102` (log): lookup of the old email returns `account_91011` with HTTP 404.

Expected for `case_9101`: **INCOMPLETE**

| step_no | phase | operation | system_id | resource_id | evidence_id |
|---|---|---|---|---|---|
| 1 | FINDING | REPORT_REMAINING_RECORD | search_service | search_91011 | evidence_9101 |
| 2 | FINDING | REPORT_EXPOSED_PII | analytics_platform | analytics_91011 | evidence_9101 |
| 3 | REMEDIATION | DELETE_RECORD | search_service | search_91011 | - |
| 4 | REMEDIATION | ANONYMIZE_RECORD | analytics_platform | analytics_91011 | - |
| 5 | VERIFICATION | VERIFY_RECORD_ABSENT | account_service | account_91011 | - |
| 6 | VERIFICATION | VERIFY_RECORD_ABSENT | search_service | search_91011 | - |
| 7 | VERIFICATION | VERIFY_RECORD_PRESENT | security_service | audit_record_91011 | - |
| 8 | VERIFICATION | VERIFY_RECORD_ANONYMIZED | analytics_platform | analytics_91011 | - |

### 02_replica_drift

The deletion worker reports SUCCESS and the primary table has no profile, but the read replica of the same snapshot still holds the full profile. The case is INCOMPLETE; the security audit record is retained by policy, not deleted.

Requests:

| case_id | email | reported_status | audit_at | request_text |
|---|---|---|---|---|
| case_9201 | `tomasz.wrobelski@polmail.example` | COMPLETE | 2026-09-22T10:00:00Z | I would like you to erase my account and the personal data connected to it. Please confirm once it is done. |

Evidence files (`evidence_files.csv`): `evidence_9201` captured 2026-09-22T09:00:00Z.

What the data says:

- `system_activity.csv`: `DELETE_RECORD` for `account_92011`, reported result SUCCESS.
- `evidence_9201` (SQL snapshot, read replica): table `customer_data_primary` holds only `audit_record_92011`; table `customer_data_read_replica` holds `account_92011` with name, email and phone, plus the audit record.

Expected for `case_9201`: **INCOMPLETE**

| step_no | phase | operation | system_id | resource_id | evidence_id |
|---|---|---|---|---|---|
| 1 | FINDING | REPORT_REMAINING_RECORD | account_service | account_92011 | evidence_9201 |
| 2 | REMEDIATION | DELETE_RECORD | account_service | account_92011 | - |
| 3 | VERIFICATION | VERIFY_RECORD_ABSENT | account_service | account_92011 | - |
| 4 | VERIFICATION | VERIFY_RECORD_PRESENT | security_service | audit_record_92011 | - |

### 03_later_evidence_ignored

Everything is gone at audit_at (2026-09-14). A snapshot captured and timed 6 days later shows the profile, the search document and a new export for the same person; that evidence is later than audit_at and must change nothing: the case stays COMPLETE, cites only the earlier log, and mentions neither the later records nor export_93012.

Requests:

| case_id | email | reported_status | audit_at | request_text |
|---|---|---|---|---|
| case_9301 | `beatriz.quintanilla@sudmail.example` | COMPLETE | 2026-09-14T12:00:00Z | Please delete my account and every copy of my personal data. |

Evidence files (`evidence_files.csv`): `evidence_9301` captured 2026-09-13T20:00:00Z, `evidence_9302` captured 2026-09-20T12:00:00Z.

What the data says:

- `evidence_9301` (log captured 2026-09-13, before `audit_at` 2026-09-14): `account_93011` lookup returns 404; `search_93011` query returns 0 rows; `audit_record_93011` read succeeds.
- `evidence_9302` (JSON export captured and timed 2026-09-20, after `audit_at`): `account_93011` and `search_93011` present with the person's email; new `export_93012` present.

Expected for `case_9301`: **COMPLETE**

| step_no | phase | operation | system_id | resource_id | evidence_id |
|---|---|---|---|---|---|
| 1 | VERIFICATION | VERIFY_RECORD_ABSENT | account_service | account_93011 | evidence_9301 |
| 2 | VERIFICATION | VERIFY_RECORD_ABSENT | search_service | search_93011 | evidence_9301 |
| 3 | VERIFICATION | VERIFY_RECORD_PRESENT | security_service | audit_record_93011 | evidence_9301 |

### 04_tombstone_clean_vs_exposed

Two people, same bundle. user_9401 has a clean tombstone (only the stable user reference left): COMPLETE, and the tombstone must be verified as redacted, not deleted. user_9402's tombstone still holds name, email, phone and email hash: INCOMPLETE, redact it (never delete it) and keep the audit record.

Requests:

| case_id | email | reported_status | audit_at | request_text |
|---|---|---|---|---|
| case_9401 | `rangi.tamatea@kiwimail.example` | COMPLETE | 2026-09-18T12:00:00Z | Please delete my account and personal data. |
| case_9402 | `wiremu.parata@kiwimail.example` | COMPLETE | 2026-09-18T12:00:00Z | Please delete my account and personal data. |

Evidence files (`evidence_files.csv`): `evidence_9401` captured 2026-09-18T11:00:00Z, `evidence_9402` captured 2026-09-18T10:00:00Z.

What the data says:

- `evidence_9401` (JSON bundle, 2026-09-18): `tombstone_94011` for `user_9401` with name, email, phone and hash all null; `tombstone_94021` for `user_9402` with all four set; one audit record each.
- `evidence_9402` (log): both people's account lookups return 404 (`account_94011`, `account_94021`).

Expected for `case_9401`: **COMPLETE**

| step_no | phase | operation | system_id | resource_id | evidence_id |
|---|---|---|---|---|---|
| 1 | VERIFICATION | VERIFY_RECORD_ABSENT | account_service | account_94011 | evidence_9402 |
| 2 | VERIFICATION | VERIFY_RECORD_PRESENT | security_service | audit_record_94011 | evidence_9401 |
| 3 | VERIFICATION | VERIFY_RECORD_REDACTED | identity_service | tombstone_94011 | evidence_9401 |

Expected for `case_9402`: **INCOMPLETE**

| step_no | phase | operation | system_id | resource_id | evidence_id |
|---|---|---|---|---|---|
| 1 | FINDING | REPORT_EXPOSED_PII | identity_service | tombstone_94021 | evidence_9401 |
| 2 | REMEDIATION | REDACT_RECORD | identity_service | tombstone_94021 | - |
| 3 | VERIFICATION | VERIFY_RECORD_ABSENT | account_service | account_94021 | - |
| 4 | VERIFICATION | VERIFY_RECORD_PRESENT | security_service | audit_record_94021 | - |
| 5 | VERIFICATION | VERIFY_RECORD_REDACTED | identity_service | tombstone_94021 | - |

### 05_non_deletion_requests

Four requests that are not deletion requests: a negated deletion plus a marketing opt-out (English), a data copy request (Spanish), a name correction (Japanese) and an email correction (French). Two of these people still have a search document in the evidence, which must not matter: each answer is NOT_APPLICABLE with an empty steps array. These use the keyword intent rules (no model call).

Requests:

| case_id | email | reported_status | audit_at | request_text |
|---|---|---|---|---|
| case_9501 | `ayesha.farooqi@lumenpost.example` | COMPLETE | 2026-09-19T12:00:00Z | I do not want my account deleted. Please only unsubscribe me from marketing emails. |
| case_9502 | `diego.salcedo@lumenpost.example` | PARTIALLY_COMPLETE | 2026-09-19T12:00:00Z | Quiero descargar una copia de mis datos personales. No elimine mi cuenta. |
| case_9503 | `hana.kobayashi@lumenpost.example` | NOT_STARTED | 2026-09-19T12:00:00Z | 登録されている氏名の綴りを訂正してください。アカウントは削除しないでください。 |
| case_9504 | `camille.rousseau@lumenpost.example` | COMPLETE | 2026-09-19T12:00:00Z | Ne supprimez pas mon compte. Je souhaite seulement corriger mon adresse e-mail. |

Evidence files (`evidence_files.csv`): `evidence_9501` captured 2026-09-19T11:00:00Z.

What the data says:

- `evidence_9501` (JSON export): `search_95011` for `user_9501` and `search_95021` for `user_9502` are still present.

Expected for `case_9501`: **NOT_APPLICABLE** (steps_json `[]`)

Expected for `case_9502`: **NOT_APPLICABLE** (steps_json `[]`)

Expected for `case_9503`: **NOT_APPLICABLE** (steps_json `[]`)

Expected for `case_9504`: **NOT_APPLICABLE** (steps_json `[]`)

### 06_full_plan_order

One person with every kind of leftover: an export and a search document to delete, a support ticket to redact (shared context is retained), an analytics profile to anonymize, an active connector, a backup that can still be restored, a retained security audit record, and an account that is already gone. Checks the full phase and operation order: findings, then disable flow and block restore before any record change, then deletes, redactions and anonymizations, then every verification in table order.

Requests:

| case_id | email | reported_status | audit_at | request_text |
|---|---|---|---|---|
| case_9601 | `odalys.ferreira@harbormail.example` | COMPLETE | 2026-09-22T12:00:00Z | Please delete my account and personal data, including exports, search entries and backups where it applies. |

Evidence files (`evidence_files.csv`): `evidence_9601` captured 2026-09-21T12:00:00Z, `evidence_9602` captured 2026-09-21T11:00:00Z.

What the data says:

- `evidence_9601` (JSON export, 2026-09-21): `export_96011` and `search_96011` hold the email; `support_96011` holds requester email and name; `analytics_96011` holds the email; `data_flow_96011` has `enabled` true; `backup_96011` has `restore_allowed` true; `audit_record_96011` has retention until 2034.
- `evidence_9602` (log): account lookup returns `account_96011` with HTTP 404.

Expected for `case_9601`: **INCOMPLETE**

| step_no | phase | operation | system_id | resource_id | evidence_id |
|---|---|---|---|---|---|
| 1 | FINDING | REPORT_REMAINING_RECORD | export_service | export_96011 | evidence_9601 |
| 2 | FINDING | REPORT_REMAINING_RECORD | search_service | search_96011 | evidence_9601 |
| 3 | FINDING | REPORT_EXPOSED_PII | analytics_platform | analytics_96011 | evidence_9601 |
| 4 | FINDING | REPORT_EXPOSED_PII | support_platform | support_96011 | evidence_9601 |
| 5 | FINDING | REPORT_ACTIVE_DATA_FLOW | integration_service | data_flow_96011 | evidence_9601 |
| 6 | FINDING | REPORT_RESTORE_RISK | backup_service | backup_96011 | evidence_9601 |
| 7 | REMEDIATION | DISABLE_DATA_FLOW | integration_service | data_flow_96011 | - |
| 8 | REMEDIATION | BLOCK_RESTORE | backup_service | backup_96011 | - |
| 9 | REMEDIATION | DELETE_RECORD | export_service | export_96011 | - |
| 10 | REMEDIATION | DELETE_RECORD | search_service | search_96011 | - |
| 11 | REMEDIATION | REDACT_RECORD | support_platform | support_96011 | - |
| 12 | REMEDIATION | ANONYMIZE_RECORD | analytics_platform | analytics_96011 | - |
| 13 | VERIFICATION | VERIFY_RECORD_ABSENT | account_service | account_96011 | - |
| 14 | VERIFICATION | VERIFY_RECORD_ABSENT | export_service | export_96011 | - |
| 15 | VERIFICATION | VERIFY_RECORD_ABSENT | search_service | search_96011 | - |
| 16 | VERIFICATION | VERIFY_RECORD_PRESENT | security_service | audit_record_96011 | - |
| 17 | VERIFICATION | VERIFY_RECORD_REDACTED | support_platform | support_96011 | - |
| 18 | VERIFICATION | VERIFY_RECORD_ANONYMIZED | analytics_platform | analytics_96011 | - |
| 19 | VERIFICATION | VERIFY_DATA_FLOW_DISABLED | integration_service | data_flow_96011 | - |
| 20 | VERIFICATION | VERIFY_RESTORE_BLOCKED | backup_service | backup_96011 | - |

### 07_request_only_lookup_decoy

A log trace names search_97099 only on its request line, which carries the person's email as the lookup key; the completed result line returns no resource. Naming a resource in a request is not a link, so search_97099 is not part of this case. The person's account lookup (404) and retained audit record are proven, so the answer is COMPLETE with two verification steps. A wrongly linked decoy would add a step for search_97099.

Requests:

| case_id | email | reported_status | audit_at | request_text |
|---|---|---|---|---|
| case_9701 | `saoirse.quigleigh@celtmail.example` | COMPLETE | 2026-09-18T12:00:00Z | Could you please erase my account, along with any personal data you hold about me? |

Evidence files (`evidence_files.csv`): `evidence_9701` captured 2026-09-17T10:00:00Z.

What the data says:

- `evidence_9701` (log): account lookup returns `account_97011` with HTTP 404; `audit_record_97011` read succeeds.
- Decoy trace: `search_97099` appears only on the request line, with the person's email as `match_key`; the completed line carries no resource ID.

Expected for `case_9701`: **COMPLETE**

| step_no | phase | operation | system_id | resource_id | evidence_id |
|---|---|---|---|---|---|
| 1 | VERIFICATION | VERIFY_RECORD_ABSENT | account_service | account_97011 | evidence_9701 |
| 2 | VERIFICATION | VERIFY_RECORD_PRESENT | security_service | audit_record_97011 | evidence_9701 |

### 08_record_time_rule

audit_at is 2026-09-16 09:00. The log evidence_9803 was captured on 2026-09-18 (late) but two of its lines are timed 08:52 and 08:55 on the 16th, before audit_at, so they still show who owns audit_record_98011 and audit_record_98021 (record-time rule). The file is never cited and supplies no state. case_9801 is INCOMPLETE (a search document remains), so its retained audit record is still listed as a check with an empty evidence_id. case_9802 is COMPLETE, where every step must cite eligible evidence and none exists for its audit record, so only the account step appears. A third line in the same file is timed 2026-09-17, after audit_at, and must not link audit_record_98012 (an extra check step would appear if it did).

Requests:

| case_id | email | reported_status | audit_at | request_text |
|---|---|---|---|---|
| case_9801 | `halvard.eikeland@fjellpost.example` | COMPLETE | 2026-09-16T09:00:00Z | Please delete my account and all personal data linked to it. |
| case_9802 | `signe.moen@fjellpost.example` | COMPLETE | 2026-09-16T09:00:00Z | Please delete my account and all personal data linked to it. |

Evidence files (`evidence_files.csv`): `evidence_9801` captured 2026-09-15T12:00:00Z, `evidence_9802` captured 2026-09-15T18:00:00Z, `evidence_9803` captured 2026-09-18T10:00:00Z.

What the data says:

- `evidence_9801` (JSON export, captured 2026-09-15): `search_98011` for `user_9801` holds the email.
- `evidence_9802` (log, captured 2026-09-15): account lookups return 404 for `account_98011` and `account_98021`.
- `evidence_9803` (log, captured 2026-09-18, after `audit_at`): lines timed 2026-09-16 08:52 and 08:55 read `audit_record_98011` and `audit_record_98021` with the people's `user_id` as correlation key; a line timed 2026-09-17 reads `audit_record_98012` with `user_9801`.

Expected for `case_9801`: **INCOMPLETE**

| step_no | phase | operation | system_id | resource_id | evidence_id |
|---|---|---|---|---|---|
| 1 | FINDING | REPORT_REMAINING_RECORD | search_service | search_98011 | evidence_9801 |
| 2 | REMEDIATION | DELETE_RECORD | search_service | search_98011 | - |
| 3 | VERIFICATION | VERIFY_RECORD_ABSENT | account_service | account_98011 | - |
| 4 | VERIFICATION | VERIFY_RECORD_ABSENT | search_service | search_98011 | - |
| 5 | VERIFICATION | VERIFY_RECORD_PRESENT | security_service | audit_record_98011 | - |

Expected for `case_9802`: **COMPLETE**

| step_no | phase | operation | system_id | resource_id | evidence_id |
|---|---|---|---|---|---|
| 1 | VERIFICATION | VERIFY_RECORD_ABSENT | account_service | account_98021 | evidence_9802 |

