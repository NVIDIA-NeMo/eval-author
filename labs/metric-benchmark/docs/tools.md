<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Benchmark tools

These tools consume externally prepared cases. Subset construction is not a
runtime stage. A benchmark is a YAML definition plus its pinned inputs; a run ID
identifies one execution of that benchmark. Prepared subsets can be reused across
runs without modification.

## Experiment and prepared case manifests

Unpack a benchmark bundle and point `--benchmark` at its `experiment.yml`.
The expected package layout is:

```text
benchmark/
  experiment.yml
  cases/
    rho-agent/
      case.yml
      subsets.json
      description.json
      tasks/
        inspect-files/
        read-config/
        search-workspace/
      agents/                 # Prepared baseline/mutant bundles, when required
```

The experiment YAML contains `benchmark_id`, `revision`, pinned `cases` references,
`coverage`, and optional `execution`, `scoring`, and `missing_policy` settings.
`cases` is a list of `{path, sha256}` references to case manifest files; it does not
inline their contents. Changing the metric command or execution settings does not
require changing the prepared case or subset manifests.

Each case manifest supplies:

- `case_id`, a fixed `task_inventory` and `task_sources` keyed by task ID;
- `baseline` and `mutants`, each with `agent_bundle_id`, `revision`, native
  `harbor` settings, and optional pinned `inputs`;
- pinned `case_description` (a JSON object) and `subsets` (a JSON manifest).

Every reference has `path`, `sha256` (`sha256:<hex>`), and optional `kind`
(`file` by default, or `directory`). Experiment references are relative to the
experiment YAML; case references are relative to that case manifest's directory.
Absolute paths, traversal, and symlinks are rejected. Manifests and file inputs
are pinned by exact bytes. Directory digests hash the canonical JSON mapping of
relative file paths to SHA-256 digests using `input_digest(path)`.
The package can move as a unit without changing its references or identity.
Task membership is explicit; adding a directory does not add a task to the experiment.

The subset manifest contains `artifact_type: eval_subset_definitions`, `case_id`,
`task_inventory`, `sampling_parameters` (including the preparation seed), and
`eval_subsets`. Each subset has `eval_subset_id` and explicit tasks with `task_id`
and `revision`. Its case identity and inventory must match the containing case.
It is independent of the experiment's ID and metric settings. Membership is
fixed during prep; runtime never samples from the seed. Duplicate subsets or
tasks and unknown revisions are rejected.

`metric_benchmark.config.read_benchmark(path)` reads the experiment and verifies
its referenced case manifests without opening task sources, agent bundles,
subsets, or descriptions. `load_benchmark(path)` also verifies and loads those
prepared inputs. Both reject unknown fields, duplicate YAML keys, and non-JSON
native settings. `BenchmarkDefinition.model_json_schema()` and
`Case.model_json_schema()` describe the experiment and case formats separately.
Analysis commands verify the selected case's required artifacts without requiring
execution sources. They still need the experiment's referenced case manifests.

The [configuration test fixture](../../../tests/fixtures/metric_benchmark/experiment.yml)
uses illustrative rho-agent IDs and synthetic Oracle/NOP agents. Its coverage
command is a placeholder; the tests supply their own adapter.

The initial backend is `local_harbor`, pinned to Harbor 0.20.0. Its settings are
`n_concurrent_trials` (default 1) and native `environment` configuration (Docker
by default). NeMo Helix is a future backend. Native Harbor settings and coverage
commands are trusted local programs, not a sandbox or secret store. Supply
credentials through the runtime environment, not benchmark YAML committed to Git.
