<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Benchmark tools

These tools consume externally prepared cases. A prepared case contains:

- A baseline agent and its mutant versions, including their executable files and dependencies.
- A description of the agent's intended behavior for coverage computation.
- A fixed collection of evaluation tasks, including their environments and verifiers.
- Named, fixed subsets of those tasks for score and coverage comparisons.
- Manifests connecting these inputs, with relative paths and SHA-256 digests for verification.

## Benchmark config

The following is an **example bundle layout**, including prepared agent and task
files. Directory names are illustrative; the manifests specify their locations.
The directory containing `experiment.yml` is the bundle root:

```text
benchmark/
  experiment.yml
  cases/rho-agent/case.yml
  agents/rho-agent/
    agent.yml
    description.json
    baseline/
      ... agent files and dependencies
    mutants/
      mutant/
        ... mutant agent files and dependencies
      mutant-no-search/
        ... mutant agent files and dependencies
  tasks/workspace/
    tasks.yml
    subsets.json
    inspect-files/
      ... Harbor task files, environment and verifier
    read-config/
      ... Harbor task files, environment and verifier
    search-workspace/
      ... Harbor task files, environment and verifier
```

Every reference has `path`, `sha256` (`sha256:<hex>`), and optional `kind`
(`file` by default, or `directory`). **All paths are relative to the bundle root**,
including references inside nested manifests. Cases can share agent, task and
subset manifests without copying them or using `../`. Absolute paths, traversal,
and symlinks are rejected. Manifests and file inputs are pinned by exact bytes.
Directory digests hash the canonical JSON mapping of relative file paths to
SHA-256 digests using `input_digest(path)`. Moving the bundle preserves its
references and identity. Task membership is explicit; adding a directory does
not add a task.

### Agents: `agent.yml`

This file defines `baseline`, `mutants`, and a pinned `description`. Each agent
version supplies `agent_bundle_id`, `revision`, native `harbor` settings, and
optional pinned `inputs` for its prepared files and dependencies. In the example
layout above, these inputs reference `agents/rho-agent/baseline/` or the
corresponding directory under `agents/rho-agent/mutants/`, with `kind: directory`
and that directory's SHA-256 digest. Listing those files does not install or
launch them; the selected Harbor integration must support doing so.

The agent is the executable attempting the task; the task owns its verifier.
The `harbor` field configures how the local executor invokes that agent through
a Harbor integration (`name` or `import_path`, plus native settings). Prepared
agent contents and dependencies are separate pinned inputs.

The agent's description is a JSON object describing intended behavior for the
coverage tool. The runner passes it through as `case_description`; the configured
metric tool determines how to interpret it. An Ethos-derived description is one
possible input. The synthetic fixture's `required` list is only a test convention,
not a required description schema.

### Tasks: `tasks.yml`

This file defines a fixed `task_inventory` and `task_sources` keyed by task ID.

`tasks.yml` identifies a task benchmark: a fixed collection of Harbor tasks.
Existing task benchmarks can be bundled during prep; tasks need not be newly
authored for this experiment.
The pinned `tasks.yml` identifies the exact collection; there is no separate
collection deployment, benchmark ID or revision block. Individual tasks retain
their IDs and revisions in `task_inventory.tasks`.

Task IDs are stable labels in our manifest. The example uses readable names that
match its task directories; the executor binds each ID to its explicit source
path. A Harbor task directory contains `task.toml`, `instruction.md`, environment
files and a verifier. Currently `task_sources` supports only `kind: directory`.
`kind: file` is for manifests and agent dependencies, not executable task sources.
Archives, Git repositories or remote datasets must be materialized as local task
directories during prep; the runner does not download or unpack them.

#### Subsets: `subsets.json`

The subset manifest sits beside `tasks.yml` and contains
`artifact_type: eval_subset_definitions`, `sampling_parameters` (including the
preparation seed), and `eval_subsets`. Each subset has an `eval_subset_id` (a
readable name or number) and a `task_ids` list. It does not repeat task revisions,
display names, or the task inventory. The selected `tasks.yml` supplies those
task definitions; analysis checks that recorded task IDs and revisions match it.
Unknown task IDs, duplicate IDs, and duplicate memberships are rejected.
Cases and experiments can share prepared subsets. Membership stays fixed;
runtime never samples from the seed.

### Cases: `case.yml`

This file defines a `case_id` and pinned `agent`, `tasks`, and `subsets` manifest
references. It combines an agent and its mutants with a task collection and the
subsets used for analysis. Cases can reuse manifests while selecting different
combinations of agents, tasks and subsets.

### Experiment: `experiment.yml`

The experiment YAML contains `benchmark_id`, `revision`, a list of pinned `cases`
references, `coverage`, and optional `execution`, `scoring`, and `missing_policy`
settings. Each case reference has a relative `path` and `sha256`. This defines the
metric benchmark by combining prepared cases with execution and analysis settings.

The initial backend is `local_harbor`, pinned to Harbor 0.20.0. Its settings are
`n_concurrent_trials` (default 1) and native `environment` configuration (Docker
by default). NeMo Helix is a future backend. Native Harbor settings and coverage
commands are trusted local programs, not a sandbox or secret store. Supply
credentials through the runtime environment, not benchmark YAML committed to Git.

## Reading benchmarks

Unpack a benchmark bundle and pass its `experiment.yml` to the Python library.
From the repository root:

```bash
PYTHONPATH=labs/metric-benchmark uv run python - <<'PYTHON'
from pathlib import Path
from metric_benchmark.config import read_benchmark

benchmark = read_benchmark(Path("benchmark/experiment.yml"))
prepared = benchmark.case("rho-agent")
subsets = prepared.case.load_subsets(prepared.root)
PYTHON
```

`read_benchmark(path)` reads the experiment and verifies its pinned case, agent
and task manifests without opening task sources, agent bundles, subsets, or
descriptions. Load the selected case's subsets or description with
`prepared.case.load_subsets(prepared.root)` or
`prepared.case.load_description(prepared.root)`; each verifies the referenced
artifact's digest.

Use `load_benchmark(path)` from the same module to also verify all task sources
and agent inputs and load every case's subsets and description. Both readers
reject unknown fields, duplicate YAML keys, and non-JSON native settings.
`BenchmarkDefinition`, `CaseManifest`, `AgentManifest`, and `TaskManifest` each
expose their format through `model_json_schema()`.

The [configuration test fixture](../../../tests/fixtures/metric_benchmark/experiment.yml)
uses illustrative rho-agent IDs, an Oracle baseline and two NOP mutant entries.
These are synthetic stand-ins, not implementations of distinct Rho faults. Its coverage
command is a placeholder; the tests supply their own adapter.

Unlike the example bundle layout above, this synthetic fixture uses built-in
agents and does not contain prepared baseline or mutant agent directories.
