<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Generation benchmark

**How much of a subset's metric gap can a task generator close?**

The benchmark runs case examples for one generator command and one metric scoring command. A case is one eval agent, one task inventory, and that case's predefined subsets. Each case reports `Rᵢ` for its subsets, then the median of the defined values. [Architecture](docs/architecture.md#cases) defines a case.

## Documentation

- [Architecture](docs/architecture.md) defines the flow and the contracts.
- [Stages](docs/stages.md) defines the stage contract.
- [Benchmark format](docs/benchmark-format.md) defines the YAML configs.

## Reference implementation

The experiment takes a metric and a generator command. The metric names a score command and an optional setup command. v0 supplies both in the [reference generator implementation](docs/reference-generator-implementation.md).

## Roadmap

Details are in [deferred](docs/architecture.md#deferred).

