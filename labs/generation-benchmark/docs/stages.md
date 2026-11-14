<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Experiment stages

The architecture has three phases: external prep, execution, and analysis.
`run_generation_benchmark.py` runs the stages below for one selected `case_id`.
Subset ids come from the pinned `subsets.json`. The driver does not resample
at runtime.

**Prep** builds the case, task inventory, subset manifest, baseline agent,
coverage command, generation command, and generation sidecar inputs outside
this repository. Runs consume a bundle in the
[generation benchmark format](benchmark-format.md).

| Stage | Tool |
| --- | --- |
| Execute baseline | `run_generation_benchmark.py` (`execute_generation_baseline`) |
| Measure full benchmark **M(B)** | same driver (`measure_baseline_full`) |
| Measure subset **M(Bᵢ)** | same driver (`measure_subset`) |
| Generate **Bᵢ⁺** | `eval_author_generate.py` (`invoke_generation`) |
| Execute new tasks | same driver (`execute_new_tasks`) |
| Measure expanded subset **M(Bᵢ⁺)** | same driver (`measure_subset`) |
| Analyze **ΔMᵢ** and **Rᵢ** | same driver (`analyze_generation`) |

`coverage.settings.before_measure_command`, when set, runs before measurement.
It is an external argv, not a library stage.

## Stage contract

Each stage has declared inputs, outputs, and completion criteria. Outputs
become available downstream only after validation. The driver stops on error.
Coverage stages reuse a completed request when its receipt matches. Changed
inputs require a new work root.

Execution writes the shared [results format](../../metric-benchmark/docs/results-format.md)
(`run_id`, `task_inventory`, `execution_results`). Generation writes
`request.json`, `response.json`, and `receipt.json` under each subset work cell.
`generation-results.json` records the generation mode and one receipt summary
per subset. `generation-analysis.json` records **ΔMᵢ**, **Rᵢ**, and the cohort
summaries from [architecture](architecture.md).

## 1. Execute baseline

**Purpose:** run the eval agent **A** on the full task inventory **B** and
produce the results directory.

**Inputs:** prepared case, baseline agent bundle, and Harbor execution settings.

**Outputs:** case-level `results.json` under `execution-results/`.

Coverage needs evaluation evidence from these trials and from later runs on
generated tasks. The reference driver always runs this stage.

## 2. Measure the full benchmark

**Purpose:** compute **M(B)** on the full inventory from baseline evidence.

**Outputs:** scores under `coverage-baseline/`. **M(B)** is the denominator for
**Rᵢ**.

## 3. Per-subset loop

For each `eval_subset_id` in the pinned manifest, in order:

### Measure the subset

**Purpose:** compute **M(Bᵢ)** and stage that subset's task trees.

The staged suite includes that subset's tasks. It excludes tasks from the rest
of **B**, and the request excludes **M(B)**. The coverage work cell is copied
through unchanged.

### Generate

**Purpose:** run **G**, driven by coding agent **C**, to produce **Bᵢ⁺**.

**Inputs:** staged `request.json`. It names the subset suite, the coverage work
cell, and the output suite, and it carries the subset coverage score, the case
description, and the generation settings. `settings.draft_dir` names where
**G** deposits new Harbor task directories, relative to the generator work cell.
The reference adapter reads `authored/audit.md` and `report.json` from the
coverage work cell. The driver does not.

**Outputs:** expanded suite, `response.json`, and `receipt.json`. The reference
adapter copies each draft from `draft_dir` into the expanded suite. Generator
logs may appear under `.eval-author/generator-logs/`. A subset with no new
draft still returns a receipt. The driver records that in
`generation-results.json` and continues.

### Execute new tasks

**Purpose:** run **A** on the copied new task ids and merge those rows into the
case `results.json`. Skipped when the subset added no tasks.

### Measure the expanded subset

**Purpose:** compute **M(Bᵢ⁺)** from the expanded evidence.

The driver then merges the generated tasks into the shared inventory before
the next subset.

## 4. Analyze

**Purpose:** compute per-subset **ΔMᵢ** and **Rᵢ**, plus cohort summaries, and
write `generation-analysis.json`.

When **M(B) − M(Bᵢ) = 0**, **Rᵢ** is null with reason `zero_gap`. See
[architecture](architecture.md).
