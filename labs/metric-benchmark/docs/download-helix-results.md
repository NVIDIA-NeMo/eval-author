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

## Live collection checks

After following the [submit handoff](submit-helix-evals.md#live-test-handoff), use
its client interpreter, context and external test directory:

```bash
"$HELIX_PYTHON" "$EVAL_AUTHOR_REPO/labs/metric-benchmark/download_helix_results.py" \
  --submission "$HELIX_TEST_DIR/submitted" --output "$HELIX_TEST_DIR/collected" \
  --context "$HELIX_CONTEXT"
```

Run once while jobs are active if possible: expect exit 2 and no published output
directory. Run again after completion: expect exit 0, one observation per task and
agent, and intact native result files and traces. A very short run may complete
before the first check; record that rather than claiming the live pending path was tested.
Repeat the completed download and confirm that the collection is unchanged.
Failures or incomplete terminal matrices must not produce a successful collection.
The mocked suite exercises these failure cases; a successful live smoke alone does not.

For the one-task Oracle/NOP control, inspect the native verifier rewards and compare
them with the prepared task's expected results. Keep full bundles, submission records,
run/job IDs and client/server versions privately for diagnosis. Report only sanitized
outcomes publicly. The expected collection still has `analysis_ready: false` and no
`execution.json`; this is a smoke-test success, not completed analysis integration.

To finish that integration, inspect the public Jobs API's task-job `spec`: the source
version used here stores the effective task/agent configuration, prepared images and
sandbox policy there. Verify those against the prepared inputs and the downloaded
native trial. This is a candidate source of identity evidence, not yet a validated
importer. Do not change recorded checksums or bypass native validation to make the
collection appear analysis-ready. Once the mapping is established, implement and test
the shared results-contract importer against these saved bundles before scaling up.
