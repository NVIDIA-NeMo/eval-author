<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Generation benchmark

An experimental benchmark for evaluating whether a task-generation process can
close coverage gaps on partial task subsets toward full-benchmark coverage. This
project outlines the architecture and develops deterministic Python tooling for
running prepared case examples, invoking generation on fixed subsets, and
measuring improvement with the same coverage metric as the
[metric benchmark](../metric-benchmark/README.md).

**This is a work in progress.** The tools and benchmark will change. Case examples
will grow, change, and may be dropped as we learn. We believe in building in plain
sight.

Prep happens outside this repository. The benchmark consumes prepared case
examples, fixed task-subset manifests, and a YAML experiment configuration with
generation adapter settings and pinned coding-agent configuration.

The architecture has three phases: external prep, execution, and analysis.
Execution and analysis comprise these stages:

1. **Execute baseline:** run the eval agent on the full task inventory and save results.
2. **Measure full benchmark:** compute coverage on that inventory.
3. **Per subset:** measure coverage, run generation, execute the eval agent on new tasks, and measure coverage again.
4. **Analyze:** compute the coverage change and gap recovery for each subset.

See the [architecture](docs/architecture.md) and [stage descriptions](docs/stages.md)
for how the experiment works.

For preparing bundles, use the [generation benchmark format](docs/benchmark-format.md).
For execution outputs, use the [results format specification](../metric-benchmark/docs/results-format.md).
For running the driver and adapters, see [benchmark tools](docs/tools.md).
