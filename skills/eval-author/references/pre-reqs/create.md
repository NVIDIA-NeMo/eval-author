<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Creation prerequisites

Use for building first evaluations.

## Prerequisite table

Journey `create`; follow the [shared prerequisite procedure](../shared-prerequisites.md#check-the-agent-and-setup).

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

An unavailable, incompatible, or unresolved selected runtime or Python environment
blocks this flow before Ethos and case planning. Follow the shared authoring
checks to save the blocker, explain the required action, and resume after rechecking.

Keep later environment, service, data, and credential requirements in the table
with the work they block and when they must be resolved. Pause the dependent work
when needed. Missing information or access required for the next Ethos action
remains a blocker under the shared procedure.
