<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# run_experiment.py

Run a prepared benchmark from agent execution through coverage, mutant scoring,
and correlation, or analyze execution results you already have.

## Generate an experiment configuration

Running a complete benchmark can be expensive. The optional
[`prepare_experiment.py`](../prepare_experiment.py) tool generates the same
experiment format with fewer mutants and tasks, so you can check infrastructure
or obtain early signals before running the full composition. It also generates
full experiments from cases and your execution settings.

Generation copies selected inputs, applies configuration overrides, writes and
validates content pins, and reports planned trial counts. It starts no containers,
installs no agents, downloads no tasks, and makes no model calls. You can still
write the same YAML by hand. The existing runner consumes both forms identically.

Run the following from the repository root after `uv sync --locked`. This example
uses the repository's public Oracle/NOP case and synthetic coverage command;
it does not require model credentials.

```bash
uv run --locked python labs/metric-benchmark/prepare_experiment.py \
  --benchmark-source labs/metric-benchmark/examples/smoke \
  --execution labs/metric-benchmark/examples/smoke/execution.yml \
  --selection labs/metric-benchmark/examples/smoke/selection.yml \
  --benchmark-id smoke-reduced \
  --output /tmp/smoke-reduced

uv run --locked python labs/metric-benchmark/run_experiment.py \
  --benchmark /tmp/smoke-reduced/experiment.yml \
  --run-id smoke-1 --output /tmp/smoke-reduced-results
```

This selects the baseline, one mutant, and one task: **two trials**. The second
command requires Docker. Its one analysis subset checks plumbing; it cannot
establish a meaningful coverage correlation. Use new output directories when
repeating the example.

Omit `--selection` to preserve all cases, mutants, tasks, and predefined subsets:

```bash
uv run --locked python labs/metric-benchmark/prepare_experiment.py \
  --benchmark-source labs/metric-benchmark/examples/smoke \
  --execution labs/metric-benchmark/examples/smoke/execution.yml \
  --benchmark-id smoke-complete \
  --output /tmp/smoke-complete
```

That example produces six trials: the baseline and two mutants on two tasks.
Execution remains a separate `run_experiment.py` invocation.

### Inputs and selection

The source directory contains `cases.yml`, `analysis.yml`, and referenced prepared
inputs in the existing [benchmark format](benchmark-format.md). An existing
`experiment.yml`, Git checkout, or preparation receipt is not required. Input
references are relative to the directory containing `cases.yml`. All referenced
manifests and selected inputs must have valid pins; generation never silently
re-signs modified source content. Use [sha-sign](sha-sign.md) when authoring inputs,
and the [task downloader](tools.md#download-remote-harbor-tasks) to materialize
remote task sources before generation.

| Flag | Purpose |
| --- | --- |
| `--benchmark-source` | Required directory containing prepared cases and their inputs. |
| `--execution` | Required execution-settings YAML; concurrency controls scheduling. |
| `--benchmark-id` | Required identity for the generated experiment. |
| `--output` | Required new directory outside the source bundle. |
| `--selection` | Optional YAML selecting one derived case, mutants, tasks, and analysis subsets. |
| `--case` | Include a complete source case; repeat for multiple cases. Defaults to all cases. Cannot accompany `--selection`. |
| `--analysis` | Alternative analysis YAML; its references still resolve from the source root. |
| `--agent-settings` | Harbor configuration mapping merged into every included baseline and mutant. |
| `--coverage-author` | Optional standalone author-script snapshot, described below. |

The [example selection](../examples/smoke/selection.yml) has this shape:

```yaml
source_case_id: smoke-case
case_id: smoke-reduced
mutant_ids: [nop]
task_ids: [answer]
subsets:
  - eval_subset_id: answer-only
    task_ids: [answer]
```

The baseline is always retained. `case_id` must differ from every source case ID.
`mutant_ids` and `task_ids` accept explicit nonempty ID lists or `all`; source
inventory order is preserved. `subsets` accepts explicit nonempty memberships or
`parent` to reuse the original subset document. Every subset member must be in
the selected task inventory, so `parent` fails if a retained subset refers to a
removed task. Unknown IDs, duplicates, and empty inventories are rejected.
Subsets group results for analysis; they do not schedule additional trials.

### Agent and analysis settings

`--agent-settings` uses ordinary Harbor configuration, for example:

```yaml
model_name: provider/model
kwargs:
  adapter_option: value
```

Nested mappings merge recursively; explicit values replace defaults and lists
replace lists. Unspecified values remain unchanged. The generator does not
interpret adapter-specific kwargs. Overrides can change installation settings
as well as model settings: review the resulting manifests when overriding input
paths. Named `{input:NAME}` references resolve to the copied input files. The
adapter and its host dependencies must be importable in the execution environment,
just as for a handwritten experiment; generation does not install them.

`--analysis` independently supplies the coverage command and its settings. Those
commands remain literal, with no parameter substitution or inferred provider
configuration. Keep credentials in the runtime environment rather than YAML.

For a coverage integration using `coverage.settings.author_command`, the optional
`--coverage-author /absolute/path/author.py` copies that standalone file into the
output's `coverage/` directory. Its absolute source path must occur exactly once
in `author_command`, and its digest must equal `coverage.system_revision`. The
command is rewritten to use the absolute output path. The script is verified
while copying; imported modules and its interpreter environment are not bundled.
Other coverage tools need no author script or special settings.

### Generated output

The output contains `experiment.yml`, `cases.yml`, `execution.yml`, `analysis.yml`,
per-case manifests, and selected input copies under `inputs/`. Reference paths
are rewritten to the copies; executable input bytes remain unchanged. Source
files are never edited. A failure leaves no published output directory; existing
outputs are rejected. Use distinct destinations for concurrent invocations.

`generation.json` records input-manifest hashes, resolved selections, and the
optional author snapshot pin. It is an informational generation receipt, not a
new requirement of the experiment format. The ordinary runner validates the
standard manifest graph; it does not read this receipt or independently enforce
the optional author snapshot pin. Keep generated author code unchanged or use
your coverage integration's runtime verification. Absolute coverage-command
paths and external dependencies still need to exist when executing the result.

## Start an experiment

Run from the repository root after `uv sync --locked`. Supply a prepared
[experiment configuration](benchmark-format.md), Docker for local execution,
and any runtimes or credentials required by its agents and coverage command.
Preparation of cases, mutants, tasks, and fixed subsets happens before this step.

```bash
uv run python labs/metric-benchmark/run_experiment.py \
  --benchmark experiment.yml --run-id trial-1 --output private-run
```

- `--benchmark` selects the YAML referencing cases, execution, and analysis settings.
- `--run-id` identifies this execution and must stay the same when reusing it.
- `--output` selects the directory for execution results and analysis artifacts.
  Use a new directory for a new experiment.

Inputs require SHA pins by default. Use [sha-sign](sha-sign.md) to sign prepared
inputs; add `--allow-unsigned` while developing a bundle to permit missing pins.
Supplied pins are always checked. Keep outputs outside Git and use only one
writer per output directory.

The command runs each baseline and mutant against its tasks, validates the native
results, and analyzes the predefined subsets. Agents and coverage authoring may
call models. Subset analysis reuses task results rather than executing tasks again.

## Find the results

A successful command exits with status zero and writes:

- `private-run/experiment.json`: completion record linking each case to its correlation result.
- `private-run/analysis/case-NNNN/`: `mutant-scores.json`, `coverage-scores.json`,
  and `correlation.json`, plus saved analysis inputs and coverage evidence.
- `private-run/execution/`: reusable Harbor results, indexed by `execution.json`.
- `private-run/validation.json`: native validation report for a fresh execution.

Cases are analyzed separately. Correlation can be unavailable, for example when
all subset scores are equal; inspect its `missing_score_reason` and the
[correlation guide](correlate.md). If the command fails, keep its output directory
and follow the relevant recovery path below.

## Resume interrupted execution

If execution stopped between agent jobs, rerun the original command with the
same configuration, run ID, and output directory. Completed jobs are checked and
reused. The execution inputs and settings must still match. A package-code change
before analysis begins does not by itself prevent completed-job reuse.

If it stopped **inside a Harbor job**, first complete that job using the
[Harbor recovery instructions](run-evals.md#interrupted-jobs). For this guide's
example, job directories are under
`private-run/execution/cases/case-NNNN/execution-results/agents/agent-NNNN/run-0001`.
Use the original input and output locations, then rerun the original experiment
command. The coordinator does not automatically resume an incomplete Harbor job.

Completed jobs, including completed trials with failing outcomes, are reused;
rerunning the command is not a request to retry those trials. To repeat execution,
[start a fresh run](#start-over).

## Resume interrupted analysis

Once `private-run/execution/execution.json` exists, resume analysis without
invoking agent execution:

```bash
uv run python labs/metric-benchmark/run_experiment.py \
  --benchmark experiment.yml --run-id trial-1 \
  --results private-run/execution --output private-run
```

Keep the experiment settings, run ID, saved evidence, package code, and recorded
dependency versions unchanged when resuming into the same analysis directory.
Completed coverage subsets are checked and reused; unfinished subsets invoke the
coverage command again and may make new model calls. Scores and correlation are
recomputed, accepting identical existing outputs.

For a coverage-command failure, inspect the diagnostic directory named in the
error, including `stdout.log` and `stderr.log`. Restore the required service or
credentials, then retry. Failed attempts remain available for inspection; see
[coverage recovery behavior](compute-coverage.md#inputs-and-behavior).
If the fix changes code or configuration, use a new analysis directory instead.

## Change analysis while keeping execution results

To try different subsets, scoring settings, coverage settings, or analysis code,
use a new output directory and point `--results` at the saved execution root:

```bash
uv run python labs/metric-benchmark/run_experiment.py \
  --benchmark revised-experiment.yml --run-id trial-1 \
  --results private-run/execution --output revised-analysis
```

Keep the case definitions and run ID matching the saved execution. Re-sign edited
inputs with [sha-sign --resign](sha-sign.md) while the complete prepared bundle is
still available. Once signed, saved-results analysis does not require executable
task directories or agent bundles; retain the case metadata, selected analysis
inputs, and native evidence needed by the coverage command. The new directory computes coverage afresh and may make new
model calls; it does not inherit the old directory's coverage cache.

`--results` checks saved indexes and their identities but does **not** repeat
native validation against executable sources. If you need that check, run
[validate_results.py](validate-results.md) with the prepared inputs before analysis.
A saved-results invocation does not create a new `validation.json` report.

## Replay without new coverage calls

Add `--cached-only` to the resume-analysis command above. It requires `--results`
and reuses completed coverage from the **same analysis output directory**. It
fails if any required coverage result is missing or no longer matches; it never
fills that gap with a new invocation. A fresh output directory therefore cannot
be used for cached-only replay. Scoring and correlation still run.

## Start over

Choose a new `--output` directory and a new run ID, and run the basic command
without `--results`. This executes all agents and tasks again and recomputes
coverage. Keep the previous directory as evidence. Use this path when changing
agents, task sources, or execution settings, or when deliberately repeating trials.

If an older partial run refuses to resume because its analysis record pins an
older package implementation, finish execution using
[run_evals.py](run-evals.md) with the original cases, execution settings, and run ID,
setting its `--output` to `private-run/execution`. Once that directory contains
`execution.json`, analyze it in a new output directory as above.

## Try the bundled smoke example

```bash
uv run python labs/metric-benchmark/run_experiment.py \
  --benchmark labs/metric-benchmark/examples/smoke/experiment.yml \
  --run-id smoke-1 --output /tmp/metric-benchmark-smoke
```

This runs six local Harbor trials using two Docker tasks and Oracle/NOP agents,
then analyzes three fixed subsets. It requires Docker with `python:3.12-slim`
and no model credentials. Coverage is **synthetic subset size**: the example
checks the workflow, not metric validity. Use a new output directory for a fresh run.

## Python library

`run_experiment(benchmark, run_id, output, *, results_root=None,
cached_only=False, allow_unsigned=False)` provides the same workflow to Python
callers. For individual stages, use the [standalone tools and libraries](tools.md).
