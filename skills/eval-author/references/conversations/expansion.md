<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Expansion entry and scope

Use the [shared renderer](../../scripts/conversation_renderer.py) with
`expansion_continue` for an unresolved entry question or `expansion_scope` for
construction scope. Follow the [shared rendering contract](../conversation-rendering.md);
fixed copy lives in [expansion_templates.py](../../scripts/conversation_flows/expansion_templates.py).
Supply normal reconciled state, its exact revision, and the same view's
`facts.expansion_observations`, then present the complete response unchanged.

For a general request without a selected path or active decision, use the core's
[expansion entry](../../SKILL.md#expansion-handoff). A selected path supplies
direction and readiness; ask only for unresolved inputs.

## Selected audit improvements

Consume `scopes.expand.audit_handoff` and its retained report, selection IDs,
limits, and provenance without editing the handoff. The audit already supplies
recommendations: do not offer another proposal pass. Additional planning uses
task-create Step 1 only when explicitly requested.

For selected additions, continue at [task-create Step 2](../../../eval-author-task-create/SKILL.md#step-2-select-one-actionable-gap).
Each task must match an eligible measured tool gap and a selected `new_task`
recommendation whose `items` include that tool. Reuse the selector's exact
identity and paths. Missing evidence or an empty selector stays visible; do not
substitute unselected gaps or relabel capability/failure-case recommendations.

Keep unsupported selected changes visibly deferred with their implementation
limit. Applying arbitrary revisions to existing cases or graders is not yet
supported; [repairs within existing authoring workflows](../task-validation.md#repair-and-retain)
remain available. Findings acceptance and recommendation selection do not
approve construction.

| Supported path | Existing workflow |
| --- | --- |
| `starter_suite` | [Starter expansion](../../../eval-author-first-eval/SKILL.md#starter-expansion), reusing the provider, source selection, and a current reviewed or accepted task pattern. No audit is required. |
| `audit_tool_gap` | Task-create's measured selector. Agree eligible tasks together and construct them one at a time; retain remaining agreed tasks as pending and unsupported work as deferred. |
| `recorded_interaction` | Task-create's experimental [trace-source mode](../../../eval-author-task-create/SKILL.md#trace-source-mode) and [trace handoff](../trace-input.md). Select the source before inspection and preserve privacy, access, admission, and qualification gates. No audit or full prerequisite/Ethos interview is added. |

## Saved entry and scope records

Use only `scopes.expand.lifecycle` in shared journey state. The existing
`schema_version: 1` record stores an unresolved entry question, not a product
version. Keep it resumable with `expansion_continue`; do not insert an extra
pause for an already-selected path. Upgrading preserves that record in
`scopes.expand.entry_history`, alongside retained legacy and sibling context.

Use `expansion_state.new_scope` for `schema_version: 2`. Its
[validator](../../scripts/expansion_state.py) owns the exact record shape. Save
the complete lifecycle through `journey_state.py update --expected-revision`
from the latest normal `show`; lifecycle updates are not recursive patches.

| Saved content | Required meaning |
| --- | --- |
| Request and path | Actual requested outcome and provenance, using a supported path above |
| Task plan and selection | Scenario, expected outcome, grading, validation, stable example IDs, planned attempts, unresolved inputs, and applicable recommendation/tool IDs; preserve the exact selected subset |
| Sources | Selected repository-relative files/task trees and current digests; bind the measured report for audit creation and the reviewed pattern for starter expansion |
| Deferred work and exclusions | Every selected audit improvement remains selected or deferred with a reason; preserve corpus filters and exclusions |
| Limits and pending inputs | Construction attempt/time/cost caps and their provenance; unresolved choices remain explicit |
| Decisions | Current intent/handoff bindings, presentation receipt, actual user answer, and retained decision history |

Apply [coverage planning](../coverage-planning.md) for example breadth, source
selection, and counting units. Planned agent attempts repeat a Harbor task or
selected Gym row and grant no execution permission. Construction attempts are
separate, including the initial attempt and at most three repairs. A null
time/cost cap means explicitly uncapped only after its limit questions are
settled; preserve existing limits rather than silently replacing them.

## Present and record the decision

Save the proposed scope and actual pending question, reload, and render
`expansion_scope`. After presenting it, save
`presented: {digest: scope_digest(state), source: <actual presentation>}` in a
revision-checked update. On the user's later answer, save
`decision: {digest, kind, source}` for that exact scope; `kind` is `approved`,
`declined`, or `revise`. Do not combine a new presentation and answer in one
update or reuse request, audit selection, or presentation provenance as an answer.

Approval requires supported selected tasks, current evidence/context, settled
inputs/limits, and applicable prerequisites. Approval or decline clears both
lifecycle `pending` and top-level `pending_question`; revision saves the actual
next question in both. Decline retains the plan and limits. For revision, keep
the old receipt, edit affected content, present the revised scope, and obtain
its answer; replacement decisions are archived automatically. A changed scope
cannot reuse its old approval, and a subset cannot silently replace the agreed set.

Before generating each selected task's instructions, fixtures, or scaffolds,
reload state and run:

```bash
python <core_dir>/scripts/journey_state.py --repo <repo> check-expansion-scope \
  --task-id <selected-task-id> --expected-revision <latest-revision>
```

This read-only guard returns the current task, scope digest, limits, and owning
workflow with `execution_authorized: false`. Pass that state to existing
authoring without another planning document or unchanged scope question. A
failed guard pauses dependent construction; it tracks neither spent attempts
nor native validation. Trace source preparation/admission retain their separate
[ordering and authority](../trace-input.md).

## Report follow-through

While audit-derived construction depends on its saved findings, keep the bound
report/content and audited suite configuration unchanged. Retain interim choices
and pending work in the existing expansion scope and conversation. Construct
new tasks at stable paths and perform their required checks first; refreshing
the report or editing audited membership between tasks would stale the next guard.

When that construction scope finishes or the user ends it, use the audit's
[existing report follow-through](../../../eval-author-audit/references/coverage-report.md#selected-improvement-handoff-and-follow-through)
for delivered/pending work and historical-measurement limits. A report refresh
preserves selection provenance but does not refresh construction approval;
further work requires reconciliation of affected scope. Standalone proposal
and report workflows keep their existing timing.

## Reconciliation and resume

`observe` delegates audit freshness/selection to its owner and tool eligibility
to task-create; `derive` uses supplied observations without I/O. Missing or
changed bound inputs block construction. Show the saved scope and affected
inputs, then reconcile their bindings before obtaining a new answer. Preserve
partial artifacts, decision history, unrelated scopes, and reusable evidence.

Unchanged resume keeps selections, limits, answers, and the actual pending
question across chats and journey switches. Approved/declined scopes restore no
stale question. Shared [prerequisite/Ethos recovery](../shared-prerequisites.md)
takes precedence in the full flow while retaining scope context; unchanged
intent restores its applicable question. The recorded-interaction path keeps
its narrower trace entry.

## Checkpoint ownership

| Checkpoint | Owner | Advancement authority |
| --- | --- | --- |
| Entry and supported scope | ASE-1425 | Explicit agreement on current supported tasks and limits |
| Creation and validation progress | ASE-1426 | Agreed scope, current task artifacts, and native evidence |
| Task review and execution authorization | ASE-1427 | Separate review and permission for exact tasks, agent, and settings |
| Execution, results, and continuation | ASE-1428 | Authorized actual-agent evidence |

Only `expansion_continue` and `expansion_scope` are registered here. Scope
derives `complete: false`; construction approval establishes no validation,
agent execution, or completed journey. Follow the shared rendering contract
when extending these checkpoints.
