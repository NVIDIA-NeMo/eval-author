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

To run the reference generator on the smoke benchmark, with a scripted driver in place of the coding agent:

```bash
PYTHONPATH=labs/generation-benchmark:labs/metric-benchmark uv run --locked python -m generator.build
# Set generation.settings.image_id in examples/smoke/generation-experiment-reference.yml to the printed id.
uv run --locked labs/generation-benchmark/tools/sign-generation \
  labs/generation-benchmark/examples/smoke/generation-experiment-reference.yml --resign
uv run --locked python labs/generation-benchmark/run_generation_experiment.py \
  --benchmark labs/generation-benchmark/examples/smoke/generation-experiment-reference.yml \
  --run-id smoke-ref-1 --work-root /tmp/gen-smoke-ref
```

After you edit the four skill trees or the code the image copies to `/opt/generation/`, rebuild the image, update `image_id`, and re-sign.

## Roadmap

Details are in [deferred](docs/architecture.md#deferred).

