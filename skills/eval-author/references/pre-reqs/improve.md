<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Improvement prerequisites

Use for improving existing evaluations (`expand` in shared state). Follow the
[shared procedure](../shared-prerequisites.md#check-the-agent-and-setup) for when
to show this table, how to inspect prerequisites, and how to present results.

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
are the shared, Ethos, and selected authoring skills. Accept user-created and
Eval Author-created suites without requiring prior audits, completed evaluations,
traces, coverage reports, or planning artifacts.

Confirm that the existing suite uses a supported format before checking a
provider's runtime or starting Ethos. Identifying an existing framework does not
establish compatibility; a saved Harbor setup does not establish the suite's
format. If the suite uses another format, finish the independent checks and show
this under **Blockers**:

```markdown
Eval Author only supports NeMo Gym or Harbor formats. Convert your existing evaluations to one of those formats before we continue.
```

Keep setup incomplete and omit Next Steps, the checklist, and Ethos guidance
until the user returns with a supported suite. Save progress and wait for them to
convert it; conversion remains outside Eval Author. Do not offer direct authoring
in the unsupported format or separate supported evaluations as a workaround.
This requirement adds no audit or trace requirement and does not change supported
audit evidence formats or scoped trace conversion.

Record the detected suite format and required conversion in the selected scope,
with an `evaluation_format` blocker in phase `setup`, the conversion request as
`pending_question`, and `eval-author` as `next_handoff`. On return, check the
converted suite's format before clearing the blocker and resuming prerequisites.
Reassess this blocker when the scope changes; remove its blocking effect when it
no longer applies to the new request, such as a switch to audit, while retaining
the format findings.

Once the format is established, use the [authoring checks](../shared-prerequisites.md#authoring-checks)
for Python and the selected evaluation runtime, the applicable evaluation
environment, required services and data, and agent connection and credentials.
Read versions and capabilities from [runtime-policy.json](../../scripts/runtime-policy.json).

Keep later execution requirements in the table with the work they block and when
they must be resolved. Missing runtime, environment, service, or credential access
does not block independent planning and Ethos; pause the dependent authoring or
execution step when needed. Missing information or access required for the next
Ethos action remains a blocker under the shared procedure.
