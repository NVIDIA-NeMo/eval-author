<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Benchmark format

One experiment composes three files. Cases define the agents and tasks; execution
and analysis settings do not refer to cases themselves.

```text
experiment.yml
  -> cases.yml -> agent.yml -> baseline and mutant files
               -> tasks.yml -> Harbor task directories
  -> execution.yml
  -> analysis.yml -> description.json and subsets.json, selected by case ID
```

Preparation happens outside the experiment. Agent and task files are materialized
locally before execution; analysis can use case metadata and saved results without
those executable files. The [tool guides](tools.md) explain the library and CLI interfaces.

## Experiment: `experiment.yml`

```yaml
benchmark_id: rho-metric-validity
revision: 1
cases: {path: cases.yml}
execution: {path: execution.yml}
analysis: {path: analysis.yml}
```

All five fields are required. `benchmark_id` is a nonempty string; `revision` is a
positive integer. The three references must name files. They are relative to the
experiment file's directory. Signed experiments include a `sha256` on each reference.
The experiment's identity and revision describe the composition; they do not
invalidate execution reuse when only analysis changes.

## Cases: `cases.yml`

```yaml
- case_id: rho-agent
  agent: {path: agents/rho-agent/agent.yml}
  tasks: {path: tasks/workspace/tasks.yml}
```

The document is a nonempty list, with unique case IDs. Every case selects an agent
manifest and a task manifest; there is no `inputs` wrapper and no analysis settings
in this file. Pairing the same agent with a different task collection uses another
case ID. Shared manifests need not be copied.

The directory containing `cases.yml` is the input bundle root. All references in
cases, agent/task manifests, and analysis case settings resolve from that root,
regardless of the settings file's location. The experiment's three file references
are resolved from its own directory. This gives standalone stage commands the
same interpretation of paths as the orchestrator.

## Agents: `agent.yml`

```yaml
baseline:
  agent_bundle_id: baseline
  revision: '1'
  harbor: {name: oracle}
mutants:
  - agent_bundle_id: mutant
    revision: '1'
    harbor: {name: nop}
```

These built-in agents illustrate the format, not a real mutation experiment.
Each version has a distinct nonempty `agent_bundle_id`, a string `revision`, native
`harbor` settings, and optional `inputs` references to files or directories. A real
agent's Harbor integration can reference a named dependency using `{input:NAME}`
in its settings. References describe prepared dependencies; they do not install them.
The selected integration must support loading them. There must be at least one mutant.
The intended-behavior description belongs in analysis settings, not this manifest.

## Tasks: `tasks.yml`

```yaml
tasks:
  - task_id: inspect-files
    revision: 1
    path: tasks/workspace/inspect-files
  - task_id: read-config
    revision: 1
    path: tasks/workspace/read-config
```

The task list is nonempty. IDs are unique, revisions are positive integers, and
`name` and `sha256` are optional. Each path identifies a Harbor task directory,
including its environment and verifier. Task sources are always directories;
there is no `kind` field on these entries. The inventory is derived from this
manifest and is not repeated in the experiment. Adding a directory does not add
a task. Preparation must materialize archives or remote datasets first.

### Remote task sources

Authored task manifests can select a Harbor JSON-registry dataset:

```yaml
dataset:
  name: terminal-bench
  version: "2.0"
```

The entire dataset is selected. An explicit release version is required; `latest`
and `head` are rejected. Omit `registry` to use Harbor's default JSON registry, or
add `registry: {url: https://example.org/registry.json}` inside `dataset`. The downloader
supports Git-backed entries in JSON registries, not Harbor Hub package
datasets. The task manifest contains either `dataset` or `tasks`, never both.

An explicit task list can mix existing local entries with individual Git tasks:

```yaml
tasks:
  - task_id: hello-world
    revision: 1
    source:
      git_url: https://github.com/laude-institute/harbor.git
      git_commit_id: ec6618290a90aa4ba2cfd08bd91b2f8a1afa760a
      path: examples/tasks/hello-world
```

`source` replaces the local `path` field on a remote entry. Its path is relative
to the Git repository and its commit must be a full 40-character lowercase SHA.
An optional entry-level `sha256` verifies the downloaded task directory using the
existing content-pin rules. HTTP(S) Git URLs are supported; `file://` is also
accepted for local Git fixtures. URL credentials must come from the environment,
not embedded user/password fields.

These are download inputs. The stage loaders, signer, and experiment runner
continue to consume the explicit local task format above. Run the
[Harbor task download command](tools.md#download-harbor-tasks) first; it resolves upstream
Git refs in registry entries to immutable commits, downloads the tasks, and
produces a separate signed experiment bundle. Case names and analysis settings
are preserved. Registry refs that match different commits are rejected; use a
fully qualified ref or commit to disambiguate. Registry task names become task IDs;
dataset-generated integer task revisions start at `1`. The exact upstream version,
commit, and content hashes are retained separately rather than encoded as an integer revision.

The design keeps resolution explicit so execution and saved-result analysis do
not query a potentially changed registry. Reuse the prepared bundle to repeat an
experiment; prepare a new output and increment the benchmark revision when
adopting changed upstream inputs. See the
[supported sources and limits](tools.md#supported-sources-and-limits).

## Execution: `execution.yml`

```yaml
n_concurrent_trials: 4
environment:
  type: docker
```

An empty mapping selects defaults: `backend: local_harbor`, `harbor_version: 0.24.0`,
`n_concurrent_trials: 1`, and Docker. No other backend or Harbor version is currently
supported. Install that version in the execution environment; the YAML does not
install it, and execution rejects a different installed version. The environment
mapping accepts native Harbor environment settings.
Execution always runs each case's baseline then mutants against its complete task
inventory, with one attempt per pair and no automatic retries. That matrix is
already defined by the cases and is not configured a second time.

When upgrading from Harbor 0.20.0, update any explicit `harbor_version` to `0.24.0`
and choose a new execution output directory. The runtime version is part of the
execution identity, so completed jobs from the old runtime cannot be resumed as
0.24.0 jobs.

## Analysis: `analysis.yml`

```yaml
case_settings:
  rho-agent:
    description: {path: agents/rho-agent/description.json}
    subsets: {path: tasks/workspace/subsets.json}
coverage:
  system_id: eval-author
  system_revision: '1'
  command: [/path/to/venv/bin/python, /path/to/coverage-adapter.py]
scoring:
  metric: reward
  direction: higher_is_better
missing_policy: error
```

`case_settings` must contain exactly the configured case IDs. Each entry references
a description JSON object and a prepared subset manifest. The coverage tool defines
the description's contents; an Ethos-derived description is one possible input.
The coverage command above is illustrative and must name an installed tool.

Coverage requires `system_id`, `system_revision`, and a nonempty `command` argument
list whose first argument is an absolute executable path. Optional `settings` is a
JSON object and `timeout_seconds` defaults to 3600.
Scoring defaults to `metric: reward`, `direction: higher_is_better`,
`completed_statuses: [succeeded]`, and `attempt_policy: only`. Alternatives include
`lower_is_better` and `latest` (select the highest attempt, not the best reward).
`missing_policy` is `error` by default or `exclude`. Analysis selects from recorded
measurements; execution preserves all native reward metrics independently of this policy.

### Subsets: `subsets.json`

A subset manifest contains `artifact_type: eval_subset_definitions` (optional),
`sampling_parameters`, and a nonempty `eval_subsets` list. Each entry supplies a
unique string `eval_subset_id` and nonempty `task_ids`. Task IDs come from the selected
task manifest; unknown or repeated IDs within one subset are rejected. Subsets may
overlap, and different subset IDs may have identical memberships.

Preparation records a fixed integer seed in `sampling_parameters`; other metadata
values may be integers or strings. Runtime consumes the explicit memberships and
never samples from the seed. Analysis checks the recorded task identities and revisions
against the case metadata.

## References and content pins

References require `path`, optionally `sha256` (`sha256:` plus 64 lowercase hex digits)
and `kind` (`file` by default, or `directory` for agent dependencies). Use relative
POSIX paths without `..`, backslashes, colons, or NUL characters. Absolute paths,
symlinks, and special files are rejected. Settings and manifest references name files.

File pins cover exact bytes. Directory pins hash a canonical map of relative file
paths to content digests; permission bits and empty directories are not covered.
Use [`sha-sign`](sha-sign.md) to add or update pins from leaves to the experiment.
It preserves comments and formatting while editing SHA fields. Pins identify content,
not a publisher. Omit pins while iterating and pass `--allow-unsigned`; supplied
pins are still verified. `sha-sign --check` requires and checks every reference.

## Stage boundaries and reuse

`load_cases` verifies the case and agent/task metadata without opening task or
agent files. `load_execution` additionally validates executable inputs.
`load_analysis` reads case metadata and analysis settings; its artifact loaders
open subsets or descriptions only when needed. `load_benchmark` composes stage
objects without executing or requiring executable inputs. Full bundle verification
is provided by `sha-sign --check`.

Execution identity covers the resolved case definitions, executable content pins
(or actual contents during unsigned iteration), and effective execution settings.
It excludes analysis settings, descriptions, subsets, and experiment labels/revision.
Signing unchanged inputs preserves both execution and case identities, so saved
jobs remain reusable and their results remain valid. Unsigned case identities
require executable inputs to compute missing content digests; sign the bundle
before removing those inputs for analysis elsewhere.
Reformatting an unsigned manifest alone does not change its execution meaning.
Analysis records its settings and inputs alongside the selected execution evidence.
Use a new analysis output directory for a new hypothesis; saved executions remain
unchanged. Analysis can choose another recorded reward metric without rerunning tasks.

Fields have strict types; unknown fields, duplicate YAML/JSON keys, non-finite
numbers, and unquoted YAML dates in native settings are rejected. Credentials belong
in the runtime environment, not in committed configuration. Native Harbor settings
and coverage commands run trusted local code.

## Examples and migration

The [example experiment](../../../tests/fixtures/metric_benchmark/experiment.yml)
links all three files. It is a synthetic configuration fixture; tests supply its
local task sources and its coverage command is a placeholder. Released benchmarks
must include executable inputs and a working metric command.

This replaces the experimental inline-case format. Move the old experiment's
`cases` list into `cases.yml`, move `execution` into `execution.yml`, and move
coverage/scoring/missing policy into `analysis.yml`. Move each case's subsets and
its agent manifest's description into `analysis.case_settings[case_id]`, then sign
again. Existing execution artifacts using `benchmark_sha256` are not silently
converted; preserve them separately or explicitly migrate and validate their provenance.
