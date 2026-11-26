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

## Baseline-only execution

Use the same agent manifest with `baseline: ...` and an explicit `mutants: []`
to run only the baseline, with normal validation, receipts and completed-job reuse.
`run_experiment.py`, `compute_mutant_scores.py`, and `compute_coverage.py` still
require at least one mutant.
Execution needs no analysis configuration. Without a full experiment bundle for
[SHA signing](sha-sign.md), supply pins on all input references yourself or use
`--allow-unsigned` while iterating.

## Execution and outputs

The loop follows case order, then baseline and mutant order. Each job runs the
whole task inventory with `n_attempts` set to the case's `repetitions` (default 1),
with retries disabled and configured task concurrency. See the
[case configuration example](benchmark-format.md#cases-casesyml).
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
Within each task, the collector assigns 1-based repetition numbers by sorted
native trial-directory identity, never by reward or completion time. These numbers
identify retained samples; they do not imply matched random seeds across agents.
Collection requires exactly the configured number of trials for every task.
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


## Import pinned ATOM runs from Scaled Evals

The experimental `metric_benchmark.scaled_import` adapter imports existing
Scaled Evals archives into the standard metric-benchmark results format. It is
qualified for the fixture's `atom_runtime:LockedAtomAgent` identities, the
`MatrixCommandAgent` deployment adapter, and Harbor 0.25.0. It does not submit
jobs or install credentials.

Prepare a private campaign directory containing `bindings.json`, the original
task packs, and the exact agent build contexts. The binding contract is
`Bindings` in `metric_benchmark/scaled_import.py`: it records the case and
benchmark, repetition count, task publication/image/profile/source hashes, and
each selected agent bundle and run. Paths are relative to the campaign directory.
Collect the exact completed runs with the fixtures repository's `eval-matrix`
downloader; retain its metadata, matrix, manifest, and native archives. Both
results archives and `--archive-source harbor-viewer` exports are supported.
The latter can be available while the secondary results archive is still
building. Both must contain unique terminal Scaled provenance and the native
trial evidence required by the importer.

```bash
PYTHONPATH=labs/metric-benchmark uv run --locked python -m metric_benchmark.scaled_import \
  --cases /private/campaign/inputs/cases-atom.yml \
  --campaign /private/campaign \
  --collection /private/campaign/collection \
  --output /private/campaign/imported-results \
  --include-verified-grader-reward
```

If terminal persistence drops the provenance runtime handle, both
`scaled_import` and `scaled_repair` accept the opt-in
`--recover-native-job-identity` flag. This requires the archived root job
configuration to name the exact current execution and repetition count, the
root job result to identify its UUID and trial budget, and each native trial
to match that UUID and execution directory. A conflicting runtime handle is
still rejected. The importer pins the additional job evidence in each proof;
it does not edit the service provenance or infer a reward from parent status.
All task, agent, source, model, grader, and trace checks still apply.

The output directory must be new. Import validates pinned task and agent bytes,
published identities, model settings, native task checksums, and execution
provenance before publishing the results. Full benchmark imports require a
published task-pack digest. A standalone preflight can explicitly record that
an older publication lacks this digest; native task-byte checks still apply.

Set Scaled Evals `--n-attempts` to the case's `repetitions`. A service evaluation
contains multiple native trials; each becomes a separate observation. Repetition
labels are stable archive-order ordinals, not paired random seeds. The service's
aggregate reward is never copied into the individual observations. Missing
native trials remain unavailable; infrastructure retries use the execution
number and do not add repetitions.

The optional `verified_grader_reward` metric retains completed, consistent binary
grader outcomes separately from strict execution success. It requires identity,
trace, and grader evidence and supports only qualified terminal agent failures;
infrastructure failures remain unavailable. To analyze this metric, set
`scoring.metric: verified_grader_reward` and
`scoring.completed_statuses: [succeeded, error]`. The original execution status
and strict `reward` remain available for comparison. Incomplete repetition sets
retain unavailable means under the normal repetition policy.

A derived repair cohort can be assembled with
`python -m metric_benchmark.scaled_repair --cases CASES --original RESULTS --repairs RECEIPTS --output NEW_RESULTS`.
It requires a bounded plan, creation receipts, terminal metadata, and native
archives. One replacement evaluation may supply the missing repetition slots
of one original evaluation, using the same repetition budget. Valid original
outcomes are retained; replacement slots are selected by repetition ordinal,
not by reward. The original results and repair receipts remain pinned in the
derived cohort. This command assembles collected evidence; it submits no jobs.
