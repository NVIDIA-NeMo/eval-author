<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Helix experiment file layout

This is a dummy example for reading, not running. It shows all the files that make
up an experiment and how they refer to one another. Agent instructions, task content,
environments, solutions, verifiers and the coverage command are placeholders.
The task scripts deliberately stop with an error. Helix connection and login values
are also fake; no real secrets are included.

## Files and what they do

| File | Purpose |
| --- | --- |
| [experiment.yml](experiment.yml) | Names the experiment and links cases, execution and analysis settings. |
| [cases.yml](cases.yml) | Defines case `example` and selects its agent and task manifests. |
| [agents.yml](agents.yml) | Selects `baseline` and `mutant` and points to their separate bundle directories. |
| [Baseline agent](agents/baseline/agent.yaml), [mutant agent](agents/mutant/agent.yaml) | Show where each agent's model, harness and instructions belong. These are dummy definitions. |
| [tasks.yml](tasks.yml) | Lists two illustrative task IDs, `inspect-files` and `count-lines`, with revisions and paths. |
| [inspect-files](tasks/inspect-files/instruction.md), [count-lines](tasks/count-lines/instruction.md) | Dummy task directories. Each has instructions, `task.toml`, an environment Dockerfile and input file, `solution/solve.sh`, and `tests/test.sh`. |
| [analysis.yml](analysis.yml) | Links description/subsets and shows where coverage and scoring settings belong. The coverage executable path is a placeholder. |
| [description.json](description.json) | Placeholder descriptions of intended behavior, baseline and mutant. |
| [subsets.json](subsets.json) | Shows fixed task subsets: each task separately and both together. |
| [publication.yml](publication.yml) | Settings for publishing the case's agents and tasks, with an optional secret-reference example. |
| [publication-existing-task.yml](publication-existing-task.yml) | Alternative settings for reusing a pinned task. Its remote task reference is fake. |
| [execution.yml](execution.yml) | Illustrative Helix dispatcher, concurrency and sandbox settings. |
| [helix-client.example.yml](helix-client.example.yml) | Dummy client settings with an `.invalid` server address and an explicitly fake token. |

Manifest paths resolve from the directory containing `cases.yml`. The three file
references in `experiment.yml` resolve from its directory. See the
[benchmark format](../../docs/benchmark-format.md) for the shared rules.

## What is specific to Helix?

| Same as a local experiment | Specific to this Helix workflow |
| --- | --- |
| `experiment.yml` and `cases.yml` structure and selection | `agents.yml` selects registered bundles instead of native `harbor` agent configurations. |
| Task IDs, revisions, `tasks.yml` and Harbor task directory layout | Each bundle's `agent.yaml` uses the managed-agent definition format. |
| `analysis.yml`, descriptions, fixed subsets and scoring rules | `publication.yml` controls uploading/reusing resources and binding secrets. |
| Input content hashes and result validation | `execution.yml` uses Helix dispatcher and sandbox settings instead of local Docker settings. |
| Collected result layout, trials, attempts and measurements | Helix client settings supply the server, login and workspace. |
| Analysis of saved results | `references.json` and run receipts link experiment names to Helix resources and runs. |

In a real experiment, task sources are uploaded unchanged. Agent files are uploaded
separately and are never added to task images. Behavior, model and runtime settings
belong to the signed agent definition and manifest. Downloading a registered bundle
does not make it executable by the local Harbor backend.

## How these files fit into the workflow

1. **Define the experiment.** `experiment.yml` selects `cases.yml`, `execution.yml`
   and `analysis.yml`. The case selects `agents.yml` and `tasks.yml`, which point
   to the bundle and task directories shown here.
2. **Publish.** The publication tool reads the prepared cases and publication settings,
   uploads or verifies/reuses resources, and writes `references.json` with their exact
   Helix IDs and versions.
3. **Execute.** The execution tool reads `execution.yml` and `references.json`.
   Select case `example` and agent `baseline` or `mutant`; save each run receipt.
4. **Collect and analyze.** Collection uses the references and run receipts to download
   and check results, then writes the standard results directory. Analysis uses that
   directory with `analysis.yml` and its description/subset files.
5. **Download sources.** Publication references or a run receipt identify which agent
   and task versions to download. No additional source-download YAML is needed.

The [architecture command examples](../../docs/helix-architecture.md#example-workflow)
show these steps. The tools and registered-agent support are proposed interfaces in
this PR. To build a real experiment later, replace every dummy implementation and
setting, configure login outside the checkout, then
[sign the inputs](../../docs/sha-sign.md). The example is intentionally unsigned;
the signer on this base branch does not yet support the proposed Helix formats.

`references.json`, run receipts and results are generated outputs, so they are not
included as dummy evidence here.
