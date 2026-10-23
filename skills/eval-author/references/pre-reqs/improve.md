<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Improvement prerequisites

Use for improving existing evaluations (`expand` in shared state). Follow the
[shared procedure](../shared-prerequisites.md#check-the-agent-and-setup) for when
to show this table, how to inspect prerequisites, and how to present results.

## Prerequisite table

Use the [onboarding renderer](../conversations/onboarding.md) for journey
`expand`. After the user's readiness reply, show `prerequisites` with its canonical
grounding sentence above the initial status table. Use only facts already in
conversation; do not read saved state or inspect the repository to populate it.
Then complete and save bounded state-only checks before target Ethos inspection.
If state is unavailable, use the [state failure response](../conversations/onboarding.md#when-state-is-unavailable).
With saved state, if blocked, render `prerequisite_results` directly as final once. If unblocked,
continue Ethos without an intermediate completed report, then render `results`
after normal reconciliation for the final completed table, Recommendations,
checklist, and Ethos action.
Table labels, order, and default statuses belong only to
[onboarding_templates.py](../../scripts/conversation_flows/onboarding_templates.py); supply findings by
row ID and retain every row.

## Checks and blocking conditions

Use the [common checks](../shared-prerequisites.md#common-checks) for agent and
repository identity, required skills, and file and command access. Required skills
are the shared, Ethos, and selected authoring skills. Accept user-created and
Eval Author-created suites without requiring prior audits, completed evaluations,
traces, coverage reports, or planning artifacts.

Confirm that the existing suite uses a supported format before checking a
provider's runtime or starting Ethos. Identifying an existing framework does not
establish compatibility; a saved Harbor setup does not establish the suite's
format. If the suite uses another format, finish the independent checks, then
record the detected suite format and required conversion in the selected scope.
Save an `evaluation_format` blocker in phase `setup`, the conversion request as
`pending_question`, and `eval-author` as `next_handoff`.

Render the blocked response through the
[onboarding renderer](../conversations/onboarding.md#blockers). For this active blocker on
journey `expand`, supply `id: evaluation_format` and `skill_link` to the exact
loaded core `eval-author/SKILL.md`, using a caller-verified absolute path or
HTTP(S) URL. The renderer owns the blocker explanation, action, question, linked
skill-pause attribution, and conversion handoff in
[onboarding_templates.py](../../scripts/conversation_flows/onboarding_templates.py). Legacy custom prose
does not override them. Present the rendering unchanged, without an additional
pause explanation or manually appended prompt. The handoff directs repository
engineering work to a separate coding-agent chat. It is a remedy within
Blockers, not a Next Steps section or permission for Eval Author to convert.

Keep setup incomplete and omit Next Steps, the checklist, and Ethos guidance.
Preserve progress while the user converts the suite; do not execute conversion,
start unsupported-format authoring, or offer separate supported evaluations as
a workaround. This requirement adds no audit or trace requirement and does not
change supported audit evidence formats or scoped trace conversion.

When the user returns with converted paths and validation results, check the
actual suite format before clearing the blocker and resuming prerequisites.
Displaying the prompt or receiving a completion claim does not establish
compatibility. Reassess this blocker when scope changes; remove its blocking
effect when it no longer applies, such as a switch to audit, while retaining
the format findings. The conversion prompt does not appear for unrelated
blockers or another journey.

Once the format is established, use the [authoring checks](../shared-prerequisites.md#authoring-checks)
for Python and the selected evaluation runtime, the applicable evaluation
environment, required services and data, and agent connection and credentials.
Read versions and capabilities from [runtime-policy.json](../../scripts/runtime-policy.json).

An unavailable, incompatible, or unresolved selected runtime or Python environment
blocks this flow before Ethos and case planning. Follow the shared authoring
checks to save the blocker, explain the required action, and resume after rechecking.

Keep later environment, service, data, and credential requirements in the table
with the work they block and when they must be resolved. Pause the dependent work
when needed. Missing information or access required for the next Ethos action
remains a blocker under the shared procedure.
