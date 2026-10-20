<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Metric benchmark

**Draft for review.** This directory describes an external benchmark for testing
whether Eval Author's coverage measurements predict how well an evaluation set
detects deliberately weakened agents. It contains a proposed design, not an
implemented runner or a completed experiment.

Start with the [architecture](architecture.md) for the supplied inputs, component
boundaries, and experiment flow. The [stage descriptions](stages.md) explain
execution, scoring, and completion criteria.

The benchmark comes with prepared case examples: a baseline agent, its mutants,
and an associated task set. Building those inputs happens before a benchmark
release. A run starts with those inputs and an explicit experiment configuration,
executes the agent-by-task matrix, then analyzes the resulting evidence.

The intended runner is deterministic program code. The distinction between
deterministic orchestration and repeatable model outputs remains explicit in the
[determinism requirements](architecture.md#determinism).

This draft adapts the metric efficacy experiment design from the fixtures
repository, as present at revision `f1f72b7a6a5085f9e369905417b3171f30879b92`.
It retains the scientific question and the execution-to-analysis artifact flow.
Preparation, publication, service-specific identifiers, and internal execution
infrastructure are outside this version's runtime design. The
[open decisions](architecture.md#open-decisions) identify where the external
benchmark contract still needs work.
