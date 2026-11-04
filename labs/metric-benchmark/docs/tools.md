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

## Publish inputs to Helix

`publish_helix_inputs.py` derives remote artifacts from prepared cases without
changing task or agent definitions. See the [publication command guide](publish-helix-inputs.md)
for prerequisites, packaging settings, output records and replay behavior.

## Execute, collect and import Helix results

Use the [submit](submit-helix-evals.md) and [download](download-helix-results.md)
commands after publication. Collection preserves complete native evidence and
remains `analysis_ready: false`. Import is a separate, offline stage:

```bash
/path/to/helix/.venv/bin/python labs/metric-benchmark/import_helix_results.py \
  --cases /private/experiment/cases.yml --publication /private/publication \
  --collection /private/collected --output /private/results
```

Initial import needs the pinned Helix client and its `tomlkit==0.15.0` dependency
(`uv pip install --python /path/to/helix/.venv/bin/python tomlkit==0.15.0` for the
qualified client revision). It reproduces the platform's image binding on a
separate task copy. It never rewrites native results or prepared source files.
The normal benchmark environment can subsequently validate and analyze the
result without Helix dependencies or network access.

The importer verifies the complete matrix, prepared bundle identity, uploaded
source archive, resolved job target, immutable image binding, native checksum,
and native measurements. It retains the original collection plus the identity
proofs described in the [results format](results-format.md#helix-identity-evidence).
The output is published only after all cases pass validation. Identical retries
verify and reuse the completed output; changed evidence requires investigation.

Qualified mappings currently cover Oracle/NOP controls and explicitly configured
installed Harbor agents with `task_image` bundles, single-step tasks, and one
shared agent/verifier environment. Registered agents, separate verifier images,
and compose images fail closed until their identity transformations are qualified.
Custom adapters must report provider failures as execution errors and use a model
route reachable from the sandbox. A zero verifier reward alone does not establish
that the model ran. Use the platform operator's supported gateway configuration;
keep deployment-specific routes and secret references in private configuration.

The Python entrypoint is `metric_benchmark.helix_import.import_helix_results`.
Its arguments are loaded cases and the publication, collection and output paths.

## Compute mutant scores

See the [`compute_mutant_scores.py` command guide](compute-mutant-scores.md) for
arguments, required inputs, score semantics, and the Python API.

## Compute coverage

See the [`compute_coverage.py` command guide](compute-coverage.md) for
arguments, evidence staging, cached replay, and the Python API.

The [`eval_author_coverage.py` adapter guide](eval-author-coverage.md) covers
the Eval Author integration.

## Correlate the subset scores

See the [`correlate.py` command guide](correlate.md) for
arguments, unavailable pairs, interpretation, and the Python API.
