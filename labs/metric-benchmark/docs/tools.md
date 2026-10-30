<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Benchmark tools

These tools consume externally prepared cases. A prepared case contains:

- A baseline agent and its mutant versions, including their executable files and dependencies.
- A description of the agent's intended behavior for coverage computation.
- A fixed collection of evaluation tasks, including their environments and verifiers.
- Named, fixed subsets of those tasks for score and coverage comparisons.
- Manifests connecting these inputs, with relative paths and optional SHA-256 digests for verification.

## Benchmark config

The [benchmark format specification](benchmark-format.md) defines the recommended
directory layout, manifest fields, relative paths, and content pins:

- [Agents](benchmark-format.md#agents-agentyml): baseline, mutants and intended behavior.
- [Tasks](benchmark-format.md#tasks-tasksyml): task inventory and source directories.
- [Subsets](benchmark-format.md#subsets-subsetsjson): fixed task memberships.
- [Cases](benchmark-format.md#experiment-and-inline-cases-experimentyml): inline combinations of agents, tasks and subsets.
- [Experiment](benchmark-format.md#experiment-and-inline-cases-experimentyml): case list and execution/analysis settings.

## Signing a benchmark

See the [`sha-sign` command guide](sha-sign.md) for signing, re-signing, checking
pins, and the Python API.

## Reading benchmarks

Unpack a benchmark bundle and pass its `experiment.yml` to the Python library.
From the repository root:

```bash
PYTHONPATH=labs/metric-benchmark uv run python - <<'PYTHON'
from pathlib import Path
from metric_benchmark.config import load_benchmark

benchmark = load_benchmark(Path("benchmark/experiment.yml"))
prepared = benchmark.case("rho-agent")
subsets = benchmark.subsets["rho-agent"]
PYTHON
```

`load_benchmark(path)` is the full entry point: it reads the experiment, verifies
all referenced manifests, task sources and agent inputs, and loads every case's
subsets and description. The returned object holds the experiment definition,
resolved cases, subsets, and descriptions. Loading requires SHA-256 digests by
default. During iteration, explicitly opt in to unsigned inputs:

```python
benchmark = load_benchmark(Path("benchmark/experiment.yml"), allow_unsigned=True)
```

This permits missing pins but still verifies every supplied digest. The lighter
`read_benchmark()` and individual artifact loaders accept the same keyword,
also defaulting to `False`. Benchmark-reading CLIs accept `--allow-unsigned`
for iteration; `sha-sign --check` always requires all pins.

Use `read_benchmark(path)` from the same module when only the experiment and
inline cases and referenced agent/task manifests are needed. It does not open
task sources, agent bundles, subsets, or descriptions. With this lighter reader,
load a selected case's subsets or description through
`prepared.case.load_subsets(prepared.root, allow_unsigned=prepared.allow_unsigned)` or
`prepared.case.load_description(prepared.root, allow_unsigned=prepared.allow_unsigned)`;
each verifies the referenced artifact and requires a digest unless unsigned
loading was explicitly enabled.

The [format specification](benchmark-format.md) describes validation rules.
`BenchmarkDefinition`, `CaseDefinition`, `AgentManifest`, and `TaskManifest` also
expose their format through `model_json_schema()`.

## Validate a results directory

See the [`validate_results.py` command guide](validate-results.md) for arguments,
output behavior, and the Python API. The [validation report](validation-report.md)
defines the output format.

## Execute the eval tasks

See the [`run_evals.py` command guide](run-evals.md) for arguments, the output
layout, completed-job reuse, interrupted-job recovery, and the Python API.
