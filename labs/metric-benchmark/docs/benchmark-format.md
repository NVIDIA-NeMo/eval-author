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
  -> case.yml (one reference per case)
       -> agent.yml -> baseline files, mutant files, description.json
       -> tasks.yml -> task directories, each with its own verifier
       -> subsets.json -> task IDs from the selected tasks.yml
```

Cases select fixed agent, task, and subset manifests. An experiment selects
cases and supplies execution and analysis settings. Reusing a manifest does not
copy or regenerate its contents. Execution results belong in a separate output
directory; they are not prepared inputs in this bundle.

## Recommended directory layout

Use this **recommended layout** when preparing a benchmark. The names below
are examples: replace `rho-agent`, `workspace`, task IDs, and mutant IDs with
those of the case being packaged. Keep agents under `agents/`, task collections
under `tasks/`, and case combinations under `cases/`. Keep `subsets.json` beside
its `tasks.yml` and each agent version in its own directory.

The loader enforces the manifest relationships and reference rules below, not
these directory names. Other layouts are accepted when their references satisfy
the same contract.
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

## References and content pins

Every reference has `path`, `sha256` (`sha256:<hex>`), and optional `kind`
(`file` by default, or `directory`). **All paths are relative to the bundle root**,
including references inside nested manifests. Cases can share agent, task and
subset manifests without copying them or using `../`. Absolute paths, traversal,
and symlinks are rejected. Manifests and file inputs are pinned by exact bytes.
Directory digests hash the canonical JSON mapping of relative file paths to
SHA-256 digests using `input_digest(path)`. Moving the bundle preserves its
references and identity. Task membership is explicit; adding a directory does
not add a task.

Use `/`-separated relative paths without `..`, backslashes, colons, or NUL
characters. The referenced file or directory must exist when verified and match
its declared `kind`. File references use `kind: file` or omit `kind`; task
sources must explicitly use `kind: directory`. Agent inputs may use either.
A digest is `sha256:` followed by 64 lowercase hexadecimal characters.

For example, the `agent` reference inside `cases/rho-agent/case.yml` points to
`agents/rho-agent/agent.yml`, not `../../agents/rho-agent/agent.yml`. Its digest
covers that file's exact bytes, including whitespace and comments.

Directory pins cover relative file names and file contents. They do not cover
permission bits or empty directories. Compute pins using `input_digest(path)`
from `metric_benchmark.config` so prep uses the same algorithm as the reader.

Prepare pins from the leaves upward: agent/task files and the description and
subsets first, then agent/tasks manifests, then case manifests, then the
experiment. Changing a leaf requires updating each referencing digest up to the
experiment. Revision labels do not replace this content verification.

## Agents: `agent.yml`

This file defines `baseline`, `mutants`, and a pinned `description`. Each agent
version supplies `agent_bundle_id`, `revision`, native `harbor` settings, and
optional pinned `inputs` for its prepared files and dependencies. In the example
layout above, these inputs reference `agents/rho-agent/baseline/` or the
corresponding directory under `agents/rho-agent/mutants/`, with `kind: directory`
and that directory's SHA-256 digest. Listing those files does not install or
launch them; the selected Harbor integration must support doing so.

`baseline`, `mutants`, and `description` are required. There must be at least
one mutant, and the baseline and mutants must have distinct `agent_bundle_id`
values within a case. An agent `revision` is a nonempty **string**, so quote
numeric labels such as `'1'`. `inputs` defaults to an empty mapping; built-in
agents can use this when they have no prepared files to reference.

The agent is the executable attempting the task; the task owns its verifier.
The `harbor` field configures how the local executor invokes that agent through
a Harbor integration (`name` or `import_path`, plus native settings). Prepared
agent contents and dependencies are separate pinned inputs.

The agent's description is a JSON object describing intended behavior for the
coverage tool. The runner passes it through as `case_description`; the configured
metric tool determines how to interpret it. An Ethos-derived description is one
possible input. The synthetic fixture's `required` list is only a test convention,
not a required description schema.

## Tasks: `tasks.yml`

Both `task_inventory` and `task_sources` are required. The inventory has a
nonempty `tasks` list. Each task requires a unique `task_id` and a positive
integer `revision`; `name` is optional. The inventory's `artifact_type` is
`task_references` and may be omitted. `task_sources` must contain exactly the
same task IDs, each mapped to a pinned task directory.

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

## Subsets: `subsets.json`

The subset manifest sits beside `tasks.yml` and contains
`artifact_type: eval_subset_definitions`, `sampling_parameters` (including the
preparation seed), and `eval_subsets`. Each subset has an `eval_subset_id` (a
readable name or number) and a `task_ids` list. It does not repeat task revisions,
display names, or the task inventory. The selected `tasks.yml` supplies those
task definitions; analysis checks that recorded task IDs and revisions match it.
Unknown task IDs, duplicate IDs, and duplicate memberships are rejected.
Cases and experiments can share prepared subsets. Membership stays fixed;
runtime never samples from the seed.

`sampling_parameters` and `eval_subsets` are required; `artifact_type` may be
omitted. Include a fixed integer `seed` in `sampling_parameters` as the prep
convention (the loader does not require that specific key). Other sampling
metadata values must be integers or strings. There must be at least one subset,
and each subset must contain at least one task. Subset IDs are strings, including
numbered labels such as `"subset-000001"`. Two subsets may overlap, but cannot
have identical membership, even if their task ordering differs.

## Cases: `case.yml`

This file defines a `case_id` and pinned `agent`, `tasks`, and `subsets` manifest
references. It combines an agent and its mutants with a task collection and the
subsets used for analysis. Cases can reuse manifests while selecting different
combinations of agents, tasks and subsets. All four fields are required;
`agent`, `tasks`, and `subsets` must reference files. Case IDs must be unique
within the experiment. Use the case ID for its directory name by convention;
if the same agent is paired with two task collections, give each combination
a distinct case ID.

## Experiment: `experiment.yml`

The experiment YAML contains `benchmark_id`, `revision`, a list of pinned `cases`
references, `coverage`, and optional `execution`, `scoring`, and `missing_policy`
settings. Each case reference has a relative `path` and `sha256`. This defines the
metric benchmark by combining prepared cases with execution and analysis settings.

| Field | Requirement or default |
| --- | --- |
| `benchmark_id` | Required nonempty string identifying the metric benchmark. |
| `revision` | Required positive integer identifying this experiment definition. |
| `cases` | Required nonempty list of pinned case files; no repeated paths or case IDs. |
| `coverage` | Required metric-tool configuration, described below. |
| `execution` | Optional; defaults to the local Harbor settings below. |
| `scoring` | Optional; defaults to the policy below. |
| `missing_policy` | `error` (default) or `exclude` for analysis. |

`coverage` requires `system_id`, `system_revision` (both nonempty strings), and
`command` (a nonempty list of argument strings). Optional `settings` is a JSON
object, default `{}`; `timeout_seconds` is a positive integer, default `3600`.
The command identifies the metric tool to invoke, not a prepared input reference.
The bundle-relative path rules apply to pinned references; they do not rewrite
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

`load_benchmark()` reads the experiment, verifies the referenced manifests and
all prepared inputs, and loads subsets and descriptions. It checks subset task
IDs against the selected task inventory. It does not execute the agents, verify
that a launch integration works, or validate the metric-specific meaning of the
description. See [Reading benchmarks](tools.md#reading-benchmarks) for the API.

For concrete YAML and JSON syntax, start with the
[example experiment](../../../tests/fixtures/metric_benchmark/experiment.yml)
and follow its manifest references. This is a synthetic configuration fixture:
its Oracle baseline and two NOP mutants have no prepared agent directories,
its task files are supplied by the tests, and its coverage command is a
placeholder. A released bundle using the recommended layout includes the
referenced task and agent files and a working launch configuration.
