<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# compute_coverage.py

Compute coverage for each predefined task subset using its baseline evidence.

## Usage

```bash
uv run python labs/metric-benchmark/compute_coverage.py \
  --cases cases.yml --analysis analysis.yml --case case-id --results-root experiment-results \
  --work-root coverage-evidence --output coverage-scores.json
```

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

Completed subset requests have file-digest receipts. Resume verifies those files
and inputs before reuse. `--cached-only` prohibits new invocations. A command
failure preserves its private diagnostic workspace without a completion receipt.
On POSIX, timeout cancellation kills the command's process group, including its
children. Keep all run directories private and outside Git.


## Eval Author adapter

See the [`eval_author_coverage.py` adapter guide](eval-author-coverage.md) for
configuration, source pins, authoring outputs, and native measurement behavior.

## Python library

`invoke_coverage(raw, subsets, selection, configuration, case_description,
record_root, work_root, *, cached_only=False)`.
