<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Metric benchmark

An experimental benchmark for evaluating how well our coverage metric predicts
an evaluation set's effectiveness at detecting mutations in an agent. This
project outlines the architecture and develops deterministic Python tooling for
running a growing set of case examples and evaluating our metric through
mutation testing.

**This is a work in progress.** The tools and benchmark will change. Case examples
will grow, change, and may be dropped as we learn. We believe in building in plain
sight.

Prep happens outside this repository. The benchmark consumes prepared case
examples, fixed task-subset manifests, and a YAML experiment configuration.

The architecture has three phases: external prep, execution, and score analysis.
Execution and score analysis comprise four executable stages, each exposed as a
Python tool and library API:

1. **Run mutants x eval tasks:** run the baseline and every mutant against the
   benchmark tasks and save the results. The [local execution script](docs/run-evals.md) uses a
   basic Harbor loop; support for **NeMo Helix** will follow.
2. **Compute mutant scores:** measure degradation across the mutants for each
   predefined task subset.
3. **Compute coverage:** evaluate our metric on those same subsets.
4. **Correlation:** compare the paired coverage and mutant scores across subsets.

See the [architecture](docs/architecture.md) and [stage descriptions](docs/stages.md)
for how the experiment works.

For preparing bundles, use the [benchmark format specification](docs/benchmark-format.md).
For execution outputs, use the [results format specification](docs/results-format.md).

See [benchmark tools](docs/tools.md) for the command guides and configuration-loading API.
