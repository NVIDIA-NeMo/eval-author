<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Benchmark tools

These tools consume externally prepared cases. Subset construction is not a
runtime stage. A benchmark is a YAML definition plus its pinned inputs; a run ID
identifies one execution of that benchmark. Prepared subsets can be reused across
runs without modification.

## Experiment and prepared manifests

Unpack a benchmark bundle and point `--benchmark` at its `experiment.yml`.
The directory containing that experiment file is the bundle root:

```text
benchmark/
  experiment.yml
  cases/rho-agent/case.yml
  agents/rho-agent/
    agent.yml
    description.json
  tasks/workspace/
    tasks.yml
    subsets.json
    inspect-files/
    read-config/
    search-workspace/
```

The experiment YAML contains `benchmark_id`, `revision`, pinned `cases` references,
`coverage`, and optional `execution`, `scoring`, and `missing_policy` settings.
Each case reference has a relative `path` and `sha256`.

- **`case.yml`:** `case_id` and pinned `agent`, `tasks`, and `subsets` manifest references.
- **`agent.yml`:** `baseline`, `mutants`, and a pinned `description`. Each agent version
  supplies `agent_bundle_id`, `revision`, native `harbor` settings, and optional
  pinned `inputs` for its prepared dependencies.
- **`tasks.yml`:** a fixed `task_inventory` and `task_sources` keyed by task ID.
- **Subset manifest:** the complete list of fixed task subsets for the case.

Every reference has `path`, `sha256` (`sha256:<hex>`), and optional `kind`
(`file` by default, or `directory`). **All paths are relative to the bundle root**,
including references inside nested manifests. Cases can share agent, task and
subset manifests without copying them or using `../`. Absolute paths, traversal,
and symlinks are rejected. Manifests and file inputs are pinned by exact bytes.
Directory digests hash the canonical JSON mapping of relative file paths to
SHA-256 digests using `input_digest(path)`. Moving the bundle preserves its
references and identity. Task membership is explicit; adding a directory does
not add a task.

`tasks.yml` identifies a task benchmark: a fixed collection of Harbor tasks.
`experiment.yml` defines the metric benchmark, combining those task collections
with agents, mutants and analysis settings. Existing task benchmarks can be
bundled during prep; tasks need not be newly authored for this experiment.
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

The subset manifest sits beside `tasks.yml` and contains
`artifact_type: eval_subset_definitions`, `sampling_parameters` (including the
preparation seed), and `eval_subsets`. Each subset has an `eval_subset_id` (a
readable name or number) and a `task_ids` list. It does not repeat task revisions,
display names, or the task inventory. The selected `tasks.yml` supplies those
task definitions; analysis checks that recorded task IDs and revisions match it.
Unknown task IDs, duplicate IDs, and duplicate memberships are rejected.
Cases and experiments can share prepared subsets. Membership stays fixed;
runtime never samples from the seed.

The agent is the executable attempting the task; the task owns its verifier.
The `harbor` field configures how the local executor invokes that agent through
a Harbor integration (`name` or `import_path`, plus native settings). Prepared
agent contents and dependencies are separate pinned inputs. The Oracle/NOP
example illustrates orchestration; it does not implement installing and launching
a prepared Rho bundle. Such a bundle needs its launch integration wired into the
local executor.

The agent's description is a JSON object describing intended behavior for the
coverage tool. The runner passes it through as `case_description`; the configured
metric tool determines how to interpret it. An Ethos-derived description is one
possible input. The synthetic fixture's `required` list is only a test convention,
not a required description schema.

`metric_benchmark.config.read_benchmark(path)` reads and verifies case, agent and
task manifests without opening task sources, agent bundles, subsets, or descriptions.
`load_benchmark(path)` also verifies and loads those prepared inputs. Both reject
unknown fields, duplicate YAML keys, and non-JSON native settings.
`BenchmarkDefinition`, `CaseManifest`, `AgentManifest`, and `TaskManifest` each
expose their format through `model_json_schema()`.
Analysis commands need these referenced manifests and verify only the selected
case's required artifacts; they do not require execution sources.

The [configuration test fixture](../../../tests/fixtures/metric_benchmark/experiment.yml)
uses illustrative rho-agent IDs, an Oracle baseline and two NOP mutant entries.
These are synthetic stand-ins, not implementations of distinct Rho faults. Its coverage
command is a placeholder; the tests supply their own adapter.

The initial backend is `local_harbor`, pinned to Harbor 0.20.0. Its settings are
`n_concurrent_trials` (default 1) and native `environment` configuration (Docker
by default). NeMo Helix is a future backend. Native Harbor settings and coverage
commands are trusted local programs, not a sandbox or secret store. Supply
credentials through the runtime environment, not benchmark YAML committed to Git.
