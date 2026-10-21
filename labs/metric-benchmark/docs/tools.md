<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Benchmark tools

These tools consume externally prepared cases. Subset construction is not a
runtime stage. A benchmark is a YAML definition plus its pinned inputs; a run ID
identifies one execution of that benchmark. Prepared subsets can be reused across
runs without modification.

## Benchmark definition

`metric_benchmark.config.load_benchmark(path)` reads YAML, rejects unknown fields
and duplicate mapping keys, and verifies input digests before execution.
`BenchmarkDefinition.model_json_schema()` exposes the complete schema.

Each definition contains `schema_version: 1`, `benchmark_id`, `revision`, `cases`,
`execution`, and `missing_policy` (`error` by default, or explicit `exclude`).
Each case supplies:

- `case_id`, a `task_inventory` and `task_sources` keyed by task ID;
- `baseline` and `mutants`, each with `agent_bundle_id`, `revision`, native
  `harbor` agent settings, and optional named `inputs`;
- `case_description` (a JSON object) and `subsets` (a fixed JSON manifest);
- `scoring` policy and `coverage` system ID, revision, command and settings.

File references have `path`, `sha256` (`sha256:<hex>`), and `kind` (`file` or
`directory`). Paths are relative to the YAML file; traversal and symlinks are
rejected. Directory digests hash the canonical JSON mapping of relative file
paths to SHA-256 digests, using `input_digest(path)`. Prepared agent dependencies
can be referenced in native settings by the exact string `{input:NAME}`.

The subset manifest contains `schema_version: 1`,
`artifact_type: eval_subset_definitions`, `benchmark_id`, `task_inventory`,
`sampling_parameters` (including the preparation seed), and `eval_subsets`.
Each subset has `eval_subset_id` and explicit `tasks` with `task_id` and `revision`.
The manifest's membership is authoritative; runtime code never samples from the
seed. Duplicate subsets or tasks and unknown revisions are rejected.

The initial backend is `local_harbor`, pinned to Harbor 0.20.0. Its settings are
`n_concurrent_trials` (default 1) and native `environment` configuration (Docker
by default). NeMo Helix is a future backend. Native Harbor settings and coverage
commands are trusted local programs, not a sandbox or secret store. Supply
credentials through the runtime environment, not benchmark YAML committed to Git.

## Validate a results directory

```bash
uv run python labs/metric-benchmark/validate_results.py \
  --benchmark benchmark.yaml --case case-id \
  --results-root execution-results --output validation.json
```

Library: `validate_results(raw, case, results_root, benchmark_root)`.
Every configured agent/task pair must be accounted for, with one selected run per
agent and an explicit attempt policy. The validator checks native Harbor trial
identities, configured agents, completion status, measurements, and safe evidence
paths; it hashes the files for subsequent change detection. The initial validator
reads native directories, not archives or multi-step trial aggregations.
A missing execution is an explicit `status: missing` entry with a null measurement
and `unavailable_evidence.native_results` reason. A completed verifier zero is a
valid observation; an execution exception is unavailable even if it saved a reward.
Store the validation report outside the results root.

These checks establish artifact consistency, not mutation activation, verifier
correctness, or scientific validity. Such qualification belongs to prepared cases.

## Execute the eval tasks

```bash
uv run python labs/metric-benchmark/run_evals.py \
  --benchmark benchmark.yaml --run-id trial-1 --output private-run
```

Library: `execute_benchmark(loaded_benchmark, run_id, output)`.
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
case, agent, task and run identities. No model or private service is involved in
the orchestration itself. Prepared agents may call models when executed.

Rerunning the command verifies completed job receipts and skips those jobs.
Changed inputs or evidence require a new output directory. An interrupted native
job is preserved and must be explicitly completed with Harbor's own resume
mechanism before this runner can collect it; the runner never silently retries
partial observations. Do not run two writers against the same output directory.

The optional `METRIC_BENCHMARK_DOCKER_TEST=1` execution test runs six local
Oracle/NOP trials and checks CLI-to-library resume. It requires Docker and the
`python:3.12-slim` image; it does not call a model or a benchmark service.

## Compute mutant scores

```bash
uv run python labs/metric-benchmark/compute_mutant_scores.py \
  --results execution-results/results.json --subsets subsets.json \
  --config selection.json --output mutant-scores.json
```

Library: `compute_mutant_scores(raw, subsets, selection)`.
`case.selection()` produces the selection configuration for local execution;
write it with `write_json(path, case.selection())` for manual CLI use.
The score is the equally weighted mean over mutants of the signed mean paired
task degradation. Improvements remain negative. The output includes `diagnostics`
with `code: negative_mutant_score`, subset ID, and negative value.
`mutant_bundle_id` identifies an individual effect; null identifies the subset
aggregate. These flags are for inspection only: they neither fail scoring nor
clip values or exclude correlation pairs. Incomplete required pairs make
the subset unavailable. `attempt_policy: latest` selects by attempt number,
never by reward. Repetitions require a future explicit aggregation policy.
The prepared subset manifest is unchanged when the experiment run ID changes.
