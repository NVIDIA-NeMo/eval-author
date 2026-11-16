<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Expansion continuation

Use the [shared rendering entry point](../../scripts/conversation_renderer.py)
with `stage: expansion_continue` for the unresolved post-onboarding expansion
entry decision. This slice does not approve scope, construct tasks, validate
grading, or authorize execution. Only this implemented stage is registered.
For an unselected improvement path, use the core's
[expansion entry](../../SKILL.md#expansion-handoff) before shared checks;
continuation retains an already-selected path and its unresolved decision.

## Selected audit improvements

The audit already records the recommended changes, and its handoff identifies
which ones the user selected. Reuse that report and the retained decisions;
do not offer to prepare another proposal or repeat the recommendation pass.

Check which selected changes have a supported authoring path. For an audit
tool-gap addition, continue at [task-create Step 2](../../../eval-author-task-create/SKILL.md#step-2-select-one-actionable-gap),
matching eligibility to the selected recommendation. Do not substitute an
unselected gap. Resolve only missing construction details at the existing scope
and design checkpoints, reusing applicable agreement and execution authorization.
An explicit request for additional planning keeps that scope and may use
task-create Step 1; selecting an audit recommendation does not request it.

For repairs within an existing authoring flow, use the
[repair and revalidation procedure](../task-validation.md#repair-and-retain).
The audit handoff does not add a general workflow for editing existing suite
cases or graders. Describe an unavailable implementation path as not yet supported, retaining the
audit's recommendation without offering to write it again. Do not apply that
limitation to supported authoring or repair work.

`expansion_continue` presents a saved unresolved entry question; it is not a
prerequisite to the selected authoring flow. Use it only when an unanswered
entry input prevents that work from proceeding. If a retained entry question
repeats the audit's settled selection, replace it with the actual unresolved
decision through the revision-checked update below. Preserve the lifecycle and
update its question and the top-level `pending_question` together.

## State authority

Reuse the normal reconciled state from `journey_state.py show`, never
`--state-only`. Shared prerequisites and Ethos must be complete and active
blockers resolved before this checkpoint. The expansion lifecycle is versioned
under `scopes.expand.lifecycle`; existing audit handoff, legacy context, and
other scope fields remain available alongside it. No additional state file is
introduced.

After inspecting the request and retained context, create the entry record with
`expansion_state.new_record(state, question=..., source=...)`. The question is
the actual unresolved entry decision, and its source identifies the request or
context that requires it. Save the record and the same top-level
`pending_question` through the existing revision-checked update interface.
Choose that question using the core's [expansion handoff](../../SKILL.md#expansion-handoff)
and the selected authoring flow's eligibility rules. Retain unsupported selected
improvements as context and explain their implementation limit; do not frame
them as available construction choices. Explain limits only when relevant to
the selected work; do not claim construction scope is pending when it is agreed.
Loading legacy scope text does not infer a lifecycle or approval. Introducing a
structured lifecycle retains that text as `legacy_context`.

The audit handoff leaves construction scope unconfirmed and marked for review.
Preserve those flags during entry planning; they do not invalidate the entry
record's Ethos and handoff bindings or establish scope agreement.

The record binds confirmed Ethos content and the saved audit handoff. Consume
the handoff supplied by the audit workflow; do not edit its selection from the
expansion workflow. The binding identifies saved handoff context, not fresh
audit measurements or report validity. Consult the originating audit records
before relying on their findings. This checkpoint inspects only Ethos content;
it does not inspect tasks, source traces, or native receipts.

## Rendering and recovery

After saving, reload normal state and pass the complete `state`, exact
`revision`, and `facts.expansion_observations` from the same `show` result.
`expansion_state.observe` obtains current Ethos content outside rendering;
`derive` checks supplied observations without I/O. The renderer shows the shared
checklist and saved question without a generic selection or scope preamble.

Render and present the complete response unchanged. Wrong revisions, missing
observations, mismatched pending questions, active blockers, and changed context
reject continuation. Resolve shared blockers through onboarding. Reconcile a
changed Ethos or handoff and save the relevant entry question with fresh
bindings; do not manufacture a success state or render stale decisions.
Unchanged entry decisions retain their question across normal resume and journey
switches. Shared recovery questions take precedence while prerequisites or
blockers remain unresolved. After recovery clears its question, normal state
inspection restores the retained entry question only when its bindings are
current; state-only inspection never performs that artifact reconciliation.
Successful Ethos confirmation also restores a current entry question after an
unchanged review when no active blockers remain.

Entry always remains incomplete. Additional checkpoint schemas, transition
rules, rendering, and fixtures belong to the workflow that implements them;
do not add unimplemented stages or infer execution approval from this record.
