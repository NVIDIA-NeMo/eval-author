<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Benchmark format

This is the format contract for externally prepared metric benchmark bundles.
Use the recommended layout below for new bundles. The Python reader implements
this contract; [Benchmark tools](tools.md#reading-benchmarks) explains how to
load and verify a bundle. Preparation is external to the experiment.

A bundle connects the inputs in this order:

```text
experiment.yml
  -> cases (inline list)
       -> agent.yml -> baseline files, mutant files, description.json
       -> tasks.yml -> task directories, each with its own verifier
       -> subsets.json -> task IDs from the selected tasks.yml
```

Cases select agent, task, and subset manifests. The experiment defines its
cases inline and supplies execution and analysis settings. Reusing a manifest does not
copy or regenerate its contents. Execution results belong in a separate output
directory; they are not prepared inputs in this bundle.

## Recommended directory layout

Use this **recommended layout** when preparing a benchmark. The names below
are examples: replace `rho-agent`, `workspace`, task IDs, and mutant IDs with
those of the case being packaged. Keep agents under `agents/`, task collections
under `tasks/`, and case combinations in `experiment.yml`. Keep `subsets.json` beside
its `tasks.yml` and each agent version in its own directory.

The loader enforces the manifest relationships and reference rules below, not
these directory names. Other layouts are accepted when their references satisfy
the same contract.
The directory containing `experiment.yml` is the bundle root:

```text
benchmark/
  experiment.yml
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

## References and content pins

Every reference requires `path`. `sha256` (`sha256:<hex>`) is optional, as is
`kind` (`file` by default, or `directory`). **All paths are relative to the bundle root**,
including references inside nested manifests. Cases can share agent, task and
subset manifests without copying them or using `../`. Absolute paths, traversal,
and symlinks in reference paths are rejected. When supplied, file digests verify
exact bytes.
Directory digests hash the canonical JSON mapping of relative file paths to
SHA-256 digests using `input_digest(path)`. Moving the bundle preserves its
references and identity. Task membership is explicit; adding a directory does
not add a task.

Use `/`-separated relative paths without `..`, backslashes, colons, or NUL
characters. The referenced file or directory must exist when verified and match
its declared `kind`. File references use `kind: file` or omit `kind`; task
sources are always directories and have no `kind` field in `tasks.yml`. Agent
inputs may use either kind.

Omit `sha256` while iterating. The loader still checks that the referenced path
exists and has the expected kind, but does not hash its contents. When `sha256`
is supplied, a mismatch is an error. A digest is `sha256:` followed by 64 lowercase
hexadecimal characters.

For an established benchmark, supply digests on every reference, including nested
manifests and agent/task inputs, to fix the expected contents. These are content
pins, not cryptographic signatures identifying a publisher. Without pins, the
same experiment YAML can load changed inputs; its configuration identity does
not identify those inputs' contents.

For example, a case's `agent` reference in `experiment.yml` points to
`agents/rho-agent/agent.yml`. Its digest
covers that file's exact bytes, including whitespace and comments.

Directory pins cover relative file names and file contents. They do not cover
permission bits or empty directories. Compute pins using `input_digest(path)`
from `metric_benchmark.config` so prep uses the same algorithm as the reader.

Use [`sha-sign`](tools.md#signing-a-benchmark) to write the pins. It hashes
agent/task files, descriptions and subsets first, writes the hashes into the
agent/tasks manifests, then hashes those manifests and updates the inline case
references in `experiment.yml`. The experiment file has no self-hash. Changing
an input requires re-signing its referencing manifests; revision labels do not
replace content verification.

## Agents: `agent.yml`

This file defines `baseline`, `mutants`, and a `description` reference. Each agent
version supplies `agent_bundle_id`, `revision`, native `harbor` settings, and
optional `inputs` references for its prepared files and dependencies. In the example
layout above, these inputs reference `agents/rho-agent/baseline/` or the
corresponding directory under `agents/rho-agent/mutants/`, with `kind: directory`
and, when pinning the benchmark, that directory's SHA-256 digest. Listing those
files does not install or launch them; the selected Harbor integration must support doing so.

`baseline`, `mutants`, and `description` are required. There must be at least
one mutant, and the baseline and mutants must have distinct `agent_bundle_id`
values within a case. An agent `revision` is a nonempty **string**, so quote
numeric labels such as `'1'`. `inputs` defaults to an empty mapping; built-in
agents can use this when they have no prepared files to reference.

The agent is the executable attempting the task; the task owns its verifier.
The `harbor` field configures how the local executor invokes that agent through
a Harbor integration (`name` or `import_path`, plus native settings). Prepared
agent contents and dependencies are separate input references.

The agent's description is a JSON object describing intended behavior for the
coverage tool. The runner passes it through as `case_description`; the configured
metric tool determines how to interpret it. An Ethos-derived description is one
possible input. The synthetic fixture's `required` list is only a test convention,
not a required description schema.

## Tasks: `tasks.yml`

`tasks.yml` contains one nonempty `tasks` list. Each task specifies its identity
and source together:

```yaml
tasks:
  - task_id: inspect-files
    revision: 1
    path: tasks/workspace/inspect-files
  - task_id: read-config
    revision: 1
    path: tasks/workspace/read-config
```

Each entry requires a unique `task_id`, a positive integer `revision`, and a
bundle-relative `path` to a Harbor task directory. `name` and `sha256` are
optional. `sha-sign` adds the SHA field. There is no `kind` field: all task
sources are directories. The loader derives the task inventory used by scoring
and execution results from this list; authors do not repeat it in the YAML.

Task IDs are stable labels. The example uses readable names matching the task
directories, but each task's explicit `path` determines its location. Each
Harbor task directory contains `task.toml`, `instruction.md`, environment files
and a verifier. The task owns the verifier; the agent attempts the task.

This list can package an existing task benchmark. Archives, Git repositories
and remote datasets must be materialized as local directories during prep;
the runner does not download or unpack them. There is no additional collection
benchmark ID, deployment, or revision block.

## Subsets: `subsets.json`

The subset manifest sits beside `tasks.yml` and contains
`artifact_type: eval_subset_definitions`, `sampling_parameters` (including the
preparation seed), and `eval_subsets`. Each subset has an `eval_subset_id` (a
readable name or number) and a `task_ids` list. It does not repeat task revisions,
display names, or the task inventory. The selected `tasks.yml` supplies those
task definitions; analysis checks that recorded task IDs and revisions match it.
Unknown task IDs, duplicate subset IDs, and repeated task IDs within a subset
are rejected. Identical membership across different subsets is allowed; choosing
which subsets belong in the benchmark is a preparation-time decision.
Cases and experiments can share prepared subsets. Membership stays fixed;
runtime never samples from the seed.

`sampling_parameters` and `eval_subsets` are required; `artifact_type` may be
omitted. Include a fixed integer `seed` in `sampling_parameters` as the prep
convention (the loader does not require that specific key). Other sampling
metadata values must be integers or strings. There must be at least one subset,
and each subset must contain at least one task. Subset IDs are strings, including
numbered labels such as `"subset-000001"`. Subsets may overlap.

## Experiment and inline cases: `experiment.yml`

The experiment defines its cases directly. Each case requires a unique `case_id`
and `agent`, `tasks`, and `subsets` file references. There is no separate
`case.yml`. Cases can reuse manifests while choosing different combinations of
agents, tasks and subsets.

This unsigned example shows the full shape. The coverage command is illustrative;
replace it with the installed metric tool's command:

```yaml
benchmark_id: rho-metric-validity
revision: 1
cases:
  - case_id: rho-agent
    agent:
      path: agents/rho-agent/agent.yml
    tasks:
      path: tasks/workspace/tasks.yml
    subsets:
      path: tasks/workspace/subsets.json
coverage:
  system_id: eval-author
  system_revision: '1'
  command: [python, /path/to/coverage-adapter.py]
```

When the same agent is paired with two task collections, give each combination
a distinct case ID. `sha-sign` adds SHA fields beside each reference's `path`;
there is no SHA on the inline case itself.

| Field | Requirement or default |
| --- | --- |
| `benchmark_id` | Required nonempty string identifying the metric benchmark. |
| `revision` | Required positive integer identifying this experiment definition. |
| `cases` | Required nonempty list of inline cases, with distinct case IDs. |
| `coverage` | Required metric-tool configuration, described below. |
| `execution` | Optional; defaults to the local Harbor settings below. |
| `scoring` | Optional; defaults to the policy below. |
| `missing_policy` | `error` (default) or `exclude` for analysis. |

`coverage` requires `system_id`, `system_revision` (both nonempty strings), and
`command` (a nonempty list of argument strings). Optional `settings` is a JSON
object, default `{}`; `timeout_seconds` is a positive integer, default `3600`.
The command identifies the metric tool to invoke, not a prepared input reference.
The bundle-relative path rules apply to input references; they do not rewrite
arbitrary command arguments or native Harbor settings.

The default `scoring` policy is `metric: reward`,
`direction: higher_is_better`, `completed_statuses: [succeeded]`, and
`attempt_policy: only`. The accepted alternatives are `lower_is_better` for
`direction` and `latest` for `attempt_policy`. `completed_statuses` is a nonempty
list of status strings. These are analysis settings; the local execution stage
runs one trial per agent/task pair.

The initial backend is `local_harbor`, pinned to Harbor 0.20.0. Its settings are
`n_concurrent_trials` (default 1) and native `environment` configuration (Docker
by default). NeMo Helix is a future backend. Native Harbor settings and coverage
commands are trusted local programs, not a sandbox or secret store. Supply
credentials through the runtime environment, not benchmark YAML committed to Git.

## Validation and examples

Manifest fields have strict types: unknown fields, duplicate YAML or JSON keys,
and non-finite JSON numbers are rejected. Native `harbor`, `coverage.settings`,
and `execution.environment` values must be JSON-compatible; quote YAML date-like
strings. There is no `schema_version` field. Revisions identify experiment,
agent, task, or metric-tool inputs rather than versions of this file format.

`load_benchmark()` reads the experiment and its inline cases, verifies the referenced manifests and
all prepared inputs (checking any supplied digests), and loads subsets and
descriptions. It checks subset task IDs against the selected task inventory. It does not execute the agents, verify
that a launch integration works, or validate the metric-specific meaning of the
description. See [Reading benchmarks](tools.md#reading-benchmarks) for the API.

For concrete YAML and JSON syntax, start with the
[example experiment](../../../tests/fixtures/metric_benchmark/experiment.yml)
and follow its manifest references. This is a synthetic configuration fixture:
its Oracle baseline and two NOP mutants have no prepared agent directories,
its task files are supplied by the tests, and its coverage command is a
placeholder. A released bundle using the recommended layout includes the
referenced task and agent files and a working launch configuration.
