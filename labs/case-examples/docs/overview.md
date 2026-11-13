<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Case examples for evaluating evaluation generation

## The problem

We have not found suitable benchmarks for evaluating either the metrics used by
Eval Author or its ability to generate effective evaluations. Without them, it is
hard to compare generation methods or tell whether our own changes improve the
results. We rely heavily on intuition and user experience, which are expensive
and slow sources of feedback.

A conventional benchmark collects tasks with verifiers, as in Terminal-Bench and
other Harbor benchmarks. Here, we are evaluating the ability to write evaluations.
Generation can take hours or days, and testing the resulting evaluations inside
a Harbor task could require running Harbor inside Harbor. More fundamentally,
the usefulness of an evaluation set is not a simple correctness check. An LLM's
judgment of a generated evaluation is not sufficient evidence that it will detect
meaningful failures. We need experiments that measure that usefulness.

## Our approach

We adapt mutation testing from software engineering: introduce changes into an
agent, then measure how effectively evaluation tasks detect the resulting
behavioral degradation. This gives us a signal against which to assess an
evaluation-quality metric. We currently distinguish two experiments:

- **[Metric evaluation](../../metric-benchmark/docs/architecture.md):** compare a
  metric's scores with mutant sensitivity across predefined task subsets.
- **[Generation evaluation](https://github.com/NVIDIA-NeMo/eval-author/blob/f54a35f0119c71b707f096d48e5ff9025f7c7296/labs/generation-benchmark/README.md):**
  compare how generation methods improve coverage from predefined starting
  subsets. Its interpretation depends on the quality of the metric being used.

Both start with agents and predefined task sets. The shared ingredients are an
agent baseline, its mutants, and a task benchmark. A full case example combines
these with analysis settings, including fixed task subsets. The experiments use
these ingredients differently: metric evaluation uses the mutants to assess the
metric, while generation evaluation uses the baseline and starting subsets to
assess generation. Other experiments may follow.

A benchmark is a collection of these case examples. An experiment adds the
execution configuration; see the [case example format](benchmark-format.md) and
[experiment runner](../../metric-benchmark/docs/run-experiment.md) for the existing
definitions and execution details.

The purpose of this collection is to provide well-formed, validated, versioned
agents, mutants, and task sets that we can extend consistently. Where preparation
uses randomization, we retain the chosen memberships and seeds so comparisons
use the same inputs across runs. This controls avoidable variation without
assuming model execution is deterministic. As the collection evolves, explicit
versions let us preserve comparable inputs or identify when results are no
longer directly comparable.

## Current case examples and task sets

This is an early alpha benchmark. No case examples or task sets are listed here
yet.

**TO DO: ADD THE CASE EXAMPLES AND TASK SETS INCLUDED IN THIS BENCHMARK.**
