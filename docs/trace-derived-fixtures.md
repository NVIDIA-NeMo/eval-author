<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Trace-derived tool-call access

**Experimental:** Trace-derived fixtures are part of the trace-to-environment
workflow. The [complete guide](../skills/eval-author-trace-environment/references/trace-derived-fixtures.md)
is bundled with the skill so it is available after installation.

For runtime setup and checks, follow
[Evaluation integration](../skills/eval-author-trace-environment/references/trace-derived-fixtures.md#evaluation-integration).

## Stateful example

The [Workplace email prototype](../skills/eval-author-trace-environment/examples/workplace/README.md) supplies a bounded stateful simulation, a separate-verifier Harbor task, and held-out source-conformance checks.
It uses synthetic records and keeps simulation separate from the existing exact-replay access contract.

## Regression checks

Development tests require a source checkout and its prepared uv environment.
See [Regression checks](../skills/eval-author-trace-environment/references/trace-derived-fixtures.md#regression-checks)
in the canonical guide. They are separate from the installed skill's runtime
checks and task proof.
