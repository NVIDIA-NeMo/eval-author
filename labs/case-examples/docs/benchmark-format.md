<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Case example format

The format for the case examples follows the directory layout and reuses the YAML
specifications consumed by the metric benchmark. [See here](../../metric-benchmark/docs/benchmark-format.md)
for details.
The existing manifest schemas remain supported. The
[remote task download workflow](#remote-datasets) extends task sources
while preserving the prepared local manifest format.

## Layers

| Layer | Contents | Existing specification |
| --- | --- | --- |
| Agent | A baseline and its mutants. | [agent.yml](../../metric-benchmark/docs/benchmark-format.md#agents-agentyml) |
| Task benchmark | A set of Harbor tasks. | [tasks.yml](../../metric-benchmark/docs/benchmark-format.md#tasks-tasksyml) |
| Analysis | Selects task subsets and specifies metric measurement and scoring. | [analysis.yml](../../metric-benchmark/docs/benchmark-format.md#analysis-analysisyml) and [subsets](../../metric-benchmark/docs/benchmark-format.md#subsets-subsetsjson) |
| Full case example | Agent + task benchmark + analysis. | [cases.yml](../../metric-benchmark/docs/benchmark-format.md#cases-casesyml) selects agent/task pairs; analysis settings are associated by case ID. |
| Experiment | Case examples plus execution configuration for local or remote execution. Currently only local execution is supported. | [experiment.yml](../../metric-benchmark/docs/benchmark-format.md#experiment-experimentyml) and [execution.yml](../../metric-benchmark/docs/benchmark-format.md#execution-executionyml) |

Agents, task benchmarks, and analysis can be reused in different combinations.
Execution configuration is supplied when composing an experiment.

## Expected layout

```text
labs/case-examples/
  cases.yml
  analysis.yml
  agents/
    <agent-name>/
      agent.yml
      description.json
      baseline/
      mutant-1/
      mutant-2/
      <more-mutants>/
  tasks/
    <benchmark-name>/
      tasks.yml
      subsets.json
      data/
        <task-name>/
          task.toml
          instruction.md
          environment/
          tests/
          solution/               # reference solution, when supplied
```

Paths in manifests resolve from the directory containing `cases.yml`, here
`labs/case-examples/`. Follow the existing
[reference and content-pin rules](../../metric-benchmark/docs/benchmark-format.md#references-and-content-pins).

## Agents

`agents/<agent-name>/agent.yml` identifies the baseline and mutants, their
revisions, Harbor integration settings, and references to their prepared files.
Keep each implementation in its own directory as shown above. Descriptive mutant
names can replace the numbered examples. `description.json` describes intended
behavior and is selected by the analysis configuration. Required fields are
defined in the [agent specification](../../metric-benchmark/docs/benchmark-format.md#agents-agentyml).

## Task benchmarks

Each `tasks/<benchmark-name>/` represents a benchmark consisting of Harbor tasks.
Its prepared `tasks.yml` explicitly lists the selected tasks and their directory paths;
directories alone do not define membership. The
[task specification](../../metric-benchmark/docs/benchmark-format.md#tasks-tasksyml)
defines task IDs, revisions, paths, and content pins.

Each directory under `data/` is one Harbor task: `instruction.md` states the
request, `task.toml` configures the task, `environment/` defines its environment,
and `tests/` contains its verifier. A supplied reference solution belongs in
`solution/`. Agents are stored separately from these task directories.

`subsets.json` contains fixed task memberships using the existing
[subset specification](../../metric-benchmark/docs/benchmark-format.md#subsets-subsetsjson).
Analysis selects the subset manifest for each case; a task benchmark can provide
additional subset manifests when needed.

Repository-authored tasks can be checked in. Remote task contents are downloaded
into a separate prepared bundle. Remote dataset retrieval is independent of where
the agent executes; the current runner still requires local task directories.

### Remote datasets

An authored task manifest can select a versioned Harbor JSON-registry dataset or
list individually pinned Git tasks alongside local tasks. See the
[remote task source format](../../metric-benchmark/docs/benchmark-format.md#remote-task-sources)
for selectors, supported sources, and revision rules.

Use the [download commands](../../metric-benchmark/docs/tools.md#download-harbor-tasks)
to resolve and download the tasks, produce local manifests, and sign the complete
experiment in a separate output directory. Cases, analysis settings, and explicit
subset memberships retain their existing structure. Repeating the download command verifies
the saved bundle without querying the registry.

The downloader retains registry snapshots and resolved Git revisions in the bundle's
receipt. Archive that bundle with results: automatic inclusion of registry
provenance in result reports remains follow-up work. See the
[supported sources and limits](../../metric-benchmark/docs/tools.md#supported-sources-and-limits)
for the supported scope and remaining work.
