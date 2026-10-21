<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Benchmark tools

These tools consume externally prepared cases. Subset construction is not a
runtime stage. A benchmark is a YAML definition plus its pinned inputs; a run ID
identifies one execution of that benchmark. Prepared subsets can be reused across
runs without modification.

## Benchmark definition

`metric_benchmark.config.load_benchmark(path)` reads YAML, rejects unknown fields
and duplicate mapping keys, rejects non-finite numbers and YAML-only values such
as unquoted dates, and verifies all prepared input digests.
`read_benchmark(path)` parses the same definition without opening referenced files.
Analysis stages use it and verify only the pinned inputs they consume.
Python configuration models also require finite JSON values in native settings.
`BenchmarkDefinition.model_json_schema()` exposes the complete schema.

Each definition contains `schema_version: 1`, `benchmark_id`, `revision`, `cases`,
`execution`, and `missing_policy` (`error` by default, or explicit `exclude`).
Each case supplies:

- `case_id`, a `task_inventory` and `task_sources` keyed by task ID;
- `baseline` and `mutants`, each with `agent_bundle_id`, `revision`, native
  `harbor` agent settings, and optional named `inputs`;
- `case_description` (a JSON object) and `subsets` (a fixed JSON manifest);
- `scoring` policy and `coverage` system ID, revision, command and settings.

File references have `path`, `sha256` (`sha256:<hex>`), and `kind` (`file` or
`directory`). Paths are relative to the YAML file; traversal and symlinks are
rejected. Directory digests hash the canonical JSON mapping of relative file
paths to SHA-256 digests, using `input_digest(path)`. Prepared agent dependencies
can be referenced in native settings by the exact string `{input:NAME}`.

The subset manifest contains `schema_version: 1`,
`artifact_type: eval_subset_definitions`, `benchmark_id`, `task_inventory`,
`sampling_parameters` (including the preparation seed), and `eval_subsets`.
Each subset has `eval_subset_id` and explicit `tasks` with `task_id` and `revision`.
The manifest's membership is authoritative; runtime code never samples from the
seed. Duplicate subsets or tasks and unknown revisions are rejected.

The initial backend is `local_harbor`, pinned to Harbor 0.20.0. Its settings are
`n_concurrent_trials` (default 1) and native `environment` configuration (Docker
by default). NeMo Helix is a future backend. Native Harbor settings and coverage
commands are trusted local programs, not a sandbox or secret store. Supply
credentials through the runtime environment, not benchmark YAML committed to Git.
