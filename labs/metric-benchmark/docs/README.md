<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Metric benchmark

**Draft for review.** This directory describes an external benchmark for testing
whether a system's coverage measurements predict how well an evaluation set
detects deliberately weakened agents. It contains a proposed design, not an
implemented runner or a completed experiment.

Start with the [architecture](architecture.md) for the supplied inputs, component
boundaries, and experiment flow. The [stage descriptions](stages.md) explain
execution, scoring, and completion criteria.

The supplied inputs are a baseline agent, its mutants, a case description,
benchmark tasks, and an experiment configuration. Building the case packages
happens before a benchmark release. The system under evaluation is supplied
separately: Eval Author or another system that measures coverage.

A run executes tasks against the baseline and mutants, obtains coverage scores
from the system under evaluation, and analyzes coverage versus mutant scores
across task subsets. Coverage scores and any rubric the system generates are
experiment outputs, not supplied benchmark inputs.

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
