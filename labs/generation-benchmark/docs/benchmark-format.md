<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Generation benchmark format

This is the format contract for externally prepared generation benchmark
bundles. The Python reader implements this contract.
[Benchmark tools](tools.md#reading-benchmarks) explains how to load and verify
a bundle. Preparation is external to the experiment.

One experiment composes four files. Cases, execution, and analysis follow the
[metric benchmark format](../../metric-benchmark/docs/benchmark-format.md)
for those objects: paths, content pins, task entries, subset JSON, the coverage
command, Harbor execution settings, and the scoring policy. This page adds the
generation file: the generation adapter and the sidecar pins that adapter reads.
The agent manifest here is a baseline only.

```text
experiment.yml
  -> cases.yml -> agent.yml -> baseline files
               -> tasks.yml -> Harbor task directories
  -> execution.yml
  -> analysis.yml -> description.json and subsets.json, selected by case ID
  -> generation.yml -> agent config YAML, generator config YAML
```

The directory containing `cases.yml` is the input bundle root. References inside
cases, agent and task manifests, analysis case settings, and generation inputs
resolve from that root. The experiment's four file references resolve from the
experiment file's directory. Execution results belong in a separate output
directory. They are not prepared inputs in this bundle.

## Recommended directory layout

Names below are examples. The loader enforces manifest relationships, not these
directory names.

```text
benchmark/
  experiment.yml
  cases.yml
  execution.yml
  analysis.yml
  generation.yml
  agents/author-agent/
    agent.yml
    description.json
    baseline/
      ... agent files and dependencies
  tasks/workspace/
    tasks.yml
    subsets.json
    inspect-files/
      ... Harbor task files, environment and verifier
  configs/
    agent.yaml
    generator.yaml
```

## Experiment: `experiment.yml`

```yaml
benchmark_id: generation-gap
revision: 1
experiment_kind: generation
cases: {path: cases.yml}
execution: {path: execution.yml}
analysis: {path: analysis.yml}
generation: {path: generation.yml}
```

| Field | Requirement |
| --- | --- |
| `benchmark_id` | Required nonempty string. |
| `revision` | Required positive integer. |
| `experiment_kind` | Required. The value is `generation`. |
| `cases`, `execution`, `analysis`, `generation` | Required file references. |

Signed experiments include a `sha256` on each reference. The four files are
signed with [`sha-sign`](tools.md#signing-a-benchmark) in this lab. The metric
signer reads metric experiments.

## Cases, execution, and analysis

`cases.yml`, `execution.yml`, and `analysis.yml` use the metric format for
those files. `cases.yml` is a nonempty list of `case_id`, `agent`, and `tasks`.
`execution.yml` selects Harbor settings; an empty mapping uses the metric
defaults (`local_harbor`, Harbor 0.20.0, one concurrent trial, Docker).
`analysis.yml` selects each case's description and subsets, plus the coverage
command and scoring policy.

Case ids in `analysis.yml` `case_settings` must match `cases.yml` exactly.
Subset membership is fixed before the run. The driver does not resample.

## Agents: `agent.yml`

This file defines `baseline`: `agent_bundle_id`, `revision`, native `harbor`
settings, and optional `inputs`. `revision` is a nonempty string. The intended
behavior description is a JSON object referenced from `analysis.yml`, not from
this manifest. The runner passes that object through as `case_description`.

## Tasks

Task entries match the metric [tasks manifest](../../metric-benchmark/docs/benchmark-format.md#tasks-tasksyml).
During a run the driver may append new tasks to `tasks.generated.yml` beside
the case root, using the same task entry shape. `tasks.yml` stays the prepared
inventory. The loader merges `tasks.generated.yml` when that file is present.

## Generation: `generation.yml`

```yaml
system_id: eval-author
system_revision: '1'
command: [python, /path/to/eval_author_generate.py]
timeout_seconds: 3600
settings:
  eval_author_root: /path/to/eval-author
  source_sha256: sha256:0123...
  process_sha256: sha256:abcd...
inputs:
  agent_config:
    path: configs/agent.yaml
    kind: file
  generator_config:
    path: configs/generator.yaml
    kind: file
```

The command object matches coverage: `system_id`, `system_revision`, `command`,
optional `settings`, and `timeout_seconds` (default 3600).

`inputs` must pin exactly `agent_config` and `generator_config`, each a file.

`settings` must include:

- `process_sha256` — digest of `system_id`, `system_revision`, `command`, and `timeout_seconds`. `generation_process_sha256()` computes it.
- `eval_author_root` — checkout used by the reference adapter.
- `source_sha256` — digest of that checkout's coverage-relevant sources, from `eval_author_coverage.py --print-source-digest`.
- `draft_dir` — relative path inside the generator work cell where **G** deposits one Harbor task directory per new task. The reference adapter copies those directories into the expanded suite. The driver runs the eval agent on the copies.

`task_create_skill_sha256` is optional. When present, it must match `skills/eval-author-task-create` under `eval_author_root`.

Other settings are JSON passed through to the adapter. The reference adapter reads `generation_budget` (`max_new_tasks`, `seed`) and the resolved paths of `agent_config` and `generator_config`.

`generator.yaml` uses `schema: nemo.eval_author.generator.v1` and names the coding-agent command, model, version command, environment, and timeout. `agent.yaml` is the Harbor agent config the reference adapter passes into the generator prompt.

## Validation

Unknown fields and duplicate YAML or JSON keys are rejected. `load_generation_benchmark()` reads the four files, verifies referenced manifests and pins, and loads subsets and the case description.
