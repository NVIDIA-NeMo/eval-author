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

## Download Harbor tasks

`download_harbor_tasks.py` downloads and prepares Harbor tasks using the
[remote-source formats](benchmark-format.md#remote-task-sources).
Use the repository environment (`uv sync --locked`), which pins Harbor 0.24.0,
and Git on `PATH`. Initial downloads need network access to the registry and
Git repositories. Task execution additionally needs the configured environment
backend; the example below uses Docker and makes no model calls.
The CLI defaults `GIT_TERMINAL_PROMPT` to `0`; configure Git credentials before
running downloads that need authentication.

Download a registry snapshot without downloading or running its tasks:

```bash
uv run --locked python labs/metric-benchmark/download_harbor_tasks.py registry \
  --output /tmp/harbor-registry.json
```

`--url` selects another HTTP(S) Harbor JSON registry. The snapshot is validated
and written as normalized JSON. Registry responses larger than 32 MiB are rejected
before parsing or publication. An existing file is accepted only when its
contents match the newly fetched snapshot; use a new output to record changes.
Within one invocation, each registry URL is fetched once and each Git ref is
resolved once, so task lists share the same source versions.

Download a task collection, including a collection containing one Git task:

```bash
uv run --locked python labs/metric-benchmark/download_harbor_tasks.py tasks \
  --source labs/metric-benchmark/examples/remote/git-task.yml \
  --output /tmp/remote-task-bundle
```

This produces a `tasks.yml` with paths relative to that output bundle. Local paths
in the authored input resolve from the source file's directory in this standalone
mode. To preserve an existing experiment's bundle-relative paths, use the
benchmark command instead:

```bash
uv run --locked python labs/metric-benchmark/download_harbor_tasks.py benchmark \
  --source labs/metric-benchmark/examples/remote/experiment.yml \
  --output /tmp/remote-benchmark

uv run --locked python labs/metric-benchmark/run_experiment.py \
  --benchmark /tmp/remote-benchmark/experiment.yml \
  --run-id remote-smoke --output /tmp/remote-results
```

The [remote smoke example](../examples/remote/experiment.yml) exercises both a
registry dataset and a directly pinned Git task with Oracle and NOP. Its coverage
adapter is synthetic, and its one-task subsets cannot establish a meaningful
correlation. It demonstrates plumbing rather than coverage quality.

Keep authored experiments in a dedicated directory: the benchmark command copies
the directory containing the experiment file, preserving its relative layout.
The output must be outside that source directory. The source remains unchanged;
provided input pins are verified before any manifests are rewritten. The downloader
accepts missing pins, downloads remote tasks, then signs and checks the complete
output. Copied files must match their authored sources, including unsigned inputs.
Analysis subsets must already exist and reference the selected task IDs.
All cases sharing a task list have their subset memberships checked against its
resolved inventory before that list's task contents are downloaded.

Both task and benchmark commands publish a complete output directory only
after success. A failed download leaves no final bundle. Repeating a successful
command verifies its source request and prepared files without network access.
`--cached-only` additionally requires that an output already exists. Changed or
missing prepared files fail verification; changed source requests require a new
output. The source must leave `prepared-tasks/` unused relative to the cases
directory (or the standalone task manifest's directory), and `preparation.json`
unused relative to the experiment or standalone task manifest's directory.
Concurrent downloads must use distinct output directories; atomic publication does not reserve the destination against concurrent creation.

Each prepared bundle contains:

- The generated local task manifest(s), with content pins, and downloaded task
  directories under `prepared-tasks/` relative to the cases directory.
- One normalized registry snapshot per registry URL, stored with the first task
  list using it, including the requested release entry.
- `preparation.json`: schema version, Harbor version, authored-input file hashes,
  dataset selectors and registry hashes, resolved task Git commits, and the file
  hashes of the complete prepared bundle excluding the receipt itself.
- For the benchmark command, copies of the agent and analysis inputs and the
  signed experiment configuration, ready for the existing stage tools.

The receipt's `registry_sha256` hashes the raw registry response bytes. The
normalized snapshot has its own content hash in the receipt's `files` manifest.

Archive the prepared bundle and its receipt with execution results. The receipt
is a record of preparation, not a publisher signature. Task content hashes enter
the existing execution identity through the generated manifests. Analysis can
still run from signed metadata and saved results without task directories or
registry access.

### Supported sources and limits

The command uses Harbor's Git task downloader and registry models.
It supports HTTP(S) JSON registries whose tasks come from Git. Harbor Hub package
tasks/datasets, local registry path selectors, arbitrary archives, dataset filters,
and partial-download recovery are not supported. Git LFS datasets need separate
qualification before use; the smoke test does not exercise them.

The whole-dataset selection retains task names and rejects duplicates or missing
tasks. Explicit task lists retain author-supplied IDs and integer revisions.
Automatic task-revision tracking across dataset releases and embedding registry
provenance directly in result reports are not implemented. For now provenance is
retained in the prepared bundle; existing reports continue to use case/task IDs
and content-based execution identity. The runner never prepares or downloads
inputs implicitly.

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

## Run the configured experiment

See the [`run_experiment.py` command guide](run-experiment.md) for
arguments, saved-result analysis, resume, the Python API, and a prepared smoke example.
