<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# run_experiment.py

Run the configured benchmark through the execution and analysis stage libraries.

## Usage

```bash
uv run python labs/metric-benchmark/run_experiment.py \
  --benchmark experiment.yml --run-id trial-1 --output private-run
```

## Python library and orchestration

`run_experiment(benchmark, run_id, output, *, results_root=None,
cached_only=False, allow_unsigned=False)` calls the stage libraries directly. `--results <execution-root>`
skips agent execution and consumes its `execution.json` manifest. Case identities
and the run ID must match; analysis settings may change. Task environments and
agent bundle files are not required in this mode. Add `--cached-only` to also
prohibit new coverage invocations.

Resume into an existing analysis directory requires the same experiment settings,
run ID, evidence, package implementation, and recorded dependency versions. After
changing analysis hypotheses or code, use a new `--output` with `--results` pointing at saved executions. The
output records separate `analysis_sha256` and `execution_sha256` identities.

Fresh execution invokes native validation and writes its report to `validation.json`.
Analysis of saved results checks case identities, run IDs, task inventories and
selections, but does not repeat native validation against executable sources. Use
`validate_results.py` separately with the prepared inputs when that check is needed.
Both modes freeze saved evidence in `execution-evidence.json` before analysis and
check it again before publishing completion.

The orchestrator reads the experiment's cases, execution and analysis references
and passes the appropriate models to each stage library. It writes each case's
`selection.json`, `coverage-config.json`, `subsets.json`, and `case-description.json`
under `analysis/case-NNNN/` to preserve the analysis inputs. Standalone scoring and
coverage commands take `--cases` and `--analysis` instead of the whole experiment.
It writes mutant-score, coverage-score and correlation artifacts in
that same directory. `experiment.json` indexes per-case correlations only after
all stages succeed and input/evidence digests are rechecked. Cases are not pooled.
There is no separate record-experiment stage or runtime preparation stage.

## Recovering interrupted execution

The analysis input record is written only after execution completes and its
manifest passes the case/run checks. If execution stops between agent jobs,
rerun the original command; execution verifies and reuses completed jobs even
if the package implementation changed before analysis began. An incomplete
native job first needs [Harbor recovery](run-evals.md#interrupted-jobs).

For an older partial run that already has `experiment-inputs.json`, a package
change can block the coordinator before execution resumes. Finish execution with
the standalone tool, using the original cases, execution settings, and run ID:

```bash
uv run python labs/metric-benchmark/run_evals.py \
  --cases cases.yml --execution execution.yml --run-id trial-1 \
  --output private-run/execution
```

Once `private-run/execution/execution.json` exists, run analysis in a new output
directory with `run_experiment.py --benchmark experiment.yml --run-id trial-1
--results private-run/execution --output new-analysis`. Add `--allow-unsigned`
to these commands if the prepared inputs have missing pins.

## Small, prepared smoke benchmark

`examples/smoke/experiment.yml` references a prepared case with two Docker tasks, Oracle/NOP agents, and
three fixed subsets. Its coverage command deliberately computes **synthetic
subset size**, not Eval Author coverage. It tests wiring and cannot establish
metric validity. Run it from the repository root with:

```bash
uv run python labs/metric-benchmark/run_experiment.py \
  --benchmark labs/metric-benchmark/examples/smoke/experiment.yml \
  --run-id smoke-1 --output /tmp/metric-benchmark-smoke
```

This runs six local Harbor trials and requires Docker with `python:3.12-slim`.
It does not need model credentials. Use a new output directory for fresh execution.
Production benchmarks supply qualified agent bundles, actual case descriptions,
fixed subset manifests, and a configured metric authoring command. The Eval Author
adapter is tested with real native measurement/reporting and fixed authored
observations; fresh model-backed authoring still requires its own runtime setup.
