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
skips agent execution and consumes its `execution.json` manifest. Its
`benchmark_sha256` must match the current definition and prepared input contents,
including unpinned inputs when `--allow-unsigned` is used. Add
`--cached-only` to also prohibit new coverage invocations. The same configured
experiment can therefore run entirely, resume completed work, or analyze saved
observations without an LLM directing the process.
Resume into an existing analysis directory requires the same benchmark, run ID,
and analysis implementation. After changing analysis code, use a new `--output`
with `--results` pointing at saved executions; this reuses expensive Harbor work
without mixing outputs from different analysis versions. Full orchestration still
requires prepared execution inputs for native validation; standalone scoring and
coverage load only their own inputs.

The orchestrator calls `validate_experiment_results` before coverage work and
writes its experiment-level report to `validation.json` beside `analysis/` and
`execution/`. It freezes input and
implementation identities, and writes each case's `selection.json`,
`coverage-config.json`, `subsets.json`, and `case-description.json` under
`analysis/case-NNNN/`. These preserve the inputs used by the stage libraries. The standalone CLIs read
the benchmark YAML and results directory.
It writes mutant-score, coverage-score and correlation artifacts in
that same directory. `experiment.json` indexes per-case correlations only after
all stages succeed and input/evidence digests are rechecked. Cases are not pooled.
There is no separate record-experiment stage or runtime preparation stage.

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
