<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Metric benchmark

**Draft for review.** This directory specifies the architecture of an external
benchmark for testing whether coverage measurements predict how well an
evaluation set detects deliberately weakened agents. This base PR contains the
design and figures. Separate implementation PRs build the stage tools on top:
[subset preparation (#94)](https://github.com/NVIDIA-NeMo/eval-author/pull/94),
[mutant scoring (#95)](https://github.com/NVIDIA-NeMo/eval-author/pull/95),
[coverage computation (#96)](https://github.com/NVIDIA-NeMo/eval-author/pull/96),
[correlation (#97)](https://github.com/NVIDIA-NeMo/eval-author/pull/97), and
[orchestration and packaging (#99)](https://github.com/NVIDIA-NeMo/eval-author/pull/99).
The tool guide in that implementation stack documents available commands and
current limits. Execution uses a shared results directory, with a local Harbor
loop as the initial backend and Helix planned later.

Start with the [architecture](architecture.md) for the supplied inputs, component
boundaries, and experiment flow. The [stage descriptions](stages.md) explain
execution, scoring, and completion criteria.

A fixed benchmark is a specific `benchmark.yaml` and its versioned input
artifacts. Prep builds those artifacts and emits the YAML definition. The
orchestrator reads it and invokes the stage tools through their Python APIs;
the same tools can also be used independently from the command line. Versioned
configuration variants support controlled comparisons of experimental designs.

The supplied inputs are a baseline agent, its mutants, a case description,
benchmark tasks with a fixed subset manifest, and an experiment configuration. Building the case packages
happens before a benchmark release. The system under evaluation is supplied
separately: Eval Author or another system that measures coverage.

The architecture has three stages:

1. **Prep:** prepare case examples for the experiment, including the agent,
   mutants, case description, tasks, and configuration. Construct subsets with a
   fixed seed and bundle their explicit memberships in a versioned manifest.
   This work is outside the experiment.
2. **Run mutants x eval tasks:** every task runs against the baseline and each mutant. The initial
   implementation is a simple local Harbor loop, processing one agent at a time;
   this is expected to be expensive. A future Helix implementation will provide
   orchestration for evaluation at scale.
3. **Score analysis:** most of the tools here consume the execution results,
   use the prepared subset manifest, compute mutant scores and coverage, and correlate them.

Execution and analysis share a [results-directory contract](stages.md#results-directory-contract).
The proposed layout groups native results by agent, then benchmark run, with a
common index for looking up individual task executions. Coverage scores and any
rubric the system generates are experiment outputs, not supplied benchmark inputs.

The analysis runner is deterministic program code. Its control flow, validation,
and output handling work the same whether a person, an agent, or another program
invokes it. These documents describe program behavior and interfaces; execution
does not depend on an agent interpreting the prose. Model use belongs to the
system being evaluated. See the
[benchmark orchestration](architecture.md#benchmark-orchestration).

This draft adapts the metric efficacy experiment design from the fixtures
repository, as present at revision `f1f72b7a6a5085f9e369905417b3171f30879b92`.
It retains the scientific question and the execution-to-analysis artifact flow.
Preparation, publication, service-specific identifiers, and internal execution
infrastructure are outside this version's runtime design. The
[tool guide](https://github.com/NVIDIA-NeMo/eval-author/blob/feat/metric-benchmark-record/labs/metric-benchmark/README.md#current-implementation-limits) describes current implementation limits.
