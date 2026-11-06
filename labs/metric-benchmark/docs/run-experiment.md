<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# run_experiment.py

Run a prepared benchmark from agent execution through coverage, mutant scoring,
and correlation, or analyze execution results you already have.

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
