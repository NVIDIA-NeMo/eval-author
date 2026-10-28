<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Results format

The [benchmark format](benchmark-format.md) defines the prepared inputs and
experiment configuration, including the case examples, their baseline agents
and mutants, and the evaluation tasks to run.

Execution produces one results directory per prepared case example. It contains
a shared `results.json` index and the raw results for the baseline and mutants
running against that example's tasks. The experiment definition determines which
agents and tasks the results must account for; this document defines how those
results are represented. The validator checks that they account for the configured
agent × task matrix and agree with the prepared inputs and native execution evidence.

This structure is the interface between agent × task execution and score analysis.
It contains observations, not mutant scores, coverage scores or correlations.

See [tool usage](tools.md#validate-a-results-directory) for CLI and library entry points.

This is an experimental format and may change with the benchmark. The current
native reader supports single-step Harbor 0.20.0 trial directories. The index
models live in [models.py](../metric_benchmark/models.py); native consistency
checks live in [validation.py](../metric_benchmark/validation.py).

## Directory layout

Each prepared case example has a results directory with two kinds of JSON files:

- **`results.json` at the root** is the experiment runner's summary of all agent/task executions for that example. Each entry names the agent and task, records the attempt, status and measurement, and points to the directory containing its evidence. This summary file is what this document calls the **results index**.
- **`result.json` inside each trial directory** is Harbor's detailed record of one execution attempt. It contains the native configuration and outcome that the validator compares with the corresponding summary entry. This is Harbor's standard format.

The directory groups trials by agent and run:

```text
execution-results/
  results.json                      # Summary of all executions for this case example
  agents/
    <agent-directory>/
      <run-directory>/
        <trial-directory>/
          result.json               # Harbor record for this one attempt
          agent/                    # Agent execution evidence, when produced
          verifier/                 # Verifier outputs, when produced
          ...
        ...
      ...
    ...
```

For example, the [mock results index](../../../tests/fixtures/metric_benchmark_results/execution-results/results.json)
names `baseline` as the agent and `inspect-files` as the task in its first entry.
That entry's `native_results_path` points to
`agents/agent-0000/run-0001/inspect-files__mock001`. A reader follows that path to
find the Harbor record and other evidence for this attempt.

The agent and task IDs are explicit fields in `results.json`; folder names such
as `agent-0000` are storage labels. The validator does not require those particular
grouping names. The next section defines the summary fields, followed by the
requirements for the native evidence they reference.

## Results index: `results.json`

JSON objects reject unknown fields and duplicate keys. Numbers must be finite;
identifiers and reasons must be nonblank strings. There is no `schema_version`
field in the current format. Optional fields may be omitted; serialized examples
also show their explicit defaults.

| Field | Required / default | Meaning | Source |
| --- | --- | --- | --- |
| `run_id` | Required | ID supplied by the caller when starting this execution of the experiment; the local runner takes it from `--run-id`. | CLI: `--run-id`. |
| `task_inventory` | Required, nonempty | Array of tasks configured for this case example, including tasks whose executions failed or are missing. | YAML: `tasks.yml` → `tasks`, referenced by `experiment.yml` → `cases[].tasks.path`. |
| `execution_results` | Required, nonempty | Outcomes for each agent/task pair: one entry per attempt, including explicit entries for missing executions. | Runner-generated from the configured agents/tasks and Harbor trial results. |

For example, starting the runner with `--run-id trial-001` produces
`"run_id": "trial-001"` in each case example's results index. The per-result `agent_run_id` below
identifies one agent's run within that execution.

The sources below describe the local runner. `agent.yml` and `tasks.yml` are the
manifest files referenced by `experiment.yml`; generated ID spellings are runner
conventions, not requirements imposed by the validator.

### Task inventory: `task_inventory`

The `task_inventory` field is a required, nonempty array, just like
`execution_results`. Each object describes one task configured for the case example:

| Field | Required / default | Meaning | Source |
| --- | --- | --- | --- |
| `task_id` | Required | Task ID from the prepared task manifest. | YAML: `tasks.yml` → `tasks[].task_id`. |
| `revision` | Required | Positive integer identifying the task revision. | YAML: `tasks.yml` → `tasks[].revision`. |
| `name` | Defaults to `null` | Optional human-readable task name. | YAML: optional `tasks.yml` → `tasks[].name`; otherwise `null`. |

Task IDs are unique. The inventory must match the prepared case exactly,
including task order and optional names. Each task is listed once here, even
though the baseline and every mutant execute it separately.

### Execution results: `execution_results`

Each object in the top-level `execution_results` array describes one agent/task
attempt, including attempts recorded as missing. The runner assigns an agent ID
from its position in the execution list, such as `agent-0000` for the baseline.
This generated ID is separate from the configured `agent_bundle_id`.
The ID shapes shown below are naming conventions. Otherwise, `agent_run_id` and
`evaluation_id` are opaque IDs, unique to an agent run or evaluation within this
case’s results index; consumers do not need to parse their components.

| Field | Required / default | Meaning | Source |
| --- | --- | --- | --- |
| `agent_run_id` | Required | Unique ID for one agent run within the case’s results index, shared by its task executions. | Runner-generated as `<runner agent ID>/<job name>`; for example, `agent-0000/run-0001`. |
| `agent_bundle_id` | Required | Baseline or mutant ID from the prepared agent manifest. | YAML: `agent.yml` → `baseline.agent_bundle_id` or `mutants[].agent_bundle_id`. |
| `framework_profile_id` | Defaults to `null` | Optional profile identity; it must stay consistent within a run. | Optional producer metadata; the local runner leaves it `null`. |
| `evaluation` | Required | Task and attempt identity, defined below. | Runner-generated identity and attempt, plus a task reference from `tasks.yml`; see below. |
| `status` | Required | Native outcome: `succeeded`, `error`, or an explicit `missing` execution. | Harbor: derived from the native trial outcome. A producer explicitly declares `missing`. |
| `metrics` | Defaults to `{}` in the model | Map from measurement names to score objects. Validation requires the experiment's configured metric to be present. | YAML: `experiment.yml` → `scoring.metric` (default `reward`) selects the key. Harbor: `result.json` → `verifier_result.rewards` supplies the value; the runner derives unavailable reasons. |
| `native_results_path` | Required | Relative trial-directory path, or a nonexistent location for a missing execution. | Runner-generated relative path to the Harbor trial directory; a producer supplies a placeholder for missing evidence. |
| `native_results_format` | Required | Use `directory` for the current native validator. The model also recognizes `tar.gz`, but observed archives are not supported by this validator. | Runner-generated; the local runner sets `directory`. |
| `unavailable_evidence` | Defaults to `{}` | Map from evidence names to reasons. Missing executions require a `native_results` reason. | Optional producer-supplied reasons; the local runner leaves this empty and rejects incomplete jobs. |
| `outcome_category` | Defaults to `null` | Optional descriptive metadata; it does not determine status or scoring. | Optional producer metadata; the local runner leaves it `null`. |

`evaluation` contains:

| Field | Required / default | Meaning | Source |
| --- | --- | --- | --- |
| `evaluation_id` | Required | Unique ID for one evaluation within the case’s results index, shared by retries of the same task/run. | Runner-generated as `<runner agent ID>/<task_id>`; for example, `agent-0000/inspect-files`. The task ID comes from `tasks.yml`, not the optional task name. |
| `execution` | Required | Positive integer attempt number. | Runner-generated; the local runner sets `1`. |
| `task` | Required | Task reference with the same fields as an inventory task. Its ID and revision must occur in the inventory. | YAML: `tasks.yml` → the matching `tasks[]` entry’s `task_id`, `revision`, and optional `name`. |

Every `(evaluation_id, execution)` pair is unique across the index. Reusing an
`evaluation_id` must preserve the run and task identity. An `agent_run_id` identifies
one agent/profile combination; different agents cannot share it.

### Measurements and unavailable observations

Each execution result’s `metrics` field is an object keyed by metric name (such
as `reward`), where each entry contains a required `value` and optional `reason`:

```json
{"metrics": {"reward": {"value": 0.0, "reason": null}}}
```

A numeric value must be finite and have no non-null reason. An unavailable value
must be `null` and have a nonblank reason. Zero and negative numbers are valid
measurements; neither means that evidence is absent.

For the configured metric, the index must agree with native Harbor observations:

| Situation | Status | Metric value and reason |
| --- | --- | --- |
| Finished trial with a verifier measurement | `succeeded` | Native numeric value; no reason. |
| Finished trial without that verifier metric, or with a null metric | `succeeded` | `null`, with `native verifier metric absent: <metric>`. |
| Finished trial with `exception_info` | `error` | `null`, with `execution error: <exception_type>`, even if a reward was saved. |
| Execution has no native evidence | `missing` | `null`, with an explicit reason; also supply `unavailable_evidence.native_results`. |

A missing execution still occupies its agent/task position in the index, and its
`native_results_path` must not exist. An unfinished native trial is rejected;
it cannot be presented as a completed observation. Multi-step trials without
an execution exception are rejected because no aggregation policy is implemented.
Validation compares the configured metric, including its reason, against the
native observation. Other metric keys are schema-checked but are not compared
against native measurements by this validator.

## Matrix and attempts

Every configured baseline/mutant must appear, and every configured agent/task
pair must be accounted for. Missing rows are not silently treated as unavailable;
use explicit `missing` entries. Unexpected agents or task revisions are rejected.

One run per agent and one logical `evaluation_id` per agent/task pair are
allowed. The experiment's `scoring.attempt_policy` controls retries:

- `only` (default): exactly one execution entry per pair.
- `latest`: multiple attempts of the same evaluation are permitted. Validation checks all supplied attempts; downstream scoring selects the highest attempt number, not the best reward.

Independent repetitions with different evaluation IDs for the same pair are
ambiguous and rejected. Native trial evidence cannot be reused by multiple
observed entries. Validation reports all observations, including unavailable
attempts, rather than applying scoring's attempt selection.

## Native evidence and relocation

Store the raw Harbor execution files under the results root. Each entry in
`results.json` points to its trial directory through `native_results_path`, a
relative POSIX path within that root. Keep the configuration, results, logs and
available agent/verifier evidence; symlinks and special files are not allowed.
The validator reads each observed trial’s Harbor 0.20.0 `result.json` and checks
the task identity/checksum, trial directory name, agent configuration, status and
configured measurement against the prepared inputs and results index.

The results directory can move with its prepared input bundle, and trials may
come from different worker roots. The validator uses the supplied local inputs;
it does not open paths on the original worker. Harbor’s recorded `config.task.path`
must still be an absolute POSIX path ending in the task’s bundle-relative source
path so recorded agent input paths can be compared. Validation establishes
consistency with the prepared inputs, not the scientific validity of the results
or whether all trials came from one submission.

## Validation report: `validation.json`

The `validate_results.py` CLI reads `results.json` and the raw execution evidence
under `--results-root`, checks them against the case selected by `--benchmark` and
`--case`, and writes this report to `--output` after all checks succeed. For example,
`--output validation.json` names the output file; keep it outside the results root.
Explicitly unavailable observations do not themselves cause validation to fail.
The Python library returns the same report as a dictionary.

| Field | Meaning | Source |
| --- | --- | --- |
| `case_id` | Selected prepared case. | YAML: `experiment.yml` → `cases[].case_id`, selected by the validator’s `--case` flag. |
| `run_id` | Execution identity copied from the index. | Copied from `results.json` → `run_id`, originally supplied by the execution CLI’s `--run-id`. |
| `expected_pairs` | Number of configured agents multiplied by number of tasks. | Validator-generated from the selected case’s `agent.yml` and `tasks.yml` manifests. |
| `observations` | Number of execution entries, including missing entries and retries. | Validator-generated count of `results.json` → `execution_results`. |
| `unavailable` | Entries with null configured measurements, each containing `agent`, `task`, `attempt`, and `reason`. | Validator-generated from execution entries whose configured metric value is `null`. |
| `files` | Map of relative evidence-file paths to `sha256:<hex>` digests. | Validator-generated by hashing evidence files under CLI `--results-root`. |

The file manifest covers ordinary files under the results root except the
root-level `results.json`. Consumers hash the index separately; an execution
backend may validate an in-memory index before publishing that file. The report
therefore is not a self-contained seal of the index. Placing it outside the root
also prevents it from becoming part of its own evidence manifest.

## Example output

The checked-in [mock results directory](../../../tests/fixtures/metric_benchmark_results/)
matches the [example input bundle](../../../tests/fixtures/metric_benchmark/experiment.yml).
It shows one baseline and two mutants, each completing the same three tasks:

```text
metric_benchmark_results/
  validation.json                       # Output of the validation tool
  execution-results/                    # Pass this directory as --results-root
    results.json                        # Shared index: all nine agent/task pairs
    agents/
      agent-0000/                       # baseline (Oracle stand-in)
        run-0001/
          inspect-files__mock001/
            result.json                 # Native Harbor trial record
            verifier/reward.txt         # Mock verifier output
          read-config__mock001/         # Same two files
          search-workspace__mock001/     # Same two files
      agent-0001/                       # mutant (NOP stand-in), same three trials
      agent-0002/                       # mutant-no-search (NOP stand-in), same three trials
```

Review [results.json](../../../tests/fixtures/metric_benchmark_results/execution-results/results.json)
first, then follow one row's `native_results_path` to its
[native result](../../../tests/fixtures/metric_benchmark_results/execution-results/agents/agent-0000/run-0001/inspect-files__mock001/result.json).
Finally, [validation.json](../../../tests/fixtures/metric_benchmark_results/validation.json)
shows the nine accounted pairs and evidence-file hashes. Each baseline reward is
1; each mutant reward is 0. All nine executions succeeded: a zero reward is an
observed outcome, not a missing execution or an execution error.

These are fabricated observations, not measurements from actual agent runs.
The example keeps only trial records and verifier rewards; real Harbor jobs also
contain job metadata, logs and agent evidence used by later stages. Optional
native fields with default values are omitted. Recorded `/mock/metric-benchmark/`
paths illustrate the producer's original location; validation does not open them.
The report is outside the evidence root so it does not include itself in its hashes.
