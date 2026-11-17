<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Generate experiment configs

The optional [`create_benchmark_config.py`](../create_benchmark_config.py) tool
generates `experiment.yml` and supporting configuration files from prepared cases
and your execution settings. Running a complete benchmark can be expensive;
generate a reduced configuration to check infrastructure or obtain early signals,
or preserve the complete benchmark composition.

Generation copies selected inputs, applies configuration overrides, writes and
validates content pins, and reports planned trial counts. It starts no containers,
installs no agents, downloads no tasks, and makes no model calls. You can still
write the same YAML by hand. The existing runner consumes both forms identically.

Run the following from the repository root after `uv sync --locked`. This example
uses the repository's public Oracle/NOP case and synthetic coverage command;
it does not require model credentials.

```bash
uv run --locked python labs/metric-benchmark/create_benchmark_config.py \
  --benchmark-source labs/metric-benchmark/examples/smoke \
  --execution labs/metric-benchmark/examples/smoke/execution.yml \
  --selection labs/metric-benchmark/examples/smoke/selection.yml \
  --benchmark-id smoke-reduced \
  --output /tmp/smoke-reduced

uv run --locked python labs/metric-benchmark/run_experiment.py \
  --benchmark /tmp/smoke-reduced/experiment.yml \
  --run-id smoke-1 --output /tmp/smoke-reduced-results
```

This selects the baseline, one mutant, and one task: **two trials**. The second
command requires Docker. Its one analysis subset checks plumbing; it cannot
establish a meaningful coverage correlation. Use new output directories when
repeating the example.

Omit `--selection` to generate a configuration preserving all cases, mutants,
tasks, and predefined subsets:

```bash
uv run --locked python labs/metric-benchmark/create_benchmark_config.py \
  --benchmark-source labs/metric-benchmark/examples/smoke \
  --execution labs/metric-benchmark/examples/smoke/execution.yml \
  --benchmark-id smoke-complete \
  --output /tmp/smoke-complete
```

That example produces six trials: the baseline and two mutants on two tasks.
Execution remains a separate `run_experiment.py` invocation.

## Inputs and selection

The source directory contains `cases.yml`, `analysis.yml`, and referenced prepared
inputs in the existing [benchmark format](benchmark-format.md). An existing
`experiment.yml`, Git checkout, or preparation receipt is not required. Input
references are relative to the directory containing `cases.yml`. All referenced
manifests and selected inputs must have valid pins; generation never silently
re-signs modified source content. Use [sha-sign](sha-sign.md) when authoring inputs,
and the [task downloader](tools.md#download-harbor-tasks) to materialize
remote task sources before generation.

| Flag                 | Purpose                                                                                                           |
| -------------------- | ----------------------------------------------------------------------------------------------------------------- |
| `--benchmark-source` | Required directory containing prepared cases and their inputs.                                                    |
| `--execution`        | Required execution-settings YAML; concurrency controls scheduling.                                                |
| `--benchmark-id`     | Required identity for the generated experiment.                                                                   |
| `--output`           | Required new directory outside the source bundle.                                                                 |
| `--selection`        | Optional YAML list selecting cases, mutants, tasks, and analysis subsets.                                         |
| `--case`             | Include a complete source case; repeat for multiple cases. Defaults to all cases. Cannot accompany `--selection`. |
| `--analysis`         | Alternative analysis YAML covering every source case before selection; references resolve from the source root.   |
| `--agent-settings`   | Harbor configuration mapping merged into every included baseline and mutant.                                      |

The [example selection](../examples/smoke/selection.yml) has this shape:

```yaml
- source_case_id: smoke-case
  case_id: smoke-reduced
  mutant_ids: [nop]
  task_ids: [answer]
  subsets:
    - eval_subset_id: answer-only
      task_ids: [answer]
```

The selection document is a nonempty list. Add entries to reduce multiple cases
or create multiple variants of one source case. `case_id` defaults to
`source_case_id`; output case IDs must be unique. Selection-list order is preserved.
The baseline is always retained. `mutant_ids` and `task_ids` default to `all` and
accept explicit nonempty ID lists; source inventory order is preserved. `subsets`
defaults to `parent` to reuse the original subset document and also accepts
explicit nonempty memberships. Every subset member must be in
the selected task inventory, so `parent` fails if a retained subset refers to a
removed task. Unknown IDs, duplicates, and empty inventories are rejected.
Subsets group results for analysis; they do not schedule additional trials.

## Agent and analysis settings

`--agent-settings` uses ordinary Harbor configuration, for example:

```yaml
model_name: provider/model
kwargs:
  adapter_option: value
```

Nested mappings merge recursively; explicit values replace defaults and lists
replace lists. Unspecified values remain unchanged. The generator does not
interpret adapter-specific kwargs. Overrides can change installation settings
as well as model settings: review the resulting manifests when overriding input
paths. Named `{input:NAME}` references resolve to the copied input files. The
adapter and its host dependencies must be importable in the execution environment,
just as for a handwritten experiment; generation does not install them.

`--analysis` independently supplies the coverage command and its settings. Those
commands remain literal, with no parameter substitution or inferred provider
configuration. Keep credentials in the runtime environment rather than YAML.

For a coverage integration using a standalone author script, stage that script
separately and reference its absolute path in the analysis settings. The script
and its dependencies must be available in the execution environment.

## Generated configuration

The output contains `experiment.yml`, `cases.yml`, `execution.yml`, `analysis.yml`,
per-case manifests, and selected input copies under `inputs/`. Reference paths
are rewritten to the copies; executable input bytes remain unchanged. Source
files are never edited. A failure leaves no published output directory; existing
outputs are rejected. Use distinct destinations for concurrent invocations.

`generation.json` records input-manifest hashes and selection requests, including
defaults such as `all` and `parent`. The generated manifests contain the concrete
selected inventories. The receipt is informational, not a new requirement of the
experiment format. The ordinary runner validates the standard manifest graph;
it does not read this receipt. Absolute coverage-command paths and external
dependencies still need to exist when executing the result.
