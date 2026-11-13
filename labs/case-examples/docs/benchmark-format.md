<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Case example format

The format for the case examples follows the directory layout and reuses the YAML
specifications consumed by the metric benchmark. [See here](../../metric-benchmark/docs/benchmark-format.md)
for details.
The existing manifest schemas remain supported. The
[remote dataset proposal](#remote-datasets-proposed) extends task sources;
runtime support is proposed separately from this format document.

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
Currently, its `tasks.yml` explicitly lists the selected tasks and their directory paths;
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

Repository-authored tasks can be checked in. Remote task contents would be fetched
into an ignored `data/` directory as proposed below. Remote dataset retrieval is
independent of where the agent executes; the current runner still requires local
task directories.

### Remote datasets (proposed)

Allow `tasks.yml` to reference a versioned Harbor registry dataset instead of
manually listing its tasks. For example, `tasks/terminal-bench/tasks.yml` would
contain:

```yaml
dataset:
  name: terminal-bench
  version: "2.0"
```

This is proposed metric-benchmark syntax, not an existing Harbor configuration
file or an input accepted by the current loader. Harbor already resolves
`terminal-bench@2.0` through its
[registry](https://github.com/harbor-framework/harbor/blob/main/registry.json).
Its [registry model](https://github.com/harbor-framework/harbor/blob/main/src/harbor/models/registry.py)
associates a dataset name and version with task sources, including Git URLs,
commits, and directory paths.

A task manifest would contain exactly one of the existing `tasks` list or the new
`dataset` mapping. `name` and an explicit `version` would be required; floating
selectors such as `latest` would not be accepted. Omit `dataset.registry` to use
Harbor's default registry, or set `dataset.registry: {url: <registry-json-url>}`
to select another remote Harbor registry. The first implementation would select
the entire dataset. Analysis would continue selecting fixed subsets through
`subsets.json`.

Cases would keep referencing `tasks/terminal-bench/tasks.yml` by path. Multiple
named cases could pair different agents with the same dataset. Case IDs,
analysis settings, and experiment composition would retain their existing
structure.

Preparation would use the qualified Harbor client's registry and download
support to resolve and materialize the dataset before execution. It would:

- Record the requested registry, dataset name and version, and the resolved
  registry entry, task inventory, and immutable source revisions.
- Fetch the task directories into `data/` and generate the existing explicit
  task manifest with stable task IDs, revisions, local paths, and content hashes.
  Preserve registry task names as task IDs and reject duplicate IDs.
- Bind the resolved inventory and source record to the prepared benchmark so
  execution and analysis use the same task identities. Signing only the authored
  dataset selector would not pin its resolved contents.
- Reuse verified prepared contents on subsequent runs. Missing tasks or changed
  contents would fail preparation rather than silently reduce or update the
  dataset; updating a dataset would require explicit preparation of a new version
  of the benchmark.

Execution would consume the prepared local task inventory, and analysis would
use its saved metadata and results without querying the registry. Reports would
retain the originating dataset name, version, and resolved source identity
alongside each case's results.

The separate [experimental-support proposal and proof of concept](https://github.com/NVIDIA-NeMo/eval-author/pull/195)
implements registry snapshots, task downloads, and experiment preparation. It also
covers individual tasks referenced by pinned Git sources. Its generated manifests,
source records, and revision mapping are proposed implementation choices for
review. This remote dataset proposal adds no runtime support or dependency changes.
