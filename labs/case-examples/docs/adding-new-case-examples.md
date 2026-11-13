<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Adding new case examples

## Overview

This document describes how to add the artifacts used to construct case examples:

- **Agents:** a baseline implementation and its associated mutants.
- **Task sets:** collections of Harbor tasks, each with its environment and verifier.

We prefer established, realistic agents and task sets with evidence that they are
useful. These give us a starting point for evaluating how effectively we generate
evaluation tasks for real agents, relative to existing task sets. Newly authored
agents and tasks are welcome, but identifying and incorporating useful existing
examples is the priority.

Agents and task sets are reusable independently. A case example pairs them with
analysis settings, including fixed subsets. Follow the
[case example format](benchmark-format.md) for their directories and manifests.

### How the experiments use these artifacts

The **metric experiment** takes an agent and an existing task set, measures our
metric on predefined subsets, and compares those scores with the subsets'
ability to detect agent mutations. This evaluates whether the metric identifies
useful evaluation sets. It does not evaluate task generation.

The **generation experiment** starts with an agent and a subset of an existing
task set, then generates additional tasks to recover coverage toward the full
set. The broader question is whether we can replace omitted tasks with tasks
that are equally effective, even if they are different. Coverage improvement is
the measured signal; interpreting it as improved effectiveness depends on the
quality of the metric. See the [overview](overview.md#our-approach) for both
experiment descriptions.

## Adding an agent and its mutants

1. **Identify the baseline.** Record its upstream source and exact version, and
   briefly explain what the agent does and why it is a useful benchmark subject.
2. **Check publication rights.** Before including source code, confirm that its
   license and other restrictions permit the intended modification and
   redistribution. Preserve required license and attribution notices, including
   those for bundled third-party material. Follow the repository's
   [contribution and IP review requirements](../../../CONTRIBUTING.md#sign-off-and-contribution-license).
3. **Prepare the agent bundle.** Follow the [agent format](benchmark-format.md#agents),
   keeping the baseline and each mutant in separate directories. Include the
   dependencies, configuration, and setup instructions needed to execute them
   through the specified Harbor integration.
4. **Create and describe the mutants.** Each mutant is a version of the baseline
   with an intentional regression: for example, a removed tool, incorrect
   behavior, missing functionality, or slower execution. Mutants can be created
   by hand or through automation, including tools tailored to that agent. Add a
   short Markdown description alongside each mutant explaining what changed and
   which behavior or performance characteristic is intended to degrade.
5. **Check that they can run.** Verify that the baseline and mutants can be set up
   and invoked using the supplied instructions. Record what was checked and any
   required external services. An intended behavioral regression should be
   distinguishable from an accidental packaging or setup failure.

This adapts [mutation testing](https://en.wikipedia.org/wiki/Mutation_testing):
we deliberately change an agent and ask whether evaluation tasks detect the
resulting regressions. The experiment needs executable versions and understandable
changes so that differences in outcomes can be interpreted. Detailed scientific
qualification criteria remain to be defined.

## Adding a task set

Prefer an existing Harbor benchmark at a specific version. The experimental
[remote preparation workflow](benchmark-format.md#remote-datasets-experimental)
accepts a Harbor JSON-registry dataset or individually pinned Git tasks without
checking their contents into this repository. Prepare the complete experiment
before execution and retain the resulting bundle with its source receipt. The
runner consumes the generated local task manifests.

Checking in a task set is also supported, particularly for the first public
release of a previously internal benchmark. Confirm publication rights and
complete the same applicable review described above, including for task data and
other bundled assets. Follow the [task benchmark format](benchmark-format.md#task-benchmarks)
to identify the included tasks and their versions, provide runnable environments
and verifiers, and retain explicit subset memberships for analysis. Include the
setup instructions needed to prepare and execute the tasks, and explain why this
task set provides a useful starting point for the experiments.
