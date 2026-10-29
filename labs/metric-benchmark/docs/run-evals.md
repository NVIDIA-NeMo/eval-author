<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# run_evals.py

Run each prepared case’s baseline and mutants against its Harbor task inventory.

## Usage

```bash
uv run python labs/metric-benchmark/run_evals.py \
  --cases cases.yml --execution execution.yml --run-id trial-1 --output private-run
```

Supply `--cases` for case definitions, `--execution` for Harbor settings, `--run-id` for this execution's
identifier, and `--output` for the experiment results directory. Run from the
repository root after `uv sync --locked`; the default execution environment
requires Docker. Prepared agents may have additional runtime or credential
requirements defined by their benchmark bundle.

The cases file references manifests supplying the agents and tasks. Execution
settings do not reference cases or analysis. All inputs require SHA pins by default;
add `--allow-unsigned` while iterating to permit missing pins, while still
verifying any supplied pins.

## Execution and outputs

The loop follows case order, then baseline and mutant order. Each job runs the
whole task inventory once, with retries disabled and configured task concurrency.
Outputs are indexed by case ID in `execution.json`:

```text
private-run/
  execution-inputs.json
  execution.json
  cases/case-0000/execution-results/
    results.json
    agents/agent-0000/
      request.json
      native-request.json
      receipt.json
      run-0001/                 # Intact native Harbor job
        result.json
        config.json
        lock.json
        task-name__suffix/      # Native trial, indexed using metadata
          result.json
          agent/
          verifier/
```

Numeric folder aliases are stable within a definition. The index carries actual
case, agent, task and run identities. Each case index has `run_id` from
`--run-id`, a `task_inventory` array, and an `execution_results` array. Each observation retains all native reward metrics and has an
`agent_run_id` for its agent’s job; `native_results_path` locates the trial evidence.
See the [results format](results-format.md) for the complete contract.
No model or private service is involved in
the orchestration itself. Prepared agents may call models when executed.

## Reusing completed work

Rerunning the command verifies completed job receipts and skips those jobs.
Completed outputs and prepared inputs can be moved together. Reuse currently
requires the execution inputs, effective execution settings, and run ID to match.
Analysis settings, subsets, descriptions, and experiment revision may change
without invalidating completed jobs.
With `--allow-unsigned`, the runner hashes inputs without pins when computing this identity, so editing an
unsigned bundle also requires a fresh output directory. This does not sign or
rewrite the prepared manifests. The retained
`native-request.json` records effective Harbor settings and exact original task
paths for collecting saved trials; collection does not guess identities from
path suffixes. Harbor version remains pinned. Native evidence is never rewritten.
This supports collection/reuse of completed jobs, not migration of an active
Harbor job. Older draft output formats require explicit conversion before reuse;
the runner does not rewrite their requests or receipts.
Changed evidence requires a new output directory. Do not run two writers against
the same output directory.

## Interrupted jobs

If execution stopped between jobs, rerun the original command. If a native Harbor
job is incomplete, complete that job first with Harbor's resume command, using
its original input and output locations. For example:

```bash
uv run --locked harbor jobs resume --job-path \
  private-run/cases/case-0000/execution-results/agents/agent-0001/run-0001
```

Replace the path with the interrupted job's directory, then rerun `run_evals.py`
with the original arguments. Harbor reuses completed trials and reruns unfinished
trials; its default resume behavior removes and reruns cancelled trials. Do not
resume a completed job that this runner has already sealed with a receipt, since
changing its evidence makes reuse fail.

## Python library

`execute_benchmark(loaded_execution, run_id, output)` runs the same stage as the
CLI and returns the experiment manifest. Obtain `loaded_execution` with
[`load_execution`](tools.md#reading-benchmarks). A future NeMo Helix implementation
can produce the same [results contract](results-format.md).

## Local smoke test

The optional `METRIC_BENCHMARK_DOCKER_TEST=1` execution test runs nine local
Oracle/NOP trials and checks CLI-to-library resume. It requires Docker and the
`python:3.12-slim` image; it does not call a model or a benchmark service.
