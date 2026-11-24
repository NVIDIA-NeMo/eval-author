<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# compute_coverage.py

Compute coverage for each predefined task subset using its baseline evidence.
The case still needs a mutant; see the
[baseline-only execution limits](run-evals.md#baseline-only-execution).

## Usage

```bash
uv run python labs/metric-benchmark/compute_coverage.py \
  --cases cases.yml --analysis analysis.yml --case case-id --results-root experiment-results \
  --work-root coverage-evidence --output coverage-scores.json
```

The first argument of `coverage.command` must be an absolute executable path,
for example `/path/to/venv/bin/python`. See the
[analysis format](benchmark-format.md#analysis-analysisyml) for its configuration.

## Inputs and behavior

As with validation and mutant scoring, `--results-root` is the whole experiment
directory; `execution.json` locates the selected case and its baseline evidence.
The configured CLI reads the case description, fixed subsets, coverage settings,
and selection from the case definitions and analysis settings and the results index, with one run per agent.
It verifies the selected subset and description pins and reads baseline evidence;
it does not require the prepared task directories or agent bundles. It does not
perform the separate native results validation stage. Programmatic callers use
the library API for explicit model inputs.
Each invocation stages the subset's baseline evidence and case description,
then calls the configured command with `--request request.json --output
response.json`. Responses contain `value` (finite number), or `value: null` and
`missing_score_reason`. The request does not include mutant evidence or outcomes. The command
is trusted and runs on the host; staging is not a sandbox.

With `coverage.missing_baseline: unavailable`, an incomplete subset retains its
full task membership and missing-task list, but stages no native evidence and
does not invoke the coverage command. Its score is unavailable.
To omit unavailable scores during [correlation](correlate.md), also set the
top-level `missing_policy: exclude` in `analysis.yml`; its default is `error`.

Completed subset requests have file-digest receipts. Resume verifies those files
and inputs before reuse. `--cached-only` prohibits new invocations. A command
failure, including a missing or invalid response or modified input evidence,
preserves its private diagnostic workspace and logs without a completion receipt.
On POSIX, timeout cancellation kills the command's process group, including its
children. This workflow is tested on Linux; use WSL on Windows. Native Windows
process-tree cleanup is not supported. Keep all run directories private and
outside Git.


## Eval Author adapter

See the [`eval_author_coverage.py` adapter guide](eval-author-coverage.md) for
configuration, source pins, authoring outputs, and native measurement behavior.

## Python library

`invoke_coverage(raw, subsets, selection, configuration, case_description,
record_root, work_root, *, cached_only=False)`.

## Repeated measurements

For `repetitions: n` in the case, each subset produces `n` independent coverage
requests. Request `repetition: k` contains the baseline evidence for repetition
`k` of each task, plus `repetitions: n`. Each request runs the existing author and
measurement procedure, including evidence-bound semantic judgment sidecars.
Mutant evidence and reward values are never supplied.

The reported subset score is the arithmetic mean of those `n` measurements,
not the union of all observed behaviors. `repetition_scores[subset_id]` retains
the scores in repetition order; `receipts[subset_id]` retains the corresponding
receipt hashes in that order. If any measurement is unavailable, the mean stays
unavailable with its repetition-specific reason. Native baseline evidence checks
remain strict: a missing or incomplete execution aborts coverage under the default
policy. Unjudged semantic coverage remains unavailable.

Work directories `subset-NNNNNN` enumerate requests in sorted subset order, then
repetition order. Each request has its own evidence and resume receipt. Changing
the repetition count requires fresh output; cached replay verifies every request.
Correlation uses the averaged coverage and averaged reward-degradation scores
for each subset, with their common repetition count bound into the score context.
