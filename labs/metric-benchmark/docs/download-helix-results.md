<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# download_helix_results.py — exploratory prototype

Check every submitted Helix run and collect the complete set of task bundles.
The current output is an evidence collection with `analysis_ready: false`.
It deliberately does not publish `execution.json` or claim compatibility with
the benchmark's native-evidence validator.

## Usage

```bash
python labs/metric-benchmark/download_helix_results.py \
  --submission submitted-run --output collected-evidence --context evaluation
```

- `--submission`: directory produced by the [submit tool](submit-helix-evals.md).
- `--output`: destination for the complete native evidence collection.
- `--context`: existing authenticated Helix context; otherwise use the active context.

The command checks once. It exits 2 while jobs are queued/running or results are
being packaged, and exits 1 for failures or incomplete terminal output. Known
failures take priority over other runs still running. An interrupted submission
must first be resumed with the submit tool.

Once all runs are complete, it reads every page of task membership and checks that
every expected task job succeeded and has an artifact. It then downloads each
`agent-eval-results` task bundle, including native Harbor files and agent traces.
It matches evidence through native task IDs, not taskset ordering or directory guesses.
The combined summary alone is insufficient for offline analysis.

```text
collected-evidence/
  collection.json
  submission.json
  runs/run-0000/task-0000/agent-eval-results/
    trials.jsonl
    ... complete original bundle, including native Harbor evidence ...
```

An incomplete transfer never publishes the destination. Rerun to download again;
partial transfer caching is not implemented. A matching completed collection is
reused after checking its files. Archives with traversal, links, special entries,
or duplicate paths are rejected.
Use one writer per destination.

## Python library and remaining boundary

`check_helix_runs(submission, client)` checks completion without downloading.
`download_helix_results(submission, output, client)` implements the CLI operation.
Neither requires the original executable task or agent directories.

The remaining importer must validate Helix's image-bound task configuration and
resolved agent configuration against the prepared case identities, then emit the
[shared results contract](results-format.md). Current local validation targets
Harbor 0.20.0; inspected Helix uses 0.24.0. No compatibility bypass or native JSON
rewriting is included. Until that boundary is qualified with deployed evidence,
these collections cannot be passed directly to score analysis.
