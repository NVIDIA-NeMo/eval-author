<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Helix experiment templates

These files cover agent definitions, publication and execution settings. They are
parts of an experiment, not a complete runnable example. The Helix commands are
proposed interfaces; this architecture PR does not implement them.

## What each file is for

| File | Used for | What to fill in |
| --- | --- | --- |
| [agents.yml](agents.yml) | Defines the baseline and mutant bundles to publish. | Supply the two bundle directories and review their runtime options. Paths resolve from the directory containing `cases.yml`. |
| [agent.yaml](agent.yaml) | Defines an agent's harness, model, instructions and telemetry. | Put a customized copy inside each bundle. Replace the model placeholder and supply the baseline's and mutant's intended behavior and supporting files. |
| [publication.yml](publication.yml) | Configures publication for case `example` and agents `baseline` and `mutant`. | Match these names to your experiment and add secret references only if needed. Empty entries mean no per-agent secret bindings. |
| [publication-existing-task.yml](publication-existing-task.yml) | An alternative publication configuration that reuses an existing task. | Replace the illustrative `inspect-files` reference with a real pinned Helix task version. Keep matching local task sources for verification. Other tasks are uploaded. |
| [execution.yml](execution.yml) | Configures runs on Helix after publication. | Choose available dispatcher and sandbox profiles and the desired concurrency. Connection, login and default workspace come from the Helix client settings selected by `--context`. |

Use one publication configuration per call. Neither publication file defines the
experiment's agent behavior or run settings.

## What you still need

To assemble a complete experiment, supply:

- **`experiment.yml`:** the experiment ID and revision, with references to
  `cases.yml`, `execution.yml` and `analysis.yml`.
- **`cases.yml`:** a case named `example` that links to the agent and task manifests,
  or your own case name used consistently throughout the settings.
- **Agent bundles:** `agents/baseline/` and `agents/mutant/`, each containing its own
  `agent.yaml` and supporting files. The single template here is not two working
  agents and does not define a meaningful mutation by itself.
- **`tasks.yml` and task directories:** the task IDs, revisions and paths, plus each
  task's instructions, `task.toml`, environment/Dockerfile, verifier and other required
  files. Include `inspect-files` if using the existing-task example unchanged.
- **`analysis.yml` and its inputs:** the case description, fixed task subsets, coverage
  tool configuration, scoring settings and missing-result policy. The configured
  coverage tool must be available when you run analysis.
- **Helix access:** saved client connection/login settings, a workspace with the
  selected model and execution profiles, and any secrets the agents require.

The [benchmark format](../../docs/benchmark-format.md) defines the experiment, case,
task and analysis files and their path rules. The registered-agent and Helix settings
in this directory describe the proposed extension in the
[Helix architecture](../../docs/helix-architecture.md).

## How to use these in an experiment

1. **Prepare the files.** Create an input directory such as `prepared/`, with
   `cases.yml` at its root. Copy and customize these templates there. With the paths
   in `agents.yml`, place the bundles at `prepared/agents/baseline/` and
   `prepared/agents/mutant/`. Add the task and analysis files listed above.
2. **Sign the experiment.** Once the files are ready, use
   [input signing](../../docs/sha-sign.md) to record their content hashes. These
   templates deliberately omit those hashes because the files still need editing.
3. **Publish.** Supply the signed cases and your chosen publication settings.
   Publication uploads or verifies/reuses the selected resources and writes
   `references.json`, linking your case, agent and task names to exact Helix IDs
   and versions.
4. **Execute.** Supply `references.json` and your customized `execution.yml`. Select
   case `example` and agent `baseline`, then repeat for `mutant`. Save each run
   receipt, such as `runs/baseline.json` and `runs/mutant.json`.
5. **Collect and analyze.** Supply the references and run receipts to collection.
   It downloads and checks results, then writes the standard results directory.
   Use that directory with your prepared experiment and analysis settings for
   validation, scoring and analysis.
6. **Download sources when wanted.** Use the publication references or a run receipt
   to download the exact agent and task versions from Helix. No additional source
   download YAML is needed.

See the [workflow command examples](../../docs/helix-architecture.md#example-workflow)
for all four tools. Replace their example settings paths with your customized copies.
`references.json`, run receipts and the results directory are generated outputs;
they are not missing input templates you need to write.
