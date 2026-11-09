<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Helix file-reading experiment

This directory contains all source inputs for a small illustrative experiment:
one baseline, one mutant, two Harbor tasks and analysis settings. The baseline is
instructed to read files; the mutant is instructed to skip reading and write
`unknown`. Each task checks the answer in `/app/answer.txt`.

The Helix tools and registered-agent format are still proposed interfaces in this
architecture PR. This example shows the complete file layout, but cannot yet run
end to end from this branch. Model, connection, login and worker settings are
placeholders. No real credentials are included.

## Files and what they do

| File | Purpose |
| --- | --- |
| [experiment.yml](experiment.yml) | Names the experiment and links cases, execution and analysis settings. |
| [cases.yml](cases.yml) | Defines case `example` and selects its agent and task manifests. |
| [agents.yml](agents.yml) | Selects `baseline` and `mutant` and points to their separate bundles. |
| [Baseline agent](agents/baseline/agent.yaml), [mutant agent](agents/mutant/agent.yaml) | Complete agent definitions with the same harness/model settings and different instructions. Each directory is uploaded separately. |
| [tasks.yml](tasks.yml) | Lists `inspect-files` and `count-lines` with their revisions and source paths. |
| [inspect-files](tasks/inspect-files/instruction.md), [count-lines](tasks/count-lines/instruction.md) | Task instructions. Each task directory also includes `task.toml`, an environment Dockerfile and input file, a reference solution, and a verifier. |
| [analysis.yml](analysis.yml) | Selects description/subsets, a synthetic coverage command, reward scoring and missing-result handling. |
| [description.json](description.json) | Describes the intended behavior and illustrative mutation. |
| [subsets.json](subsets.json) | Fixes three task subsets: each task separately and both together. |
| [publication.yml](publication.yml) | Publishes the case's agents and tasks; shows optional secret references. |
| [publication-existing-task.yml](publication-existing-task.yml) | Alternative: reuse a verified `inspect-files` task revision and upload `count-lines`. The remote reference is deliberately fake. |
| [execution.yml](execution.yml) | Selects Helix and sets dispatcher, concurrency and sandbox profile. |
| [helix-client.example.yml](helix-client.example.yml) | Shows the client configuration structure with an `.invalid` server address and an explicitly dummy login token. |

All manifest paths resolve from the directory containing `cases.yml`. The three
references in `experiment.yml` resolve from that file's directory. See the
[benchmark format](../../docs/benchmark-format.md) for the shared rules.

## What is specific to Helix?

| Same as a local experiment | Specific to this Helix workflow |
| --- | --- |
| `experiment.yml` and `cases.yml` structure and selection | `agents.yml` selects registered bundles instead of native `harbor` agent configurations. |
| Task IDs, revisions, `tasks.yml` and complete Harbor task directories | Each agent bundle's `agent.yaml` uses the managed-agent definition format. |
| `analysis.yml`, descriptions, fixed subsets and scoring rules | `publication.yml` controls uploading/reusing resources and binding secrets. |
| Input content hashes and result validation | `execution.yml` uses Helix dispatcher and sandbox settings instead of local Docker settings. |
| Collected result layout, trials, attempts and measurements | Helix client settings supply the remote server, login and workspace. |
| Analysis of saved results | `references.json` and run receipts connect experiment names to registered resources and remote runs. |

Task sources are uploaded unchanged. No agent files are added to their images.
The agent's behavior, model and runtime settings belong to its signed definition
and manifest; publication and execution do not override them. Downloading these
registered bundles does not make them executable by the local Harbor backend.

## Customize and use the example

1. **Choose the real destination.** Configure Helix login outside this checkout.
   The dummy client file is for reading, not a working login. Keep real credentials
   out of the experiment. The workflow examples select saved client settings named
   `my-helix` with `--context`.
2. **Choose available resources.** Replace the model placeholder in both agent
   definitions and review the dispatcher/sandbox profiles in `execution.yml`.
   Review the agent runtime options in `agents.yml`. Use `publication.yml` for
   uploads, or replace the fake pinned task reference in the alternative file
   before using it. Reuse still checks against the included local task sources.
3. **Review the experiment.** The task solutions and verifiers are included. The
   mutant is an illustrative instruction change, not a qualified model-behavior
   guarantee. `analysis.yml` includes a complete, local Python coverage command
   that returns the fraction of tasks in each subset. This is a synthetic wiring
   check, not a meaningful coverage metric or a metric-validity experiment.
4. **Sign after customization.** The example is intentionally unsigned. Once
   registered-agent and Helix configuration support is implemented, use
   [input signing](../../docs/sha-sign.md) on `experiment.yml` to record the final
   file hashes. The signer on this base branch does not yet accept those new formats.
5. **Publish and execute.** Follow the
   [workflow command examples](../../docs/helix-architecture.md#example-workflow),
   which use this directory. Publication writes `references.json`; execution selects
   `--case example` and either `--agent baseline` or `--agent mutant` and saves the
   corresponding run receipt.
6. **Collect and analyze.** Collection checks the expected agent/task results and
   writes the standard results directory. Use the
   [validation tool](../../docs/validate-results.md) and
   [analysis workflow](../../docs/run-experiment.md) with the saved results and this
   experiment's analysis settings. The synthetic metric may produce ties or an
   undefined correlation; it provides no evidence of metric quality.
7. **Download sources when wanted.** Use `references.json` or a run receipt to
   download the exact agent/task versions. No additional download YAML is needed.

`references.json`, run receipts and results are generated by the tools. They are
not checked in as fake execution evidence. The source files are complete; live use
still requires implemented tools, real client settings and suitable Helix resources.
