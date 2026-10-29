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

The [format specification](benchmark-format.md) defines the experiment's three
references: [cases](benchmark-format.md#cases-casesyml),
[execution settings](benchmark-format.md#execution-executionyml), and
[analysis settings](benchmark-format.md#analysis-analysisyml).

## Signing a benchmark

See [`sha-sign`](sha-sign.md) for signing, re-signing, checking pins, and the Python API.

## Reading benchmarks

From the repository root, put `labs/metric-benchmark` on `PYTHONPATH` to import the
libraries. Standalone stage loaders take the cases file and their settings file:

```python
from pathlib import Path
from metric_benchmark.config import load_execution, load_analysis, load_benchmark

execution = load_execution(Path("benchmark/cases.yml"), Path("benchmark/execution.yml"))
analysis = load_analysis(Path("benchmark/cases.yml"), Path("benchmark/analysis.yml"))
subsets = analysis.load_subsets("rho-agent")
experiment = load_benchmark(Path("benchmark/experiment.yml"))
```

`load_execution` verifies executable inputs. `load_analysis` reads only metadata
and settings; subset and description loaders verify the artifacts they read.
`load_cases(path)` reads case/agent/task metadata for full native result validation.
`load_benchmark` composes execution and analysis objects for orchestration; execution
verifies its own inputs before launching jobs. It does not eagerly open executable
inputs, subsets, or descriptions. Use `sha-sign --check` to verify the entire bundle.

Loading requires pins by default. All these entrypoints accept
`allow_unsigned=True` during iteration, still verifying supplied pins. The CLI
spelling is `--allow-unsigned`. Metadata loading requires the referenced YAML
manifests, but analysis does not require their executable agent/task directories.

## Validate a results directory

See the [`validate_results.py` command guide](validate-results.md) for arguments,
output behavior, and the Python API. The [validation report](validation-report.md)
defines the output format.

## Execute the eval tasks

See the [`run_evals.py` command guide](run-evals.md) for arguments, the output
layout, completed-job reuse, interrupted-job recovery, and the Python API.

### Proposed Helix execution

The documentation-only proposal splits remote execution into
[`submit_helix_evals.py`](submit-helix-evals.md) and
[`download_helix_results.py`](download-helix-results.md): submit prepared cases,
then check completion and collect a local results directory. These commands are
not implemented; see the [Helix flow diagram](assets/helix-execution.svg).
