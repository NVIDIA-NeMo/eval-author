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

## Compute mutant scores

```bash
uv run python labs/metric-benchmark/compute_mutant_scores.py \
  --benchmark experiment.yml --case case-id \
  --results-root experiment-results --output mutant-scores.json
```

Library: `compute_mutant_scores(raw, subsets, selection)`.
The CLI derives selection from the configured case and `agent_run_id` values in
the selected case’s `results.json`, which must contain one run per agent. No selection file needs to be
authored. Pass the whole experiment directory as `--results-root`; `execution.json`
locates the case selected by `--case`, regardless of its directory name.
Only the experiment YAML with its inline cases, referenced agent and task
manifests, the selected subset manifest, `execution.json`, and the selected
case’s `results.json` are required. Task directories, agent bundles, case descriptions,
and native trial evidence are not needed for this arithmetic stage. The command
checks the index and subset consistency; use `validate_results.py` separately for
native-evidence validation. Programmatic callers pass models to the library.
The score is the equally weighted mean over mutants of the signed mean paired
task degradation. Improvements remain negative. The output includes `diagnostics`
with `code: negative_mutant_score`, subset ID, and negative value.
`mutant_bundle_id` identifies an individual effect; null identifies the subset
aggregate. These flags are for inspection only: they neither fail scoring nor
clip values or exclude correlation pairs. Incomplete required pairs make
the subset unavailable. `attempt_policy: latest` selects by attempt number,
never by reward. Repetitions require a future explicit aggregation policy.
The score context carries the execution’s `run_id` and hashes of the results,
subsets and scoring selection. Coverage uses the same context so correlation can
check that the pairs refer to the same observations. The prepared subset manifest
is unchanged when the execution run ID changes.

