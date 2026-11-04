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

`publish_helix_inputs.py` consumes signed cases and `backend: helix`
[execution settings](benchmark-format.md#execution-executionyml). It uploads task
archives, publishes digest-pinned tasksets, and requests native task-image builds.
It does not submit evaluations or wait for builds. The execution stage must check
that the images are ready before submitting the run matrix.

Run publication in an authenticated Helix client environment containing the
`nemo-helix-plugin` and `nemo-evals-plugin` packages. Keep that environment separate
from this repository's benchmark environment: the two can require different
Harbor versions. Use a client checkout/version supplied by your platform operator;
record both its revision and the deployed server version. For a Helix source
checkout with the supported package layout, installation is:

```bash
uv sync --frozen --no-default-groups --package nemo-helix-ext
uv sync --frozen --no-default-groups --package nemo-evals-plugin --inexact
```

The selected context must match the execution endpoint and workspace exactly.
Credentials belong in normal Helix authentication or secret references, never in
prepared cases. Keep deployment YAML, publication outputs, and downloaded evidence
outside the checkout.

The publication YAML maps every prepared case and agent exactly once:

```yaml
agents:
  example:
    baseline:
      bundle_input: runtime
      config_path: agent.yaml
    mutant:
      bundle_input: runtime
      config_path: agent.yaml
```

`bundle_input` names a signed directory in that agent's existing `inputs` map.
The default `registered` transport requires a root-level `nemo-agents-spec-v1`
configuration. Every bundle file must be UTF-8; publication rejects binary files
instead of silently omitting them. The tool registers a content-derived agent
name, uploads its fileset, and downloads it again to verify the complete manifest.
Your deployed runtime must support the selected harness and stage any files that
it needs. Successful registration alone does not establish execution support.

Custom installed runtimes can instead use `transport: task_image`. Preparation
must place the exact bundle in **every** task's environment build context and
configure the Dockerfile to copy it into the image. For example:

```yaml
agents:
  example:
    baseline:
      bundle_input: runtime
      transport: task_image
      image_bundle_path: environment/agents/baseline
      target:
        source: {import_path: installed_module:Agent}
        agent_kwargs: {runtime_path: /opt/agents/baseline}
    mutant:
      bundle_input: runtime
      transport: task_image
      image_bundle_path: environment/agents/mutant
      target:
        source: {import_path: installed_module:Agent}
        agent_kwargs: {runtime_path: /opt/agents/mutant}
```

The imported Harbor adapter must already exist on the dispatcher; its runtime
and dependencies can live in the task image. Those illustrative module names
are not installed by this tool. Publication checks every image-context copy
against its signed bundle. Preparation owns the Dockerfile, runtime installation,
and adapter configuration; execution evidence must establish which image and
target actually ran. This transport supports binary bundle files.

The optional `target` mapping carries native Harbor target settings, such as
`agent_kwargs` and `env_secrets`. Registered transport supplies `source` itself.
For deterministic controls only, omit `bundle_input` and supply
`target: {source: {name: oracle}}` or `nop`.

```bash
/path/to/helix/.venv/bin/python labs/metric-benchmark/publish_helix_inputs.py \
  --cases /private/experiment/cases.yml \
  --execution /private/experiment/execution.yml \
  --publication /private/experiment/publication.yml \
  --context evaluation --output /private/publication --plan-only
```

Inspect `publication.json`, then repeat without `--plan-only`. The output also
contains source snapshots, receipts, and `bindings.json` for execution. Repeating
the same command reuses content-derived resource identities; changing the plan
requires a new output directory. A partial upload can be resumed. Failures exit
nonzero and retain the private receipts already written. Published resources are
not deleted automatically.

Library entrypoints are `plan_publication` and `publish_helix_inputs` in
`metric_benchmark.helix_publication`; the optional SDK adapter is `HelixPublisher`
in `metric_benchmark.helix_publisher`.

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
