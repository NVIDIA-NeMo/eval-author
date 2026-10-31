<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# eval_author_coverage.py

Adapt Eval Author authoring and native measurement to the coverage command interface.
Use this adapter with [`compute_coverage.py`](compute-coverage.md).

## Configuration and usage

Set `coverage.command` to absolute paths for the Python interpreter and
`labs/metric-benchmark/eval_author_coverage.py`. Its `settings` contain:

- `eval_author_root`: the absolute Eval Author checkout path;
- `source_sha256`: `metric_benchmark.eval_author.source_digest(root)`, pinning
  the audit Python implementation and JSON schemas;
- `author_command`: the absolute executable and arguments that invoke the
  system under evaluation's authoring procedure;
- optional `methods` (default `tool_calls`, `capabilities`, `failure_cases`) and
  `score_scope` (`overall`, `tool`, `capability`, or `failure_case`).

Obtain the source pin without opening Python:

```bash
uv run python labs/metric-benchmark/eval_author_coverage.py \
  --print-source-digest /path/to/eval-author
```

The author command receives `--request request.json --output-dir authored` and
produces `authored/audit.md` plus `authored/judgments.json`. The latter maps task
IDs to optional `capability_judgments` and `failure_case_judgments` file paths,
relative to `authored/`. Empty judgments (`{}`) work for a tool-only audit.
Audits with semantic requirements need their judgment sidecars; selecting only
`tool_calls` does not make an unmeasured semantic denominator valid.
This is the adapter boundary for running a skill or another authoring tool.
Authoring and semantic judgments can involve a model. They are observations of
the evaluated system, not instructions for an experiment operator to interpret.
The benchmark does not accept precomputed coverage as an input.

The adapter invokes Eval Author's actual `measure.py` on every baseline ATIF
trace and `report.py` on the resulting coverage artifacts. It preserves the audit,
judgments, measurements and report. The numeric score is covered items divided
by denominator items for the configured scope. Missing semantic judgments,
unmeasured denominator kinds and an empty denominator yield unavailable scores.
Judgment availability is checked only for the selected `score_scope`; `overall`
checks every measured kind. Unjudged capability evidence does not invalidate a
tool-only score.
Invalid/stale judgment sidecars fail native validation. Legacy non-ATIF traces
need an explicit conversion before ingestion; the adapter does not reinterpret
or repair traces. Native directories are required by this adapter.

Native Eval Author reports retain their evidence bindings, including absolute
workspace paths. Recomputing coverage from the same authored inputs preserves
scores, but native report bytes and their receipt hashes can differ between
workspaces. Cached replay preserves the exact saved artifacts. The adapter gives
each task a stable measurement run ID so multi-task subsets satisfy Eval Author's
distinct-evidence validation.
