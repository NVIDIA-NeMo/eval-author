<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Helix experiment YAML example

This directory shows a complete set of illustrative YAML configurations and how
they fit together. It contains no agent bundles, tasks, analysis data or results.
Paths and remote IDs may point to things that do not exist. Nothing here is intended
to run, and the client settings contain no real secrets.

## How the files fit together

```text
experiment.yml
├── cases.yml
│   └── case_id: example
│       ├── agents.yml → baseline and mutant bundle paths (not included)
│       └── tasks.yml → task IDs and source paths (not included)
├── execution.yml
└── analysis.yml → description and subset files (not included)

publication.yml + cases.yml → publication → references.json
execution.yml + references.json + case/agent selection → execution → run receipts
references.json + run receipts → collection → standard results directory
```

| File | What it tells you |
| --- | --- |
| [experiment.yml](experiment.yml) | The experiment's ID and revision, and which cases, execution and analysis files it uses. |
| [cases.yml](cases.yml) | Case `example` uses the agent manifest `agents.yml` and task manifest `tasks.yml`. |
| [agents.yml](agents.yml) | The case has agents named `baseline` and `mutant`. Each has a separate bundle path and a configuration filename within that bundle. |
| [tasks.yml](tasks.yml) | The benchmark contains tasks named `inspect-files` and `count-lines`, with source paths and revisions. |
| [analysis.yml](analysis.yml) | Case `example` has description/subset references and settings for coverage, scoring and missing results. |
| [publication.yml](publication.yml) | Publication settings keyed by the same case and agent names, with an optional secret-reference example. |
| [publication-existing-task.yml](publication-existing-task.yml) | Alternative publication settings mapping a task name to a pinned task already in Helix. The reference is fake. |
| [execution.yml](execution.yml) | Settings for running on Helix: dispatcher, concurrency and sandbox profile. |
| [helix-client.example.yml](helix-client.example.yml) | How client settings identify a server, login and workspace. The address and token are dummy values. |

Manifest paths resolve from the directory containing `cases.yml`. The three
references in `experiment.yml` resolve from its directory. The
[benchmark format](../../docs/benchmark-format.md) explains these shared rules.

## How case and task names map to Helix IDs

**Before publication, you use experiment names and source paths.** `cases.yml`
selects case `example`. Its agent manifest supplies `baseline` and `mutant`; its
task manifest supplies `inspect-files` and `count-lines`. These names identify the
parts of your experiment. They are not Helix resource IDs.

**Publication writes the mapping.** It uploads each agent separately, uploads or
verifies/reuses tasks, and creates a pinned taskset containing the case's tasks.
It writes the resulting Helix IDs and exact versions to `references.json`.
Conceptually, that file records:

| Experiment selection | Helix reference recorded by publication |
| --- | --- |
| Case `example`, agent `baseline` | Registered baseline agent ID and exact version |
| Case `example`, agent `mutant` | Registered mutant agent ID and exact version |
| Case `example`, task `inspect-files` | Registered task ID and exact version |
| Case `example`, task `count-lines` | Registered task ID and exact version |
| Case `example`, full benchmark | Taskset ID and exact version containing those task references |

The mapping also records content hashes for verification. A case remains an
experiment grouping; it does not need its own Helix resource ID. In the existing-task
publication example, `tasks.example.inspect-files` supplies the remote task reference
instead of requesting an upload. A real publication still checks it against prepared
local sources.

**Execution uses the mapping.** You select `--case example --agent baseline` and
supply `references.json` plus `execution.yml`. The tool looks up the registered
baseline and that case's taskset, then runs the agent against every task in it.
Repeat with `--agent mutant` for the mutant. You do not enter the remote IDs manually.

**Results keep the link.** Run receipts record run IDs and the exact agent/task
references used. Collection uses these to download results and checks the expected
agents and tasks from `references.json`. Source downloads use the same resource
references. The original experiment names remain available for analysis.

## What changes when using Helix?

- **Unchanged:** experiment/case structure, task IDs and source layout, analysis
  settings, fixed subsets, scoring rules and the standard collected results format.
- **Different agent selection:** `agents.yml` selects registered bundles instead of
  native local Harbor agent configurations. Their behavior and model settings belong
  in the bundle; publication and execution do not override them.
- **Additional publication step:** publication settings control upload/reuse and
  secret bindings. Generated `references.json` connects experiment names to Helix IDs.
- **Different run settings:** `execution.yml` describes Helix workers and sandboxes
  instead of local execution settings. Shared Helix client settings provide the
  connection, login and default workspace.
- **Remote results:** execution returns run receipts; collection downloads results
  into the same format used by local experiments. Agent and task sources can also
  be downloaded by their recorded IDs and exact versions.

Tasks are uploaded unchanged; agent files are never injected into task images.
Downloading a registered agent does not automatically make it runnable locally.

See the [architecture command examples](../../docs/helix-architecture.md#example-workflow)
for the proposed publication, execution, collection and download commands. These
interfaces are not implemented in this base PR. A real experiment would supply the
referenced sources and analysis data, replace the dummy settings, and sign its
inputs. `references.json`, run receipts and results are generated outputs, so they
are not included here.
