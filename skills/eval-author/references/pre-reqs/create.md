<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Creation prerequisites

Use for building first evaluations. Follow the [shared procedure](../shared-prerequisites.md#check-the-agent-and-setup)
for when to show this table, how to inspect prerequisites, and how to present results.

## Prerequisite table

| Prerequisite | Status | Findings |
|---|---|---|
| Agent and repository | Checking | — |
| Evaluation framework | Checking | — |
| Required skills | Checking | — |
| File and command access | Checking | — |
| Python and evaluation runtime | Checking | — |
| Evaluation environment | Checking | — |
| Required services and data | Checking | — |
| Agent connection and credentials | Needed before agent runs | — |

## Checks and blocking conditions

Use the [common checks](../shared-prerequisites.md#common-checks) for agent and
repository identity, required skills, and file and command access. Required skills
are the shared, Ethos, and selected authoring skills.

Establish the intended NeMo Gym or Harbor format using the shared setup-selection
rules. A first suite needs an intended supported format, not an existing task.
No existing suite, completed evaluations, traces, prior audit, coverage report, or
planning artifact is required. Accept a stated absence without forcing discovery.

Use the [authoring checks](../shared-prerequisites.md#authoring-checks) for Python
and the selected evaluation runtime, the applicable evaluation environment,
required services and data, and agent connection and credentials. Read versions
and capabilities from [runtime-policy.json](../../scripts/runtime-policy.json).

Keep later execution requirements in the table with the work they block and when
they must be resolved. Missing runtime, environment, service, or credential access
does not block independent planning and Ethos; pause the dependent authoring or
execution step when needed. Missing information or access required for the next
Ethos action remains a blocker under the shared procedure.
